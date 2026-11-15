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


def test_auto_pick_tempo_und_wechsel():
    AUTO_POOLS, auto_pick, tempo_class = bc.AUTO_POOLS, bc.auto_pick, bc.tempo_class
    ps = importlib.util.spec_from_file_location("patterns", ROOT / "patterns.py")
    pm = importlib.util.module_from_spec(ps)
    ps.loader.exec_module(pm)
    MODES, PATTERNS = pm.MODES, pm.PATTERNS

    assert all(p in PATTERNS for pool in AUTO_POOLS.values() for p in pool)
    assert tempo_class(80) == "ruhig" and tempo_class(120) == "mittel" and tempo_class(140) == "schnell"
    a = auto_pick(140, "x|y", 3)
    assert a == auto_pick(140, "x|y", 3)            # gleicher Abschnitt = gleiche Wahl
    assert a[0] in AUTO_POOLS["schnell"] and a[1] in MODES
    prev = None
    for slot in range(50):
        pat, _ = auto_pick(90, "t", slot, prev)
        assert pat in AUTO_POOLS["ruhig"] and pat != prev
        prev = pat


def test_fe1500_und_abschnitte():
    assert bc.effect_capable([255, 164, 0, 0, 0, 0, 0, 30, 0], "fe1500")
    base = [255, 164, 0, 0, 0, 0, 0, 30, 0]
    on = bc.fe1500_effect(base, 1.0, (0, 0, 250))
    assert on[0] >= 220 and on[1] == 22 and on[7] == 30      # Blau, Rest unverändert
    assert bc.fe1500_effect(base, 0.2, (0, 0, 250))[1] == 0   # zwischen den Schlägen aus
    assert bc.fe1500_effect(base, 1.0, None)[1] == 164         # ohne Farbwechsel: eigene Farbe
    assert bc.segment_seconds(180) == 60 and bc.segment_seconds(3600) == 90
    assert bc.segment_seconds(30) == 20 and bc.segment_seconds(None) == 60


