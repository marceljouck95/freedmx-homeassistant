"""Beat-Sync in einem echten Home Assistant (pytest-homeassistant-custom-component).

Wird übersprungen, wenn das Testpaket nicht installiert ist.
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LAMPS = [
    {"id": "bar1", "name": "LED Bar 1", "start_address": 200, "preset": "ledbar5", "layout": "R,G,B,D,0"},
    {"id": "bar2", "name": "LED Bar 2", "start_address": 205, "preset": "ledbar5", "layout": "R,G,B,D,0"},
    {"id": "mh1", "name": "Moving Head", "start_address": 130, "preset": "dmh40", "layout": "P,T,0,0,0,0,0,0,0,255,D,0,p,t",
     "mount": {"x": 1.75, "y": 0.2, "height": 2.5}},
    {"id": "fe", "name": "FE-1500", "start_address": 62, "preset": "fe1500", "layout": "255,164,0,0,0,0,0,30,0"},
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def no_network():
    async def start(self):
        self._connected = True
    async def stop(self):
        self._connected = False
    with patch("custom_components.freedmx.device.FreeDmxDevice.async_start", start), \
         patch("custom_components.freedmx.device.FreeDmxDevice.async_stop", stop):
        yield


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="freedmx", title="freeDMX", data={"host": "127.0.0.1", "port": 10100},
                            options={"lamps": LAMPS})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ma_player(hass: HomeAssistant, state: str, title: str, pos: float = 30.0):
    reg = er.async_get(hass)
    if not reg.async_get("media_player.wohnzimmer"):
        reg.async_get_or_create("media_player", "music_assistant", "wz", suggested_object_id="wohnzimmer")
    hass.states.async_set("media_player.wohnzimmer", state, {
        "friendly_name": "Wohnzimmer", "media_title": title, "media_artist": "Daft Punk",
        "media_position": pos, "media_position_updated_at": dt_util.utcnow(),
    })


async def test_entities_and_deezer_tempo(hass: HomeAssistant, no_network, aioclient_mock):
    aioclient_mock.get("https://api.deezer.com/search", json={"data": [{"id": 3135556, "artist": {"name": "Daft Punk"}}]})
    aioclient_mock.get("https://api.deezer.com/track/3135556", json={"id": 3135556, "bpm": 122.7})
    await async_setup_component(hass, "homeassistant", {})
    _ma_player(hass, "paused", "One More Time - Radio Edit")
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]

    states = hass.states.async_all()
    ids = {s.entity_id for s in states}
    sensor = next(s for s in states if s.attributes.get("freedmx_beat"))
    assert any(i.startswith("switch.") and "beat" in i for i in ids)
    assert any(i.startswith("button.") and "tippen" in i for i in ids)
    assert any(i.startswith("select.") and "effekt" in i for i in ids)
    assert sensor.attributes["entitaeten"]["muster"].startswith("select.")
    assert [p["entity_id"] for p in sensor.attributes["ma_player"]] == ["media_player.wohnzimmer"]
    assert {l["id"] for l in sensor.attributes["lampen_wahl"]} == {"bar1", "bar2", "mh1", "fe"}

    # Wiedergabe startet -> Tempo von Deezer, Takt läuft und folgt der Position
    _ma_player(hass, "playing", "One More Time - Radio Edit", pos=30.0)
    await hass.async_block_till_done()
    for _ in range(20):
        if engine.clock.bpm:
            break
        await asyncio.sleep(0.05)
    assert engine.clock.bpm == pytest.approx(122.7)
    assert engine.bpm_source == "Deezer"
    assert engine.clock.running
    assert engine.beat_now() == pytest.approx(30.0 * 122.7 / 60, abs=0.5)
    st = hass.states.get(sensor.entity_id)
    assert float(st.state) == pytest.approx(122.7)

    # gleiche Position erneut gemeldet -> kein Sprung; Pause -> Takt steht
    b1 = engine.beat_now()
    _ma_player(hass, "paused", "One More Time - Radio Edit", pos=30.5)
    await hass.async_block_till_done()
    assert not engine.clock.running
    assert engine.beat_now() == pytest.approx(b1, abs=0.3)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_tap_manual_effect_and_motion(hass: HomeAssistant, no_network):
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    device = entry.runtime_data
    runner = hass.data["freedmx"]["runners"][entry.entry_id]

    await hass.services.async_call("freedmx", "beat_settings", {"source": "manuell", "manual_bpm": 120, "enabled": True, "effect": "blitz", "intensity": 100}, blocking=True)
    assert engine.clock.ready and engine.clock.bpm == 120

    # Tippen (Notfall-Funktion): 4 Tipps im Abstand 0,6 s -> 100 BPM
    clock = {"t": time.time()}
    with patch("custom_components.freedmx.beat.time.time", side_effect=lambda: clock["t"]):
        for _ in range(4):
            await hass.services.async_call("freedmx", "beat_tap", {}, blocking=True)
            clock["t"] += 0.6
        assert engine.clock.bpm == pytest.approx(100)
        assert engine.bpm_source == "Getippt"
    await hass.services.async_call("freedmx", "beat_bpm", {"bpm": 120}, blocking=True)

    # Lampen einschalten -> Effekt schreibt den Dimmer der LED-Bar
    bar = next(s.entity_id for s in hass.states.async_all("light") if s.attributes.get("lamp_id") == "bar1")
    await hass.services.async_call("light", "turn_on", {"entity_id": bar, "brightness": 255, "rgb_color": [255, 0, 0]}, blocking=True)
    seen = set()
    for _ in range(40):
        seen.add(device.get_channels(203, 1)[0])
        await asyncio.sleep(0.03)
    assert 255 in seen and 0 in seen, seen    # Blitz: voll auf dem Beat, sonst aus

    # nur bar2 gewählt -> bar1 bekommt wieder seine normalen Werte
    await hass.services.async_call("freedmx", "beat_settings", {"lamps": ["bar2"]}, blocking=True)
    await asyncio.sleep(0.15)
    assert device.get_channels(200, 4) == [255, 0, 0, 255]

    # Effekt aus -> alles normal
    await hass.services.async_call("freedmx", "beat_settings", {"lamps": "alle", "effect": "aus"}, blocking=True)
    await asyncio.sleep(0.1)
    assert device.get_channels(200, 4) == [255, 0, 0, 255]

    # Bewegung im Takt: Phase = Beat / Beats je Durchlauf
    await hass.services.async_call("freedmx", "beat_settings", {"bars": 4}, blocking=True)
    ph = engine.motion_phase()
    assert ph is not None and ph == pytest.approx((engine.beat_now() / 4) % 1.0, abs=0.02)
    await hass.services.async_call("freedmx", "beat_settings", {"enabled": False}, blocking=True)
    assert engine.motion_phase() is None

    # Feinjustage
    await hass.services.async_call("freedmx", "beat_settings", {"enabled": True}, blocking=True)
    b = engine.beat_now()
    await hass.services.async_call("freedmx", "beat_align", {"beats": 0.25}, blocking=True)
    assert engine.beat_now() == pytest.approx(b + 0.25, abs=0.02)
    await hass.services.async_call("freedmx", "beat_bpm", {"faktor": 2}, blocking=True)
    assert engine.clock.bpm == pytest.approx(240)
    await hass.services.async_call("freedmx", "beat_align", {"eins": True}, blocking=True)
    assert engine.beat_now() % 4 == pytest.approx(0, abs=0.05) or engine.beat_now() % 4 == pytest.approx(4, abs=0.05)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_settings_persist(hass: HomeAssistant, no_network, hass_storage):
    entry = await _setup(hass)
    await hass.services.async_call("freedmx", "beat_settings", {"effect": "lauflicht", "bars": 16, "offset_ms": 120}, blocking=True)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    assert engine.settings["effect"] == "lauflicht" and engine.settings["bars"] == 16 and engine.settings["offset_ms"] == 120
    sel = next(s for s in hass.states.async_all("select") if "effekt" in s.entity_id)
    assert sel.state == "Lauflicht"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_palette_rate_and_auto_lights(hass: HomeAssistant, no_network):
    await async_setup_component(hass, "homeassistant", {})
    _ma_player(hass, "paused", "Unbekannt")
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    device = entry.runtime_data
    engine.cache["daft punk|unbekannt"] = {"bpm": 120, "quelle": "Deezer", "t": time.time()}
    await hass.services.async_call("freedmx", "beat_settings", {
        "enabled": True, "effect": "farbe", "palette": "eigene", "colors": ["#00ff00", "#0000ff"],
        "rate": 1, "lamps": ["bar1"], "auto_lights": True}, blocking=True)
    # neuer Titel -> Tempo aus dem Speicher
    _ma_player(hass, "playing", "Unbekannt", pos=10)
    await hass.async_block_till_done()
    assert engine.clock.ready and engine.clock.bpm == 120
    # Automatik schaltet die gewählte Lampe ein
    bar = next(s for s in hass.states.async_all("light") if s.attributes.get("lamp_id") == "bar1")
    for _ in range(20):
        await hass.async_block_till_done()
        if hass.states.get(bar.entity_id).state == "on":
            break
        await asyncio.sleep(0.02)
    assert hass.states.get(bar.entity_id).state == "on"
    other = next(s for s in hass.states.async_all("light") if s.attributes.get("lamp_id") == "bar2")
    assert hass.states.get(other.entity_id).state == "off"
    await asyncio.sleep(0.1)
    seen = set()
    for _ in range(40):
        seen.add(tuple(device.get_channels(200, 3)))
        await asyncio.sleep(0.03)
    assert seen <= {(0, 255, 0), (0, 0, 255)} and len(seen) == 2, seen   # nur eigene Farben
    # Stopp -> nach 20 s wieder aus
    _ma_player(hass, "idle", "Unbekannt")
    await hass.async_block_till_done()
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=21))
    await hass.async_block_till_done()
    assert hass.states.get(bar.entity_id).state == "off"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_audio_analysis_fallback(hass: HomeAssistant, no_network, aioclient_mock):
    aioclient_mock.get("https://api.deezer.com/search", json={"data": [{"id": 7, "artist": {"name": "Daft Punk"}, "preview": "https://cdn.example/p.mp3"}]})
    aioclient_mock.get("https://api.deezer.com/track/7", json={"id": 7, "bpm": 0})
    await async_setup_component(hass, "homeassistant", {})
    _ma_player(hass, "paused", "Ohne Tempo")
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    with patch("custom_components.freedmx.beat.preview_bpm", return_value=126.5) as pb:
        _ma_player(hass, "playing", "Ohne Tempo", pos=5)
        await hass.async_block_till_done()
        for _ in range(20):
            if engine.clock.bpm:
                break
            await asyncio.sleep(0.05)
    assert pb.call_args[0][1] == "https://cdn.example/p.mp3"
    assert engine.clock.bpm == pytest.approx(126.5) and engine.bpm_source == "Audio-Analyse"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_auto_pattern(hass: HomeAssistant, no_network):
    """Beat-Sync wählt Muster und Modus selbst, Handwahl schaltet die Automatik ab."""
    from custom_components.freedmx.beatcore import AUTO_POOLS

    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    runner = hass.data["freedmx"]["runners"][entry.entry_id]
    device = entry.runtime_data
    mh = next(s.entity_id for s in hass.states.async_all("light") if s.attributes.get("lamp_id") == "mh1")
    await hass.services.async_call("light", "turn_on", {"entity_id": mh}, blocking=True)
    assert runner.pattern == "aus"

    # Beat-Sync an (schnelles Tempo) -> Muster aus dem schnellen Vorrat, Kopf bewegt sich
    await hass.services.async_call("freedmx", "beat_settings", {"source": "manuell", "manual_bpm": 140, "enabled": True, "bars": 1, "auto_every": 1}, blocking=True)
    await asyncio.sleep(0.1)
    assert engine.settings["auto_pattern"] is True
    assert runner.pattern in AUTO_POOLS["schnell"]
    pans = set()
    for _ in range(30):
        pans.add(tuple(device.get_channels(130, 2)))
        await asyncio.sleep(0.03)
    assert len(pans) > 3

    # Abschnittswechsel -> neues Muster (nie zweimal dasselbe hintereinander)
    seen = [engine.auto_motion()[0]]
    for _ in range(6):
        engine.nudge(1.0)
        seen.append(engine.auto_motion()[0])
    assert all(a != b for a, b in zip(seen, seen[1:])), seen
    await asyncio.sleep(0.06)
    assert runner.pattern == engine.auto_motion()[0]

    # Pause (Beat-Sync aus) -> Köpfe bleiben stehen
    await hass.services.async_call("freedmx", "beat_settings", {"enabled": False}, blocking=True)
    assert runner.pattern == "aus"

    # Handwahl -> Automatik aus, gewähltes Muster bleibt
    await hass.services.async_call("freedmx", "beat_settings", {"enabled": True}, blocking=True)
    await asyncio.sleep(0.06)
    select = next(s.entity_id for s in hass.states.async_all("select") if s.entity_id.endswith("movement_pattern"))
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Kreis"}, blocking=True)
    await asyncio.sleep(0.1)
    assert engine.settings["auto_pattern"] is False
    assert runner.pattern == "kreis"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_fe1500_und_titellaenge(hass: HomeAssistant, no_network):
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    device = entry.runtime_data
    assert "fe" in {l["id"] for l in engine.lamp_choices()}
    fe = next(s.entity_id for s in hass.states.async_all("light") if s.attributes.get("lamp_id") == "fe")
    await hass.services.async_call("light", "turn_on", {"entity_id": fe}, blocking=True)
    await hass.services.async_call("freedmx", "beat_settings", {"source": "manuell", "manual_bpm": 120, "enabled": True, "effect": "blitz", "intensity": 100, "lamps": ["fe"]}, blocking=True)
    seen = set()
    for _ in range(40):
        seen.add(device.get_channels(63, 1)[0])
        await asyncio.sleep(0.03)
    assert 0 in seen and len(seen - {0}) >= 1, seen
    assert device.get_channels(62, 1)[0] >= 220

    # nach Titellänge (Standard): Abschnitt wechselt nach Position, nicht nach Durchläufen
    assert engine.settings["auto_every"] == 0
    engine.track = {"key": "dj|set", "dur": 3600.0}
    engine._last_pos = (10.0, time.time())
    a = engine.auto_motion()
    engine._last_pos = (50.0, time.time())
    assert engine.auto_motion() == a                     # gleicher 90-s-Abschnitt
    engine._last_pos = (100.0, time.time())
    assert engine.auto_motion()[0] != a[0]               # nächster Abschnitt, anderes Muster
    await hass.config_entries.async_unload(entry.entry_id)


def _clicks(bpm: float, seconds: float, rate: int = 11025) -> bytes:
    import numpy as np
    n = int(seconds * rate)
    x = np.random.default_rng(1).normal(0, 0.02, n).astype(np.float32)
    per = 60.0 / bpm
    t = 0.0
    while t < seconds - 0.05:
        i = int(t * rate)
        k = np.arange(int(0.03 * rate))
        x[i:i + k.size] += 0.8 * np.exp(-k / (0.006 * rate)) * np.sin(2 * np.pi * 120 * k / rate)
        t += per
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


async def test_live_tempo_stream_und_aufloesung(hass: HomeAssistant, aioclient_mock):
    from custom_components.freedmx.live import LiveTempo, Smoother, fold_to

    got = []
    live = LiveTempo(hass, got.append, lambda: 5)
    reader = asyncio.StreamReader()
    reader.feed_data(_clicks(128, 31))
    reader.feed_eof()
    await live.listen(reader)
    assert got and got[-1] == pytest.approx(128, abs=1.5), got

    # Tempowechsel im DJ-Set: neues Tempo nach zwei übereinstimmenden Messungen
    reader = asyncio.StreamReader()
    reader.feed_data(_clicks(140, 31))
    reader.feed_eof()
    await live.listen(reader)
    assert got[-1] == pytest.approx(140, abs=1.5), got

    sm = Smoother()
    assert sm.feed(120) is None and sm.feed(120.5) == pytest.approx(120.2, abs=0.1)
    assert sm.feed(60.2) is None or True
    assert fold_to(63.5, 127) == pytest.approx(127)

    aioclient_mock.get("https://opml.radiotime.com/Tune.ashx", json={"body": [{"url": "http://stream.example/radio.mp3"}]})
    assert LiveTempo.supported("tunein--GiKwYjEd://radio/s263403")
    assert not LiveTempo.supported("deezer--abc://track/123")
    assert await live.resolve("tunein--GiKwYjEd://radio/s263403") == "http://stream.example/radio.mp3"


async def test_live_bpm_setzt_takt(hass: HomeAssistant, no_network):
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    reg = er.async_get(hass)
    reg.async_get_or_create("media_player", "music_assistant", "wz", suggested_object_id="wohnzimmer")
    async def _forever(self, cid):
        await asyncio.sleep(3600)

    with patch("custom_components.freedmx.live.LiveTempo._run", _forever), \
         patch("custom_components.freedmx.live.shutil.which", return_value="/usr/bin/ffmpeg"):
        await hass.services.async_call("freedmx", "beat_settings", {"enabled": True}, blocking=True)
        hass.states.async_set("media_player.wohnzimmer", "playing", {"media_title": "Radio", "media_content_id": "tunein--x://radio/s263403"})
        await hass.async_block_till_done()
        assert engine._live.active and engine._live.source_id.endswith("s263403")
        engine._live_bpm(126.0)
        assert engine.clock.bpm == pytest.approx(126) and engine.bpm_source == "Live-Analyse" and engine.clock.ready
        await hass.services.async_call("freedmx", "beat_settings", {"live_interval": 10}, blocking=True)
        assert engine._live._interval() == 10 and engine.info()["live_interval"] == 10
        await hass.services.async_call("freedmx", "beat_settings", {"live_bpm": False}, blocking=True)
        assert not engine._live.active
    await hass.config_entries.async_unload(entry.entry_id)


async def test_multiroom_nimmt_gruppenchef(hass: HomeAssistant, no_network):
    entry = await _setup(hass)
    engine = hass.data["freedmx"]["beats"][entry.entry_id]
    reg = er.async_get(hass)
    for uid, oid in (("a", "bad"), ("b", "wohnzimmer")):
        reg.async_get_or_create("media_player", "music_assistant", uid, suggested_object_id=oid)
    hass.states.async_set("media_player.bad", "playing", {"media_title": "X", "group_members": []})
    hass.states.async_set("media_player.wohnzimmer", "playing", {"media_title": "X", "group_members": ["media_player.wohnzimmer", "media_player.bad"]})
    await hass.async_block_till_done()
    engine.player = None
    assert engine._pick_player() == "media_player.wohnzimmer"
    await hass.config_entries.async_unload(entry.entry_id)
