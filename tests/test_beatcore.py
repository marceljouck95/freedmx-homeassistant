"""Tests für den Beat-Takt (ohne Home Assistant)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "freedmx"
spec = importlib.util.spec_from_file_location("beatcore", ROOT / "beatcore.py")
bc = importlib.util.module_from_spec(spec)
sys.modules["beatcore"] = bc
spec.loader.exec_module(bc)


def test_clock_runs_and_keeps_phase_on_tempo_change():
    c = bc.BeatClock()
    c.set_bpm(120, 100.0)
    c.place(0.0, 100.0)
    c.start(100.0)
    assert abs(c.beat(101.0) - 2.0) < 1e-9          # 120 BPM = 2 Beats/s
    c.set_bpm(60, 101.0)                              # Tempowechsel ohne Sprung
    assert abs(c.beat(101.0) - 2.0) < 1e-9
    assert abs(c.beat(102.0) - 3.0) < 1e-9


def test_clock_stop_freezes_and_start_continues():
    c = bc.BeatClock()
    c.set_bpm(120, 0.0)
    c.start(0.0)
    c.stop(1.0)
    assert abs(c.beat(5.0) - 2.0) < 1e-9
    c.start(5.0)
    assert abs(c.beat(5.5) - 3.0) < 1e-9


def test_align_snaps_to_nearest_beat():
    c = bc.BeatClock()
    c.set_bpm(120, 0.0)
    c.start(0.0)
    c.align(1.1)                                      # 2.2 Beats -> 2
    assert abs(c.beat(1.1) - 2.0) < 1e-9
    c.nudge(0.25, 1.1)
    assert abs(c.beat(1.1) - 2.25) < 1e-9


def test_clamp_bpm_folds_half_and_double():
    assert bc.clamp_bpm(30) == 60
    assert bc.clamp_bpm(300) == 150
    try:
        bc.clamp_bpm(0)
        assert False
    except ValueError:
        pass


def test_tap_tempo_median_and_reset():
    t = bc.TapTempo()
    assert t.tap(0.0) is None
    assert t.tap(0.5) is None
    assert abs(t.tap(1.0) - 120) < 1e-6
    assert abs(t.tap(1.52) - 120) < 1e-6              # Median dämpft Ausreißer
    assert t.tap(10.0) is None                        # lange Pause -> neu
    assert t.count == 1


def test_effects_levels():
    lvl, col = bc.effect_frame("blitz", 4.05, 0, 1, 1.0)
    assert lvl == 1.0 and col is None
    lvl, _ = bc.effect_frame("blitz", 4.5, 0, 1, 1.0)
    assert lvl == 0.0
    lvl, _ = bc.effect_frame("blitz", 4.5, 0, 1, 0.3)
    assert abs(lvl - 0.7) < 1e-9
    lvl, _ = bc.effect_frame("puls", 3.0, 0, 1, 1.0)
    assert abs(lvl - 1.0) < 1e-9
    _, c1 = bc.effect_frame("farbe", 3.2, 0, 1, 1.0)
    _, c2 = bc.effect_frame("farbe", 4.2, 0, 1, 1.0)
    assert c1 != c2
    on = [bc.effect_frame("lauflicht", 5.0, i, 3, 1.0)[0] for i in range(3)]
    assert sorted(on) == [0.0, 0.0, 1.0]
    alt = [bc.effect_frame("wechsel", 2.0, i, 2, 1.0)[0] for i in range(2)]
    assert sorted(alt) == [0.0, 1.0]


def test_apply_effect_dimmer_and_rgb():
    # LED-Bar R,G,B,D,0
    lay = ["R", "G", "B", "D", 0]
    assert bc.apply_effect(lay, [255, 0, 0, 200, 0], 0.5, None) == [255, 0, 0, 100, 0]
    out = bc.apply_effect(lay, [255, 0, 0, 200, 0], 1.0, 4)  # Cyan
    assert out[:3] == [0, 255, 255] and out[3] == 200
    # Moving Head: nur Dimmer, Position bleibt unverändert
    mh = ["P", "T", 0, 0, 0, 0, 0, 0, 0, 255, "D", 0, "p", "t"]
    base = [10, 20, 0, 0, 0, 0, 0, 0, 0, 255, 255, 0, 30, 40]
    out = bc.apply_effect(mh, base, 0.0, 3)
    assert out[10] == 0 and out[:2] == [10, 20] and out[12:] == [30, 40]
    # RGB ohne Dimmer: Farbe skaliert
    assert bc.apply_effect(["R", "G", "B"], [0, 0, 200], 0.5, None) == [0, 0, 100]


def test_effect_capable():
    assert bc.effect_capable(["R", "G", "B", "D", 0])
    assert bc.effect_capable(["P", "T", "D"])
    assert not bc.effect_capable([255, 164, 0, 0])


def test_title_cleanup_and_key():
    assert bc.clean_title("One More Time - Remastered 2011") == "One More Time"
    assert bc.clean_title("Song (feat. X) [Radio Edit]") == "Song"
    assert bc.clean_artist("Daft Punk, Romanthony") == "Daft Punk"
    assert bc.track_key("Daft Punk", "One More Time (Radio Edit)") == "daft punk|one more time"
    assert bc.same_artist("Daft Punk", "Daft Punk")
    assert not bc.same_artist("Daft Punk", "Justice")


def test_palettes():
    assert bc.palette_rgb("rot_blau") == [(255, 0, 0), (0, 48, 255)]
    assert bc.palette_rgb("eigene", ["#00ff00", "kaputt"]) == [(0, 255, 0)]
    assert bc.palette_rgb("eigene", []) == bc.PALETTE           # nie leer
    assert bc.apply_effect(["R", "G", "B", "D"], [0, 0, 0, 255], 1.0, (10, 20, 30)) == [10, 20, 30, 255]
    _, ci = bc.effect_frame("farbe", 5.0, 0, 1, 1.0, 2)
    assert ci == 1


def _clicks(bpm, sr=11025, dur=30, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(sr * dur)) * 0.02
    per = 60 / bpm
    kick = np.sin(np.arange(400) * 0.12) * np.exp(-np.arange(400) / 90)
    for k in range(int(dur / per)):
        i = int(k * per * sr)
        if i + 400 < x.size:
            x[i:i + 400] += kick
        j = int((k + 0.5) * per * sr)
        if j + 100 < x.size:
            x[j:j + 100] += 0.3 * rng.standard_normal(100)
    return x


def test_estimate_bpm_from_audio():
    for bpm in (95, 105, 120, 128, 140, 150):
        est = bc.estimate_bpm(_clicks(bpm), 11025)
        assert est is not None and (abs(est - bpm) < 1.0 or abs(est - 2 * bpm) < 2.0 or abs(est - bpm / 2) < 1.0), (bpm, est)
    assert bc.estimate_bpm([0.0] * 1000, 11025) is None