# ---------- Moving-Head-Tempo ----------
def test_mh_speed_kennlinie_und_grenzen():
    base = 50
    # Mitte (128 BPM) = Standard-Geschwindigkeit
    assert bc.mh_speed_value(128, base) == bc.mh_speed_value(128, base, auto=False)
    assert bc.mh_speed_percent(128, base) == base
    # langsamer Beat = langsame Bewegung (hoher Kanalwert), schneller Beat = schnelle (niedriger Kanalwert)
    slow, mid, fast = (bc.mh_speed_value(b, base) for b in (70, 128, 175))
    assert slow > mid > fast
    # monoton über den ganzen Bereich, glatt (keine Sprünge > 12 je BPM)
    vals = [bc.mh_speed_value(b, base) for b in range(40, 241)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    assert max(abs(a - b) for a, b in zip(vals, vals[1:])) <= 12
    # unter 70 und über 175 bleibt der Wert stehen
    assert bc.mh_speed_value(40, base) == bc.mh_speed_value(70, base)
    assert bc.mh_speed_value(240, base) == bc.mh_speed_value(175, base)


def test_mh_speed_nie_ueber_225():
    for base in range(1, 101):
        for bpm in (None, 20, 40, 70, 100, 128, 175, 240, 480):
            for auto in (True, False):
                assert 0 <= bc.mh_speed_value(bpm, base, auto) <= 225
    assert bc.mh_speed_value(None, 1, False) == 225   # 1 % = langsamste erlaubte Stufe
    assert bc.mh_speed_value(None, 100, False) == 0   # 100 % = schnellste


def test_mh_speed_automatik_aus_oder_ohne_tempo_gilt_standard():
    for base in (1, 25, 50, 75, 100):
        std = round((100 - base) / 99 * 225)
        for bpm in (60, 128, 200):
            assert bc.mh_speed_value(bpm, base, auto=False) == std
        assert bc.mh_speed_value(None, base, auto=True) == std   # kein Tempo bekannt


def test_mh_speed_standard_verschiebt_die_mitte():
    assert bc.mh_speed_value(128, 20) > bc.mh_speed_value(128, 80)
    assert bc.mh_speed_value(70, 80) > bc.mh_speed_value(128, 80) > bc.mh_speed_value(175, 80)


MH = ["P", "T", 0, 0, 0, 0, 0, 0, 0, 255, "D", 0, "p", "t"]
FE = [255, 164, 0, 0, 0, 0, 0, 30, 0]
BAR = ["R", "G", "B", "D", 0]


def test_lampentyp_erkennung():
    assert bc.lamp_kind(MH, "dmh40") == "dmh40"
    assert bc.lamp_kind(MH, "custom") == "dmh40"          # eigenes Layout, gleiche Belegung
    assert bc.lamp_kind(FE, None) == "fe1500"             # FE-1500 ohne Voreinstellung
    assert bc.effect_capable(FE, "custom")
    assert bc.lamp_kind(BAR, "ledbar5") is None
    assert not bc.effect_capable([255, 164, 0, 0])


def test_farbe_auf_dmh40_farbrad():
    w = bc.dmh40_wheel
    assert 15 <= w((255, 0, 0)) <= 29                       # Rot
    assert 30 <= w((0, 48, 255)) <= 44                      # Blau
    assert 45 <= w((0, 255, 64)) <= 59                      # Grün
    assert 60 <= w((255, 255, 0)) <= 74                     # Gelb -> Hellgelb
    assert 75 <= w((255, 0, 255)) <= 89                     # Magenta
    assert 105 <= w((0, 255, 255)) <= 119                   # Cyan -> Hellblau
    assert w((255, 255, 255)) <= 14 and w((200, 190, 180)) <= 14   # ungesättigt -> Weiß
    assert w(None) is None
    # alle Paletten landen im Farbbereich 0…127 (kein Regenbogen-Modus)
    for key in bc.PALETTES:
        assert all(0 <= w(c) <= 127 for c in bc.palette_rgb(key))
    # Effekt: Dimmer pulst, Farbrad (Kanal 4) gesetzt, Shutter offen, Position/Kanal 3 unverändert
    base = [10, 20, 77, 0, 0, 0, 0, 0, 0, 255, 255, 0, 30, 40]
    out = bc.dmh40_effect(MH, base, 0.5, (255, 0, 0), w((255, 0, 0)))
    assert 15 <= out[3] <= 29 and out[10] == 128 and out[9] == 255
    assert out[:3] == [10, 20, 77] and out[12:] == [30, 40]
    assert bc.dmh40_effect(MH, base, 1.0, None, None)[3] == 0   # ohne Farbwunsch: Farbrad wie eingestellt


def test_farbrad_wechselt_nicht_zu_schnell():
    st = {}
    assert bc.hold_mechanical(st, "mh", 22, 0.0) == 22
    assert bc.hold_mechanical(st, "mh", 37, 0.1) == 22      # < 250 ms: bleibt
    assert bc.hold_mechanical(st, "mh", 37, 0.3) == 37
    assert bc.hold_mechanical(st, "mh", 37, 0.35) == 37


def test_farbe_auf_fe1500():
    on = bc.fe1500_effect(FE, 1.0, (255, 0, 0))
    assert 220 <= on[0] <= 255 and on[1] == 12                # DMX-Steuerung, Rot
    assert 10 <= on[5] <= 49                                  # Laser rot im Takt
    assert on[7] >= 5 and on[8] >= 5                          # Drehung an
    assert bc.fe1500_effect(FE, 1.0, (0, 255, 64))[1] == 17   # Grün
    assert 50 <= bc.fe1500_effect(FE, 1.0, (0, 255, 64))[5] <= 89
    assert bc.fe1500_effect(FE, 1.0, (255, 255, 0))[1] == 42  # Gelb = Rot + Grün
    assert bc.fe1500_effect(FE, 1.0, (250, 250, 250))[1] == 32  # Weiß
    off = bc.fe1500_effect(FE, 0.2, (255, 0, 0))
    assert off[1] == 0 and off[5] == 0                        # zwischen den Schlägen aus
    # Betriebsart ausserhalb DMX (z. B. von Hand auf Musik) -> DMX
    assert bc.fe1500_effect([120] + FE[1:], 1.0, None)[0] == 230
    # Schwelle: bei geringer Stärke trotzdem an/aus
    assert bc.fe1500_effect(FE, 0.8, (255, 0, 0), threshold=0.85)[1] == 0
    assert bc.fe1500_effect(FE, 1.0, (255, 0, 0), laser=False)[5] == 0


def test_rgb_bar_bekommt_palette_statt_weiss():
    cols = bc.palette_rgb("rot_blau")
    base = [255, 255, 255, 255, 0]                            # Lampe steht auf Weiß
    for effect in ("puls", "blitz", "lauflicht", "wechsel"):
        level, ci = bc.effect_frame(effect, 3.0, 0, 3, 0.7, len(cols))
        assert ci is None
        ci = bc.effect_color(effect, 3.0, 0, len(cols))
        out = bc.apply_effect(BAR, base, level, cols[ci])
        assert tuple(out[:3]) in cols and out[:3] != [255, 255, 255], (effect, out)
    assert bc.effect_color("farbe", 3.0, 0, 2) is None        # hat eigenen Farbwechsel
    assert bc.effect_color("puls", 4.0, 0, 2) != bc.effect_color("puls", 5.0, 0, 2)
