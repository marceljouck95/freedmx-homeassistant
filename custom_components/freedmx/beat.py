"""Beat-Synchronisation: Takt aus Music Assistant (bzw. Tippen) für Bewegung und Lichteffekte."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store

from .beatcore import (
    AUTO_EVERY,
    SPECIAL_EFFECT,
    auto_pick,
    segment_seconds,
    BARS,
    EFFECTS,
    PALETTES,
    RATES,
    estimate_bpm,
    hex_rgb,
    palette_rgb,
    BeatClock,
    TapTempo,
    apply_effect,
    clamp_bpm,
    clean_artist,
    clean_title,
    effect_capable,
    effect_frame,
    same_artist,
    track_key,
)
from .const import CONF_LAMPS, DOMAIN
from .live import LiveTempo
from .protocol import parse_layout

_LOGGER = logging.getLogger(__name__)

STEP = 0.04
AUTO = "auto"
MANUAL = "manuell"
DEEZER = "https://api.deezer.com"
CACHE_MAX = 500

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "source": AUTO,
    "effect": "puls",
    "bars": 8,
    "offset_ms": 0,
    "intensity": 70,
    "lamps": None,      # None = alle geeigneten Lampen
    "manual_bpm": 120.0,
    "palette": "regenbogen",
    "colors": ["#ff0000", "#0050ff", "#00ff40"],   # eigene Farben
    "rate": 1.0,          # Beats je Effektschritt (0.5 = doppelt so schnell)
    "auto_lights": False, # gewählte Lampen mit der Musik ein-/ausschalten
    "auto_pattern": True, # Bewegungsmuster wählt das System passend zum Tempo
    "auto_every": 0,
    "live_bpm": True,     # Radio-Streams selbst mithören und das Tempo messen
    "live_interval": 5,   # Sekunden zwischen zwei Messungen (5 = normal, 10 = ruhig)      # Musterwechsel alle n Durchläufe (0 = nach Titellänge)
}


class BeatEngine:
    """Ein Takt je freeDMX-Gerät. Einstellungen werden dauerhaft gespeichert."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device, conf: dict) -> None:
        self.hass = hass
        self.entry_id = entry.entry_id
        self._device = device
        self._lamps = [dict(l) for l in conf.get(CONF_LAMPS, [])]
        self._store: Store = Store(hass, 1, f"{DOMAIN}.beat_{entry.entry_id}")
        self.settings: dict[str, Any] = dict(DEFAULTS)
        self.cache: dict[str, dict] = {}          # Titel -> {"bpm": float|0, "quelle": str, "t": ts}
        self.clock = BeatClock()
        self.taps = TapTempo()
        self.player: str | None = None             # aktuell verfolgter MA-Player
        self.track: dict[str, Any] = {}            # artist, title, key
        self.status = "Aus"
        self.bpm_source = ""                        # Deezer / Getippt / Manuell
        self._listeners: list[Callable[[], None]] = []
        self._unsub_state: Callable[[], None] | None = None
        self._unsub_reg: Callable[[], None] | None = None
        self._task: asyncio.Task | None = None
        self._lookup: asyncio.Task | None = None
        self._touched: set[str] = set()             # Lampen, die der Effekt gerade schreibt
        self._last_pos: tuple[float, float] | None = None  # (Position s, Zeitpunkt)
        self._auto_on: set[str] = set()             # von der Automatik eingeschaltete Lampen
        self._auto_off: Callable[[], None] | None = None
        self._auto_slot: tuple | None = None        # (Titel, Abschnitt) der letzten Musterwahl
        self._auto_choice: tuple[str, str] | None = None
        self.runner = None                          # PatternRunner (Moving Heads), wird nach dem Start gesetzt
        self._live = LiveTempo(hass, self._live_bpm, lambda: self.settings["live_interval"])  # Live-Tempo aus dem Radio-Stream

    # ---------- Grundlagen ----------
    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        for k, v in (data.get("settings") or {}).items():
            if k in DEFAULTS:
                self.settings[k] = v
        self.cache = dict(data.get("cache") or {})
        self._resubscribe()
        # neue Music-Assistant-Player (z. B. neu eingerichtet) automatisch mit verfolgen
        self._unsub_reg = self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed)
        self._sync_player()
        self._ensure_task()

    async def async_stop(self) -> None:
        self._live.stop()
        if self._auto_off:
            self._auto_off()
            self._auto_off = None
        if self._unsub_reg:
            self._unsub_reg()
            self._unsub_reg = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._task and not self._task.done():
            self._task.cancel()
        if self._lookup and not self._lookup.done():
            self._lookup.cancel()
        self._restore_all()
        await self._save()

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _notify(self) -> None:
        if self.runner is not None:
            self.runner.beat_changed()
        for cb in list(self._listeners):
            cb()

    notify = _notify

    async def _save(self) -> None:
        if len(self.cache) > CACHE_MAX:
            keep = sorted(self.cache.items(), key=lambda kv: kv[1].get("t", 0))[-CACHE_MAX:]
            self.cache = dict(keep)
        await self._store.async_save({"settings": self.settings, "cache": self.cache})

    def _save_later(self) -> None:
        self._store.async_delay_save(lambda: {"settings": self.settings, "cache": self.cache}, 2)

    # ---------- Music Assistant ----------
    def ma_players(self) -> list[str]:
        reg = er.async_get(self.hass)
        return sorted(
            e.entity_id for e in reg.entities.values()
            if e.domain == "media_player" and e.platform == "music_assistant" and not e.disabled_by
        )

    def _resubscribe(self) -> None:
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        ids = self.ma_players()
        if self.settings["source"] not in (AUTO, MANUAL) and self.settings["source"] not in ids:
            ids.append(self.settings["source"])
        if ids:
            self._unsub_state = async_track_state_change_event(self.hass, ids, self._player_changed)

    @callback
    def _registry_changed(self, event: Event) -> None:
        eid = str(event.data.get("entity_id", ""))
        if eid.startswith("media_player.") and event.data.get("action") in ("create", "remove"):
            self._resubscribe()
            self._sync_player()

    @callback
    def _player_changed(self, event: Event) -> None:
        self._sync_player()

    def _pick_player(self) -> str | None:
        src = self.settings["source"]
        if src == MANUAL:
            return None
        if src != AUTO:
            return src
        players = self.ma_players()
        playing = [p for p in players if (st := self.hass.states.get(p)) and st.state == "playing"]
        # Multiroom: Mitglieder einer Gruppe spielen dasselbe wie der Gruppen-Chef -> nur den Chef nehmen
        members = set()
        for p in playing:
            grp = list((self.hass.states.get(p).attributes.get("group_members") or []))
            if grp and grp[0] == p:
                members.update(grp[1:])
        playing = [p for p in playing if p not in members] or playing
        if self.player in playing:
            return self.player
        if playing:
            return playing[0]
        return self.player if self.player in players else None

    @callback
    def _sync_player(self) -> None:
        """Wiedergabe des Players auf den Takt übertragen."""
        now = time.time()
        if self.settings["source"] == MANUAL:
            self._live.stop()
            self.player = None
            self.track = {}
            if self.clock.bpm is None:
                self.clock.set_bpm(self.settings["manual_bpm"], now)
            self.bpm_source = self.bpm_source or "Manuell"
            self.clock.start(now)
            self.status = "Manueller Takt – zum Ausrichten im Takt tippen"
            self._notify()
            return
        pid = self._pick_player()
        self.player = pid
        st = self.hass.states.get(pid) if pid else None
        if st is None:
            self._live.stop()
            self.clock.stop(now)
            self._auto_lights(False)
            self.track = {}
            self.status = "Kein Music-Assistant-Player gefunden" if not self.ma_players() else "Keine Wiedergabe"
            self._notify()
            return
        a = st.attributes
        artist = str(a.get("media_artist") or a.get("media_album_artist") or "")
        title = str(a.get("media_title") or "")
        key = track_key(artist, title) if title else ""
        dur = a.get("media_duration")
        if key != self.track.get("key"):
            self.track = {"artist": artist, "title": title, "key": key}
            self._last_pos = None
            self.taps = TapTempo()
            self._new_track(key, artist, title, now)
        try:
            self.track["dur"] = float(dur) if dur else None
        except (TypeError, ValueError):
            self.track["dur"] = None
        self._live.update(a.get("media_content_id"), st.state == "playing",
                          bool(self.settings["enabled"] and self.settings["live_bpm"]))
        if st.state != "playing":
            self.clock.stop(now)
            self.status = "Pausiert" if st.state == "paused" else "Keine Wiedergabe"
            self._auto_lights(False)
            self._notify()
            return
        self._auto_lights(True)
        # Wiedergabeposition übernehmen – nur bei Sprung (Start, Spulen, neuer Titel),
        # sonst läuft der Takt frei weiter (die Positionsangabe ist nur auf ~1 s genau)
        pos = a.get("media_position")
        upd = a.get("media_position_updated_at")
        if pos is not None and upd is not None and self.clock.bpm:
            try:
                t_upd = upd.timestamp() if hasattr(upd, "timestamp") else _parse_ts(upd)
                p_now = float(pos) + (now - t_upd)
            except (TypeError, ValueError):
                p_now = None
            if p_now is not None:
                expected = None
                if self._last_pos is not None:
                    expected = self._last_pos[0] + (now - self._last_pos[1])
                jumped = expected is None or abs(p_now - expected) > 2.0 or not self.clock.running
                self._last_pos = (p_now, now)
                if jumped:
                    off = float(self.cache.get(key, {}).get("off", 0.0))
                    self.clock.place((p_now - off) * self.clock.bpm / 60.0, now)
        self.clock.start(now)
        self.status = self._status_text()
        self._notify()

    def _chosen_lights(self) -> list:
        lights = self.hass.data.get(DOMAIN, {}).get("lights", {})
        chosen = self.settings["lamps"]
        ids = [l["id"] for l in self.lamp_choices()] if chosen is None else chosen
        return [lights[i] for i in ids if i in lights and lights[i].entity_id]

    @callback
    def _auto_lights(self, playing: bool) -> None:
        """Gewählte Lampen mit der Musik ein- und (nach 20 s Stille) wieder ausschalten."""
        if self._auto_off:
            self._auto_off()
            self._auto_off = None
        if not (self.settings["enabled"] and self.settings["auto_lights"]):
            return
        if playing:
            todo = [l for l in self._chosen_lights() if not l.is_on]
            for l in todo:
                self._auto_on.add(l._lamp["id"])
            if todo:
                self.hass.async_create_task(self.hass.services.async_call(
                    "light", "turn_on", {"entity_id": [l.entity_id for l in todo], "brightness": 255}
                ))
            return
        if not self._auto_on:
            return

        @callback
        def _off(_now) -> None:
            self._auto_off = None
            lights = self.hass.data.get(DOMAIN, {}).get("lights", {})
            ids = [lights[i].entity_id for i in self._auto_on if i in lights and lights[i].is_on]
            self._auto_on.clear()
            if ids:
                self.hass.async_create_task(self.hass.services.async_call("light", "turn_off", {"entity_id": ids}))

        self._auto_off = async_call_later(self.hass, 20, _off)

    def _status_text(self) -> str:
        if not self.clock.bpm:
            if self._lookup and not self._lookup.done():
                return "Tempo wird ermittelt …"
            return "Tempo unbekannt – Effekte pausieren (notfalls in der Feinjustage tippen)"
        return f"Takt läuft ({self.bpm_source})" if self.bpm_source else "Takt läuft"

    def _new_track(self, key: str, artist: str, title: str, now: float) -> None:
        if self._lookup and not self._lookup.done():
            self._lookup.cancel()
        if self._live.active and self._live.smoother.current:
            return  # Radio mit Live-Messung: Titelwechsel ändert das gemessene Tempo nicht
        hit = self.cache.get(key) if key else None
        if hit and hit.get("bpm"):
            self.clock.set_bpm(hit["bpm"], now)
            self.bpm_source = hit.get("quelle", "Speicher")
            return
        self.clock.bpm = None
        self.bpm_source = ""
        if key and not (hit and hit.get("bpm") == 0 and now - hit.get("t", 0) < 86400):
            self._lookup = self.hass.async_create_background_task(
                self._fetch_bpm(key, artist, title), name="freedmx_bpm_suche"
            )

    async def _fetch_bpm(self, key: str, artist: str, title: str) -> None:
        self.status = self._status_text()
        self._notify()
        bpm, preview = await deezer_lookup(self.hass, artist, title)
        quelle = "Deezer"
        if not bpm and preview:
            bpm = await preview_bpm(self.hass, preview)
            quelle = "Audio-Analyse"
        self.cache[key] = {**self.cache.get(key, {}), "bpm": bpm or 0, "quelle": quelle, "t": time.time()}
        self._save_later()
        if self.track.get("key") != key:
            return
        now = time.time()
        if bpm:
            self.clock.set_bpm(bpm, now)
            self.bpm_source = quelle
            self._last_pos = None
            self._sync_player()  # Position mit dem neuen Tempo übernehmen
        else:
            self.status = self._status_text()
            self._notify()

    def _live_bpm(self, bpm: float) -> None:
        """Neues, stabil gemessenes Tempo aus dem Live-Stream (Phase bleibt erhalten)."""
        now = time.time()
        self.clock.set_bpm(bpm, now)
        self.bpm_source = "Live-Analyse"
        if self.player and (st := self.hass.states.get(self.player)) and st.state == "playing":
            self.clock.start(now)
        self.status = self._status_text()
        self._notify()

    # ---------- Bedienung ----------
    def tap(self) -> None:
        now = time.time() - self.settings["offset_ms"] / 1000.0
        bpm = self.taps.tap(now)
        if bpm:
            self.clock.set_bpm(bpm, now)
            self.bpm_source = "Getippt"
            self._remember(bpm, "Getippt")
            if self.settings["source"] == MANUAL:
                self.settings["manual_bpm"] = round(bpm, 2)
                self._save_later()
        if self.clock.bpm:
            self.clock.align(now)
            self._remember_offset()
            if self.settings["source"] == MANUAL or not self.player:
                self.clock.start(now)
        self.status = self._status_text() if self.clock.running else self.status
        self._notify()

    def align(self, bar: bool = False) -> None:
        now = time.time() - self.settings["offset_ms"] / 1000.0
        if self.clock.bpm:
            self.clock.align(now, 4 if bar else 1)
            self._remember_offset()
            self._notify()

    def nudge(self, beats: float) -> None:
        if self.clock.bpm:
            self.clock.nudge(beats, time.time())
            self._remember_offset()
            self._notify()

    def set_bpm(self, bpm: float | None = None, factor: float | None = None) -> None:
        now = time.time()
        cur = self.clock.bpm or self.settings["manual_bpm"]
        new = bpm if bpm else cur * (factor or 1.0)
        self.clock.set_bpm(new, now)
        self.bpm_source = "Manuell"
        self._remember(self.clock.bpm, "Manuell")
        if self.settings["source"] == MANUAL:
            self.settings["manual_bpm"] = round(self.clock.bpm, 2)
            self.clock.start(now)
        self.status = self._status_text() if self.clock.running else self.status
        self._save_later()
        self._notify()

    def _remember(self, bpm: float, quelle: str) -> None:
        key = self.track.get("key")
        if key:
            self.cache[key] = {**self.cache.get(key, {}), "bpm": round(bpm, 2), "quelle": quelle, "t": time.time()}
            self._save_later()

    def _remember_offset(self) -> None:
        """Ausrichtung für diesen Titel merken: bei der nächsten Wiedergabe passt der Takt sofort."""
        key = self.track.get("key")
        if not key or not self._last_pos or not self.clock.bpm:
            return
        now = time.time()
        pos = self._last_pos[0] + (now - self._last_pos[1])
        beat = self.clock.beat(now)
        off = pos - beat * 60.0 / self.clock.bpm
        self.cache[key] = {**self.cache.get(key, {}), "off": round(off, 3), "t": now}
        self._save_later()

    def update(self, **kw: Any) -> None:
        changed_source = False
        for k, v in kw.items():
            if k == "lamps" and v == "alle":
                self.settings["lamps"] = None
                continue
            if v is None or k not in DEFAULTS:
                continue
            if k == "effect" and v not in EFFECTS:
                continue
            if k == "bars":
                v = min(BARS, key=lambda b: abs(b - int(v)))
            if k == "offset_ms":
                v = max(-1000, min(1000, int(v)))
            if k == "intensity":
                v = max(0, min(100, int(v)))
            if k == "manual_bpm":
                v = round(clamp_bpm(float(v)), 2)
            if k == "lamps":
                v = [x for x in v if any(l["id"] == x for l in self._lamps)] if isinstance(v, list) else None
            if k == "palette" and v not in PALETTES and v != "eigene":
                continue
            if k == "colors":
                v = [("#%02x%02x%02x" % c) for c in (hex_rgb(x) for x in (v if isinstance(v, list) else [])) if c][:8]
                if not v:
                    continue
            if k == "rate":
                v = min((0.5, 1.0, 2.0, 4.0), key=lambda r: abs(r - float(v)))
            if k == "live_interval":
                v = 10 if int(v) >= 8 else 5
            if k in ("auto_lights", "auto_pattern", "live_bpm"):
                v = bool(v)
            if k == "auto_every":
                v = min((0, 1, 2, 4), key=lambda r: abs(r - int(v)))
                self._auto_slot = None
            if k == "source":
                changed_source = v != self.settings["source"]
            self.settings[k] = v
        if changed_source:
            self.clock = BeatClock()
            self.track = {}
            self.bpm_source = ""
            self._last_pos = None
            self._resubscribe()
        if "manual_bpm" in kw and self.settings["source"] == MANUAL and kw["manual_bpm"]:
            self.clock.set_bpm(self.settings["manual_bpm"], time.time())
        self._sync_player()
        self._ensure_task()
        self._save_later()
        self._notify()

    # ---------- Lampen und Effekt-Schleife ----------
    def lamp_choices(self) -> list[dict]:
        out = []
        for l in self._lamps:
            try:
                lay = parse_layout(l["layout"])
            except (KeyError, ValueError):
                continue
            if effect_capable(lay, l.get("preset")):
                out.append({"id": l["id"], "name": l.get("name", l["id"])})
        return out

    def _effect_lamps(self) -> list[tuple[dict, list, Any]]:
        chosen = self.settings["lamps"]
        lights = self.hass.data.get(DOMAIN, {}).get("lights", {})
        out = []
        for l in self._lamps:
            if chosen is not None and l["id"] not in chosen:
                continue
            try:
                lay = parse_layout(l["layout"])
            except (KeyError, ValueError):
                continue
            light = lights.get(l["id"])
            if light is None or not effect_capable(lay, l.get("preset")) or not light.is_on:
                continue
            out.append((l, lay, light))
        return out

    def _ensure_task(self) -> None:
        want = self.settings["enabled"] and self.settings["effect"] != "aus"
        if want and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_background_task(self._run(), name="freedmx_beat")
        elif not want:
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            self._restore_all()

    def _restore_all(self) -> None:
        lights = self.hass.data.get(DOMAIN, {}).get("lights", {})
        for lamp_id in list(self._touched):
            light = lights.get(lamp_id)
            if light is not None and light.hass is not None:
                try:
                    light._apply(None)  # normale Werte der Lampe wieder senden
                except Exception:  # noqa: BLE001 – Effekt darf nie das Licht blockieren
                    _LOGGER.debug("Wiederherstellen von %s fehlgeschlagen", lamp_id)
        self._touched.clear()

    def beat_now(self, t: float | None = None) -> float:
        t = time.time() if t is None else t
        return self.clock.beat(t - self.settings["offset_ms"] / 1000.0)

    def motion_phase(self) -> float | None:
        """Phase 0…1 für Bewegungsmuster, wenn der Takt läuft (sonst None = normales Tempo)."""
        if not self.settings["enabled"] or not self.clock.ready:
            return None
        return (self.beat_now() / self.settings["bars"]) % 1.0

    def auto_active(self) -> bool:
        return bool(self.settings["enabled"] and self.settings["auto_pattern"] and self.clock.ready)

    def auto_motion(self) -> tuple[str, str] | None:
        """Vom System gewähltes (Muster, Modus) für den aktuellen Abschnitt – oder None."""
        if not self.auto_active():
            return None
        every = int(self.settings["auto_every"])
        if every == 0:
            # nach Titellänge: Abschnitte von 20…90 s entlang der Wiedergabeposition
            now = time.time()
            pos = (self._last_pos[0] + now - self._last_pos[1]) if self._last_pos else self.beat_now() * 60.0 / self.clock.bpm
            part = int(max(0.0, pos) // segment_seconds(self.track.get("dur")))
        else:
            part = int(self.beat_now() // max(1, int(self.settings["bars"]))) // every
        slot = (self.track.get("key") or "manuell", part)
        if slot != self._auto_slot:
            prev = self._auto_choice[0] if self._auto_choice else None
            self._auto_choice = auto_pick(self.clock.bpm, slot[0], slot[1], prev)
            self._auto_slot = slot
        return self._auto_choice

    async def _run(self) -> None:
        try:
            while self.settings["enabled"] and self.settings["effect"] != "aus":
                if self.clock.ready:
                    lamps = self._effect_lamps()
                    ids = {l["id"] for l, _, _ in lamps}
                    for gone in self._touched - ids:
                        self._touched.discard(gone)
                        light = self.hass.data.get(DOMAIN, {}).get("lights", {}).get(gone)
                        if light is not None and light.hass is not None:
                            light._apply(None)
                    beat = self.beat_now() / float(self.settings["rate"] or 1.0)
                    inten = self.settings["intensity"] / 100.0
                    cols = palette_rgb(self.settings["palette"], self.settings["colors"])
                    for i, (lamp, lay, light) in enumerate(lamps):
                        level, ci = effect_frame(self.settings["effect"], beat, i, len(lamps), inten, len(cols))
                        base = light._target()
                        color = cols[ci] if ci is not None else None
                        special = SPECIAL_EFFECT.get(lamp.get("preset"))
                        vals = special(base, level, color) if special else apply_effect(lay, base, level, color)
                        self._device.set_channels(int(lamp["start_address"]), vals)
                        self._touched.add(lamp["id"])
                elif self._touched:
                    self._restore_all()
                await asyncio.sleep(STEP)
        finally:
            self._restore_all()

    # ---------- Anzeige ----------
    def info(self) -> dict[str, Any]:
        now = time.time()
        ready = self.clock.ready
        return {
            "laeuft": ready,
            "status": self.status if self.settings["enabled"] or self.status.startswith("Kein") else "Beat-Sync aus",
            "bpm_quelle": self.bpm_source,
            "player": self.player,
            "titel": self.track.get("title", ""),
            "kuenstler": self.track.get("artist", ""),
            # Für die Karte: Beat b liegt bei anker_ms + (b - anker_beat) * 60000 / bpm (Wanduhr, inkl. Versatz)
            "anker_ms": round(now * 1000) if ready else None,
            "anker_beat": round(self.beat_now(now), 4) if self.clock.bpm else None,
            "tipps": self.taps.count,
            **{k: self.settings[k] for k in ("enabled", "source", "effect", "bars", "offset_ms", "intensity", "manual_bpm")},
            "lampen": self.settings["lamps"],
            "lampen_wahl": self.lamp_choices(),
            "ma_player": [
                {"entity_id": p, "name": (st.attributes.get("friendly_name") if (st := self.hass.states.get(p)) else p) or p}
                for p in self.ma_players()
            ],
            "effekte": EFFECTS,
            "takte_wahl": BARS,
            "palette": self.settings["palette"],
            "farben": self.settings["colors"],
            "rate": self.settings["rate"],
            "auto_lights": self.settings["auto_lights"],
            "auto_pattern": self.settings["auto_pattern"],
            "auto_every": self.settings["auto_every"],
            "auto_wechsel_wahl": AUTO_EVERY,
            "live_bpm": self.settings["live_bpm"],
            "live_status": self._live.status,
            "live_interval": self.settings["live_interval"],
            "auto_muster": self._auto_choice[0] if self._auto_choice and self.auto_active() else None,
            "paletten": {k: {"name": v[0], "farben": v[1]} for k, v in PALETTES.items()},
            "raten": RATES,
        }


def _parse_ts(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(str(value)).timestamp()


async def deezer_lookup(hass: HomeAssistant, artist: str, title: str) -> tuple[float | None, str | None]:
    """Tempo und 30-s-Hörprobe eines Titels bei Deezer (frei, ohne Schlüssel)."""
    session = async_get_clientsession(hass)
    a, t = clean_artist(artist), clean_title(title)
    if not t:
        return None, None
    queries = [f'artist:"{a}" track:"{t}"', f"{a} {t}"] if a else [t]
    preview = None
    try:
        for q in queries:
            async with asyncio.timeout(8):
                resp = await session.get(f"{DEEZER}/search", params={"q": q, "limit": 5})
                data = await resp.json(content_type=None)
            hits = [h for h in (data or {}).get("data", []) if not a or same_artist(a, (h.get("artist") or {}).get("name", ""))]
            for h in hits[:3]:
                preview = preview or h.get("preview") or None
                async with asyncio.timeout(8):
                    resp = await session.get(f"{DEEZER}/track/{int(h['id'])}")
                    tr = await resp.json(content_type=None)
                bpm = float((tr or {}).get("bpm") or 0)
                if bpm > 0:
                    return round(clamp_bpm(bpm), 2), preview
            if hits:
                break
    except (asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError) as err:
        _LOGGER.debug("Deezer-Tempo für %s – %s nicht gefunden: %s", artist, title, err)
    except Exception as err:  # noqa: BLE001 – Netzfehler dürfen den Takt nicht stören
        _LOGGER.debug("Deezer-Abfrage fehlgeschlagen: %s", err)
    return None, preview


async def preview_bpm(hass: HomeAssistant, url: str) -> float | None:
    """Hörprobe laden, mit ffmpeg dekodieren und das Tempo selbst messen."""
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not url.startswith("https://"):
        return None
    try:
        session = async_get_clientsession(hass)
        async with asyncio.timeout(15):
            resp = await session.get(url)
            audio = await resp.read()
        if not audio or len(audio) > 5_000_000:
            return None
        rate = 11025
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(20):
            pcm, _ = await proc.communicate(audio)
        if proc.returncode != 0 or not pcm:
            return None

        def _analyse() -> float | None:
            import numpy as np

            return estimate_bpm(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0, rate)

        return await hass.async_add_executor_job(_analyse)
    except Exception as err:  # noqa: BLE001 – Analyse ist nur ein Zusatz
        _LOGGER.debug("Audio-Analyse fehlgeschlagen: %s", err)
        return None
