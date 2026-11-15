"""Bewegungsmuster abspielen (läuft in Home Assistant, ~25 Bilder/s)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .geometry import Mount, Room, aim_percent, clamp_point
from .patterns import MODES, PATTERNS, point, to_room
from .protocol import parse_layout, percent_to_16bit
from .scenes import LAMP_MOUNT, position_writes

OFF = "aus"
STEP = 0.04


class PatternRunner:
    def __init__(self, hass: HomeAssistant, device, lamps: list[dict], room: dict | None) -> None:
        self._hass = hass
        self._device = device
        self._lamps = [
            l for l in lamps
            if LAMP_MOUNT in l and any(t in ("P", "T") for t in parse_layout(l["layout"]))
        ]
        self._room = Room.from_dict(room)
        self.pattern = OFF
        self.mode = "gleich"
        self.tempo = 40.0   # 1…100
        self.size = 70.0    # 10…100 %
        self._phase = 0.0
        self.beat = None  # BeatEngine (Takt aus Music Assistant), wird nach dem Start gesetzt
        self._task: asyncio.Task | None = None
        self._auto_driving = False  # Muster wird gerade vom Beat-Sync gewählt
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def set(self, *, pattern: str | None = None, mode: str | None = None,
            tempo: float | None = None, size: float | None = None) -> None:
        if (pattern is not None or mode is not None) and self.beat is not None and self.beat.settings.get("auto_pattern"):
            # Muster/Modus von Hand gewählt: Automatik aus, die Wahl bleibt bestehen
            self._auto_driving = False
            self.beat.update(auto_pattern=False)
        if pattern is not None and (pattern == OFF or pattern in PATTERNS):
            self.pattern = pattern
        if mode is not None and mode in MODES:
            self.mode = mode
        if tempo is not None:
            self.tempo = max(1.0, min(100.0, float(tempo)))
        if size is not None:
            self.size = max(10.0, min(100.0, float(size)))
        if self.pattern == OFF:
            self.stop()
        elif self._task is None or self._task.done():
            self._task = self._hass.async_create_background_task(self._run(), name="freedmx_muster")
        self._notify()

    def beat_changed(self) -> None:
        """Beat-Sync hat sich geändert: Automatik starten bzw. beenden."""
        if self.beat is not None and self.beat.auto_active():
            if self._task is None or self._task.done():
                self._task = self._hass.async_create_background_task(self._run(), name="freedmx_muster")
        elif self._auto_driving:
            # Musik pausiert/aus oder Automatik abgeschaltet: Köpfe bleiben stehen
            self._auto_driving = False
            self.pattern = OFF
            self.stop()
            self._notify()

    def _apply_auto(self) -> None:
        choice = self.beat.auto_motion() if self.beat is not None else None
        if choice is None:
            return
        self._auto_driving = True
        if (self.pattern, self.mode) != choice:
            self.pattern, self.mode = choice
            self._notify()

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def _period(self) -> float:
        # Tempo 1 → 40 s je Durchlauf, Tempo 100 → 2 s
        return 40.0 - (self.tempo - 1) / 99 * 38.0

    def _lamp_on(self, lamp: dict) -> bool:
        light = self._hass.data.get("freedmx", {}).get("lights", {}).get(lamp["id"])
        return bool(light and light.is_on)

    async def _run(self) -> None:
        bounds = self._room.bounds()
        self._apply_auto()
        while self.pattern != OFF:
            self._apply_auto()
            synced = self.beat.motion_phase() if self.beat is not None else None
            if synced is not None:
                self._phase = synced  # Muster im Takt: ein Durchlauf = eingestellte Anzahl Beats
            else:
                self._phase = (self._phase + STEP / self._period()) % 1.0
            active = [l for l in self._lamps if self._lamp_on(l)]
            for i, lamp in enumerate(active):
                u, v = point(self.pattern, self._phase, i, len(active), self.mode)
                x, y = to_room(u, v, bounds, self.size / 100)
                mount = Mount.from_dict(lamp[LAMP_MOUNT])
                if self._room.limit:
                    x, y = clamp_point(self._room, mount, x, y)
                pan, tilt = aim_percent(mount, x, y)
                # alle Positionskanäle einer Lampe in einem Schritt schreiben (keine halben Positionen)
                start = int(lamp["start_address"])
                vals = self._device.get_channels(start, len(parse_layout(lamp["layout"])))
                for channel, value in position_writes(lamp, percent_to_16bit(pan), percent_to_16bit(tilt)):
                    vals[channel - start] = value
                self._device.set_channels(start, vals)
            await asyncio.sleep(STEP)
