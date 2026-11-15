"""Live-Tempo: den laufenden Radio-Stream selbst mithören und alle 5 s das Tempo messen.

Ablauf: Stream-Adresse aus der Music-Assistant-Kennung auflösen (TuneIn, Radio-Browser
oder direkte http-Adresse), mit ffmpeg dauerhaft zu Mono-PCM dekodieren, die letzten
10 s in einem Ringpuffer halten und alle 5 s `estimate_bpm` darauf laufen lassen.
Ein neues Tempo wird erst übernommen, wenn zwei Messungen übereinstimmen (kein Springen).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections import deque
from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .beatcore import estimate_bpm

_LOGGER = logging.getLogger(__name__)

RATE = 11025
WINDOW_S = 10.0
EVERY_S = 5.0

_TUNEIN = re.compile(r"^tunein--[^:]*://radio/(s\d+)", re.I)
_RADIOBROWSER = re.compile(r"^radiobrowser--[^:]*://radio/([0-9a-f-]{36})", re.I)


def fold_to(bpm: float, ref: float | None) -> float:
    """Halb-/Doppeltempo an das bisherige Tempo anlehnen (127 statt 63,5)."""
    if not ref:
        return bpm
    best = bpm
    for f in (0.5, 2.0, 2 / 3, 1.5):
        if abs(bpm * f - ref) / ref < abs(best - ref) / ref and abs(bpm * f - ref) / ref < 0.04:
            best = bpm * f
    return best


class Smoother:
    """Übernimmt ein Tempo erst, wenn zwei aufeinanderfolgende Messungen auf 2 % übereinstimmen."""

    def __init__(self) -> None:
        self.current: float | None = None
        self._last: float | None = None

    def feed(self, bpm: float | None) -> float | None:
        if not bpm:
            return None
        bpm = fold_to(bpm, self.current)
        last, self._last = self._last, bpm
        if last is None or abs(bpm - last) / last > 0.02:
            return None
        new = round((bpm + last) / 2, 1)
        if self.current is None or abs(new - self.current) / self.current > 0.01:
            self.current = new
            return new
        return None


class LiveTempo:
    def __init__(self, hass: HomeAssistant, on_bpm: Callable[[float], None]) -> None:
        self.hass = hass
        self._on_bpm = on_bpm
        self.source_id: str | None = None   # Music-Assistant-Kennung, die gerade gehört wird
        self.status = ""
        self.smoother = Smoother()
        self._task: asyncio.Task | None = None
        self._urls: dict[str, str | None] = {}

    @staticmethod
    def supported(content_id: str | None) -> bool:
        c = str(content_id or "")
        return bool(_TUNEIN.match(c) or _RADIOBROWSER.match(c) or c.startswith(("http://", "https://")))

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def update(self, content_id: str | None, playing: bool, enabled: bool) -> None:
        want = enabled and playing and self.supported(content_id) and shutil.which("ffmpeg") is not None
        if want and content_id == self.source_id and self.active:
            return
        self.stop()
        if want:
            self.source_id = content_id
            self.smoother = Smoother()
            self._task = self.hass.async_create_background_task(self._run(str(content_id)), name="freedmx_live_tempo")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.source_id = None
        self.status = ""

    # ---------- Stream-Adresse ----------
    async def resolve(self, content_id: str) -> str | None:
        if content_id in self._urls:
            return self._urls[content_id]
        session = async_get_clientsession(self.hass)
        url = None
        try:
            if content_id.startswith(("http://", "https://")):
                url = content_id
            elif m := _TUNEIN.match(content_id):
                async with asyncio.timeout(10):
                    r = await session.get("https://opml.radiotime.com/Tune.ashx",
                                          params={"id": m.group(1), "render": "json", "formats": "mp3,aac,ogg"})
                    data = await r.json(content_type=None)
                for item in (data or {}).get("body", []):
                    if item.get("url"):
                        url = item["url"]
                        break
            elif m := _RADIOBROWSER.match(content_id):
                async with asyncio.timeout(10):
                    r = await session.get(f"https://all.api.radio-browser.info/json/stations/byuuid/{m.group(1)}")
                    data = await r.json(content_type=None)
                if data:
                    url = data[0].get("url_resolved") or data[0].get("url")
            if url and re.search(r"\.(pls|m3u8?)(\?|$)", url, re.I) and not url.lower().endswith(".m3u8"):
                async with asyncio.timeout(10):
                    txt = await (await session.get(url)).text()
                found = re.findall(r"(https?://[^\s\"']+)", txt)
                url = found[0] if found else None
        except Exception as err:  # noqa: BLE001 – ohne Adresse eben keine Live-Analyse
            _LOGGER.debug("Stream-Adresse für %s nicht gefunden: %s", content_id, err)
            url = None
        self._urls[content_id] = url
        return url

    # ---------- Mithören ----------
    async def _run(self, content_id: str) -> None:
        proc = None
        try:
            self.status = "Live: Sender wird gesucht …"
            url = await self.resolve(content_id)
            if not url:
                self.status = "Live: Stream nicht erreichbar"
                return
            proc = await asyncio.create_subprocess_exec(
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-reconnect", "1",
                "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", url,
                "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL,
            )
            self.status = "Live: hört mit …"
            await self.listen(proc.stdout)
            self.status = "Live: Stream beendet"
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Live-Tempo abgebrochen: %s", err)
            self.status = "Live: Fehler beim Mithören"
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    async def listen(self, reader: asyncio.StreamReader) -> None:
        """PCM lesen; alle EVERY_S Sekunden die letzten WINDOW_S Sekunden auswerten."""
        import numpy as np

        need = int(RATE * WINDOW_S) * 2
        step = int(RATE * EVERY_S) * 2
        buf = deque()
        size = 0
        since = 0
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                return
            buf.append(chunk)
            size += len(chunk)
            since += len(chunk)
            while size - len(buf[0]) >= need:
                size -= len(buf.popleft())
            if since >= step and size >= need:
                since = 0
                pcm = b"".join(buf)[-need:]

                def _calc(data: bytes = pcm) -> float | None:
                    return estimate_bpm(np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0, RATE)

                bpm = await self.hass.async_add_executor_job(_calc)
                self.status = f"Live: {bpm:.1f} BPM gemessen" if bpm else "Live: kein klarer Takt"
                new = self.smoother.feed(bpm)
                if new:
                    self._on_bpm(new)
