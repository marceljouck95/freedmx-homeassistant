"""Nativer freeDMX-AP-Client (UDP, Protokoll laut freedmxprotocol.txt)."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
import logging

from homeassistant.core import HomeAssistant

from .protocol import (
    ACK_TIMEOUT,
    ACK_WORD,
    FRAME_CHANNELS,
    FRAME_INTERVAL,
    HANDSHAKE,
    HANDSHAKE_REPLIES,
    TERMINATE,
    build_datagrams,
)

_LOGGER = logging.getLogger(__name__)

HANDSHAKE_INTERVAL = 1.0
PING_AFTER = 1.5


class FreeDmxDevice(asyncio.DatagramProtocol):
    """Hält den 512-Kanal-Puffer und sendet ihn im 40-ms-Takt an das freeDMX."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        bind_ip: str | None,
    ) -> None:
        self._hass = hass
        self.host = host
        self.port = port
        self.bind_ip = bind_ip
        self._values = bytearray(FRAME_CHANNELS)
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        self._connected = False
        self._last_seen = 0.0
        self._last_handshake = -HANDSHAKE_INTERVAL
        self._listeners: list[Callable[[], None]] = []
        self._addr: tuple[str, int] | None = None
        self.last_reply: str | None = None
        # Raum-Begrenzung: Funktionen (puffer, erster, letzter Kanal) -> geändert?
        self.guards: list[Callable[[bytearray, int, int], bool]] = []
        self._value_listeners: list[Callable[[int, int], None]] = []
        self.free_lamps: set[str] = set()  # Lampen, deren Begrenzung gerade ausgesetzt ist
        self._enabled = True  # False = getrennt: es wird nichts gesendet (freeDMX frei für andere Apps)

    # ---------- Zustand ----------
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """Verbindung von Hand herstellen (True) oder trennen (False).

        Getrennt sendet Home Assistant nichts mehr an das freeDMX; die Kanalwerte
        bleiben gemerkt und werden beim Verbinden wieder gesendet.
        """
        value = bool(value)
        if value == self._enabled:
            return
        self._enabled = value
        if value:
            self._last_handshake = -HANDSHAKE_INTERVAL
            self._last_seen = 0.0
        else:
            if self._transport and self._addr:
                self._transport.sendto(TERMINATE, self._addr)
            self._set_connected(False)

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def _set_connected(self, value: bool) -> None:
        if value == self._connected:
            return
        self._connected = value
        _LOGGER.warning(
            "freeDMX %s:%s %s",
            self.host,
            self.port,
            "verbunden" if value else "nicht erreichbar",
        )
        for callback in list(self._listeners):
            callback()

    # ---------- Kanäle ----------
    def get_channels(self, start: int, count: int) -> list[int]:
        idx = start - 1
        return list(self._values[idx : idx + count])

    def set_channels(self, start: int, values: list[int]) -> None:
        """Werte ab Kanal start (1-basiert) setzen. Der Sendetakt übernimmt den Rest."""
        idx = start - 1
        if idx < 0 or idx + len(values) > FRAME_CHANNELS:
            raise ValueError("Kanalbereich außerhalb 1-512")
        for offset, value in enumerate(values):
            self._values[idx + offset] = max(0, min(255, int(value)))
        first, last = start, start + len(values) - 1
        for guard in self.guards:
            guard(self._values, first, last)
        for listener in list(self._value_listeners):
            listener(first, last)

    def add_value_listener(self, callback: Callable[[int, int], None]) -> Callable[[], None]:
        """Benachrichtigung bei geänderten Kanälen (erster, letzter; 1-basiert)."""
        self._value_listeners.append(callback)

        def remove() -> None:
            if callback in self._value_listeners:
                self._value_listeners.remove(callback)

        return remove

    # ---------- Start / Stop ----------
    async def async_start(self) -> None:
        loop = self._hass.loop
        infos = await loop.getaddrinfo(
            self.host, self.port, family=socket.AF_INET, type=socket.SOCK_DGRAM
        )
        self._addr = infos[0][4][:2]
        # Kein festes Gegenüber: Antworten werden von jedem Absenderport angenommen.
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=(self.bind_ip or "0.0.0.0", 0),
            family=socket.AF_INET,
        )
        self._task = self._hass.async_create_background_task(
            self._run(), name=f"freedmx_{self.host}_{self.port}"
        )

    async def async_stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._transport:
            if self._connected:
                self._transport.sendto(TERMINATE, self._addr)
            self._transport.close()
            self._transport = None
        self._set_connected(False)

    # ---------- Sendeschleife ----------
    async def _run(self) -> None:
        loop = self._hass.loop
        while True:
            if not self._enabled:
                await asyncio.sleep(0.2)
                continue
            now = loop.time()
            if self._connected and now - self._last_seen > ACK_TIMEOUT:
                self._set_connected(False)

            # Wie QLC+: Daten laufend senden. Beobachtet: das Gerät antwortet im
            # Heimnetz nur auf den Handshake, deshalb dient er als Abfrage,
            # sobald 1,5 s lang nichts kam (höchstens 1x pro Sekunde).
            if (
                now - self._last_seen > PING_AFTER
                and now - self._last_handshake >= HANDSHAKE_INTERVAL
            ):
                self._last_handshake = now
                self._transport.sendto(HANDSHAKE, self._addr)

            for datagram in build_datagrams(self._values):
                self._transport.sendto(datagram, self._addr)
            await asyncio.sleep(FRAME_INTERVAL)

    # ---------- Empfang (nur Lebenszeichen) ----------
    def datagram_received(self, data: bytes, addr) -> None:
        if self._addr is None or addr[0] != self._addr[0] or not self._enabled:
            return
        # Wie QLC+: jedes Paket vom freeDMX gilt als Lebenszeichen
        # (beobachtet: F0 64, A7, AA und A0).
        self.last_reply = data.hex()
        if data != ACK_WORD and data not in HANDSHAKE_REPLIES:
            _LOGGER.debug("Paket von %s: %s", addr, data.hex())
        self._last_seen = self._hass.loop.time()
        self._set_connected(True)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP-Fehler freeDMX: %s", exc)
