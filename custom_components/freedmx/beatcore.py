"""Beat-Takt ohne Home-Assistant-Abhängigkeit (testbar).

BeatClock   – läuft mit einem Tempo (BPM) frei weiter, kennt die Beat-Position zu jeder Zeit.
TapTempo    – Tempo aus Tipp-Zeitpunkten (Median der Abstände).
effect_*    – Lichtwerte je Beat-Effekt.
clean_*     – Titel/Künstler für die Tempo-Suche aufbereiten.
"""

from __future__ import annotations

import math
import re
from statistics import median

BPM_MIN = 40.0
BPM_MAX = 240.0

EFFECTS: dict[str, str] = {
    "aus": "Aus",
    "puls": "Puls",
    "blitz": "Blitz",
    "farbe": "Farbwechsel",
    "puls_farbe": "Puls + Farbe",
    "lauflicht": "Lauflicht",
    "wechsel": "Wechsel (abwechselnd)",
}

BARS = [1, 2, 4, 8, 16, 32]

PALETTES: dict[str, tuple[str, list[str]]] = {
    "regenbogen": ("Regenbogen", ["#ff0000", "#ff8c00", "#ffff00", "#00ff00", "#00ffff", "#0050ff", "#aa00ff", "#ff00a0"]),
    "party": ("Party", ["#ff00a0", "#00ffff", "#ffff00", "#0050ff"]),
    "warm": ("Warm", ["#ff0000", "#ff4000", "#ff8c00", "#ffc000"]),
    "kalt": ("Kalt", ["#0030ff", "#00b4ff", "#00ffd0", "#8000ff"]),
    "rot_blau": ("Rot / Blau", ["#ff0000", "#0030ff"]),
    "gruen_lila": ("Grün / Lila", ["#00ff40", "#a000ff"]),
}
PALETTE = [(int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)) for c in PALETTES["regenbogen"][1]]

RATES: dict[str, str] = {"0.5": "2× je Beat", "1": "Jeder Beat", "2": "Alle 2 Beats", "4": "Jeder Takt (4)"}


# ---------- Automatische Musterwahl ----------
# Muster nach Tempo: ruhige Titel weiche Bahnen, schnelle Titel eckige/harte Wechsel.
AUTO_POOLS: dict[str, list[str]] = {
    "ruhig": ["kreis", "acht", "ellipse", "pendel_quer", "pendel_laengs", "welle_quer",
              "welle_laengs", "blume", "herz", "zufall", "suchlicht", "lissajous"],
    "mittel": ["kreis", "acht", "welle_quer", "welle_laengs", "lissajous", "spirale", "blume",
               "diagonale", "dreieck", "quadrat", "stern", "ellipse", "herz"],
    "schnell": ["zickzack", "kreuz", "scanner", "stern", "diagonale", "dreieck", "quadrat",
                "spirale", "lissajous", "acht", "welle_quer"],
}
AUTO_MODES = ["gespiegelt", "versetzt", "gleich"]
# Wechsel: nach so vielen Muster-Durchläufen (0 = nur bei neuem Titel)
AUTO_EVERY: dict[str, str] = {"0": "Nach Titellänge", "1": "Jeder Durchlauf", "2": "Alle 2 Durchläufe", "4": "Alle 4 Durchläufe"}
# „Nach Titellänge“: ein Titel bekommt ~3 Muster, ein Abschnitt dauert aber 20…90 s
# (ein einstündiges DJ-Set wechselt also etwa alle 1,5 Minuten).
SEG_MIN, SEG_MAX, SEG_DEFAULT = 20.0, 90.0, 60.0


def segment_seconds(duration: float | None) -> float:
    if not duration or duration <= 0:
        return SEG_DEFAULT
    return max(SEG_MIN, min(SEG_MAX, float(duration) / 3.0))


def tempo_class(bpm: float | None) -> str:
    if not bpm or bpm < 100:
        return "ruhig"
    return "mittel" if bpm < 130 else "schnell"


def auto_pick(bpm: float | None, key: str, slot: int, previous: str | None = None) -> tuple[str, str]:
    """Muster und Modus fürs Tempo wählen. Gleicher Titel + gleicher Abschnitt = gleiche Wahl,
    das direkt vorherige Muster wird nicht wiederholt."""
    import random

    pool = [p for p in AUTO_POOLS[tempo_class(bpm)] if p != previous] or AUTO_POOLS["mittel"]
    rnd = random.Random(f"{key}|{slot}")
    return rnd.choice(pool), rnd.choice(AUTO_MODES)


def hex_rgb(h: str) -> tuple[int, int, int] | None:
    h = str(h or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return None


def palette_rgb(key: str, custom: list[str] | None = None) -> list[tuple[int, int, int]]:
    """Farbliste einer Palette (\"eigene\" = custom), mindestens eine Farbe."""
    if key == "eigene":
        cols = [c for c in (hex_rgb(x) for x in (custom or [])) if c]
    else:
        cols = [hex_rgb(x) for x in PALETTES.get(key, PALETTES["regenbogen"])[1]]
    return cols or list(PALETTE)


def clamp_bpm(bpm: float) -> float:
    """Tempo auf 40…240 BPM bringen (Halb-/Doppeltempo falten)."""
    bpm = float(bpm)
    if bpm <= 0 or math.isnan(bpm):
        raise ValueError("ungültiges Tempo")
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm > BPM_MAX:
        bpm /= 2
    return bpm


class BeatClock:
    """Beat-Position = anchor_beat + (t - anchor_t) * bpm / 60, solange sie läuft."""

    def __init__(self) -> None:
        self.bpm: float | None = None
        self.anchor_t = 0.0
        self.anchor_beat = 0.0
        self.running = False

    @property
    def ready(self) -> bool:
        return self.running and self.bpm is not None

    def beat(self, t: float) -> float:
        if self.bpm is None:
            return 0.0
        if not self.running:
            return self.anchor_beat
        return self.anchor_beat + (t - self.anchor_t) * self.bpm / 60.0

    def set_bpm(self, bpm: float, t: float) -> None:
        """Tempo ändern, die aktuelle Beat-Position bleibt erhalten (kein Sprung)."""
        cur = self.beat(t)
        self.bpm = clamp_bpm(bpm)
        self.anchor_t, self.anchor_beat = t, cur

    def start(self, t: float) -> None:
        if not self.running:
            self.anchor_t = t
            self.running = True

    def stop(self, t: float) -> None:
        if self.running:
            self.anchor_beat = self.beat(t)
            self.anchor_t = t
            self.running = False

    def place(self, beat: float, t: float) -> None:
        """Beat-Position zum Zeitpunkt t festlegen (z. B. aus der Wiedergabeposition)."""
        self.anchor_t, self.anchor_beat = t, beat

    def align(self, t: float, every: int = 1) -> None:
        """Zeitpunkt t ist ein Beat (every=4: die „1“ eines 4/4-Takts): auf den nächsten schieben."""
        cur = self.beat(t)
        self.place(float(round(cur / every) * every), t)

    def nudge(self, beats: float, t: float) -> None:
        self.place(self.beat(t) + beats, t)


class TapTempo:
    """Tempo aus Tipp-Zeitpunkten. Längere Pause (> reset s) beginnt neu."""

    def __init__(self, reset: float = 2.5, keep: int = 8) -> None:
        self.reset = reset
        self.keep = keep
        self.taps: list[float] = []

    def tap(self, t: float) -> float | None:
        if self.taps and (t - self.taps[-1] > self.reset or t <= self.taps[-1]):
            self.taps = []
        self.taps.append(t)
        self.taps = self.taps[-self.keep:]
        if len(self.taps) < 3:
            return None
        gaps = [b - a for a, b in zip(self.taps, self.taps[1:])]
        g = median(gaps)
        if g <= 0:
            return None
        try:
            return clamp_bpm(60.0 / g)
        except ValueError:
            return None

    @property
    def count(self) -> int:
        return len(self.taps)


# ---------- Effekte ----------

def effect_frame(effect: str, beat: float, index: int, count: int, intensity: float, ncolors: int = 8) -> tuple[float, int | None]:
    """Helligkeitsfaktor (0…1) und Farbindex (oder None) für eine Lampe.

    beat      – aktuelle Beat-Position
    index     – Nummer der Lampe in der Effektgruppe, count = Anzahl
    intensity – 0…1, wie stark der Effekt abdunkelt
    """
    low = max(0.0, 1.0 - intensity)
    frac = beat % 1.0
    n = math.floor(beat)
    count = max(1, count)
    ncolors = max(1, ncolors)
    if effect == "puls":
        return low + (1 - low) * math.exp(-frac * 4.0), None
    if effect == "blitz":
        return (1.0 if frac < 0.12 else low), None
    if effect == "farbe":
        return 1.0, n % ncolors
    if effect == "puls_farbe":
        return low + (1 - low) * math.exp(-frac * 4.0), (n + index) % ncolors
    if effect == "lauflicht":
        return (low + (1 - low) * math.exp(-frac * 3.0) if n % count == index else low), None
    if effect == "wechsel":
        return (1.0 if (n + index) % 2 == 0 else low), None
    return 1.0, None


def apply_effect(layout: list, base: list[int], level: float, color: tuple[int, int, int] | int | None) -> list[int]:
    """Lampenwerte mit Helligkeitsfaktor und Farbe (RGB oder Index der Standardpalette) berechnen."""
    if isinstance(color, int):
        color = PALETTE[color % len(PALETTE)]
    vals = list(base)
    level = max(0.0, min(1.0, level))
    has_d = "D" in layout
    rgb_idx = [layout.index(c) for c in ("R", "G", "B") if c in layout]
    if color is not None and len(rgb_idx) == 3:
        # Farbe in der aktuellen Helligkeit der Lampe (ohne Dimmerkanal: hellster Farbanteil)
        bright = 1.0 if has_d else max(base[i] for i in rgb_idx) / 255.0
        for i, c in zip(rgb_idx, color):
            vals[i] = round(c * bright)
        if "W" in layout:
            vals[layout.index("W")] = 0
    if has_d:
        i = layout.index("D")
        vals[i] = round(base[i] * level)
    else:
        for tok in ("R", "G", "B", "W"):
            if tok in layout:
                i = layout.index(tok)
                vals[i] = round(vals[i] * level)
    return [max(0, min(255, int(v))) for v in vals]


def effect_color(effect: str, beat: float, index: int, ncolors: int) -> int | None:
    """Farbindex für Effekte ohne eigenen Farbwechsel (Puls, Blitz, Lauflicht, Wechsel),
    damit auch dort die gewählte Palette leuchtet statt der Lampenfarbe (meist Weiß)."""
    if effect in ("aus", "farbe", "puls_farbe"):
        return None
    n = math.floor(beat)
    ncolors = max(1, ncolors)
    if effect == "wechsel":
        return (n + index) % ncolors
    return n % ncolors


# ---------- Lampentypen ----------

def lamp_kind(layout: list, preset: str | None = None) -> str | None:
    """Sonder-Lampe erkennen – über die Voreinstellung oder (bei „Eigenes Layout“) über das Layout."""
    if preset in ("fe1500", "dmh40"):
        return preset
    if len(layout) == 9 and all(isinstance(t, int) for t in layout) and layout[0] >= 220:
        return "fe1500"  # 9 feste Kanäle, Kanal 1 = DMX-Betriebsart
    if len(layout) == 14 and layout[:2] == ["P", "T"] and layout[10] == "D" and isinstance(layout[3], int):
        return "dmh40"
    return None


def effect_capable(layout: list, preset: str | None = None) -> bool:
    return lamp_kind(layout, preset) in SPECIAL_EFFECT or "D" in layout or all(c in layout for c in ("R", "G", "B"))


def _norm_color(color: tuple[int, int, int]) -> tuple[tuple[float, float, float], float]:
    """Farbe auf volle Helligkeit bringen (hellster Anteil = 255) und Sättigung 0…1."""
    hi = max(color)
    if hi <= 0:
        return (255.0, 255.0, 255.0), 0.0
    return tuple(c * 255.0 / hi for c in color), (hi - min(color)) / hi


def nearest_fixed(color: tuple[int, int, int], table: list[tuple[tuple[int, int, int], int]],
                  white: int, min_sat: float = 0.25) -> int:
    """Nächstliegenden Festwert zu einer RGB-Farbe; wenig gesättigte Farben = Weiß."""
    norm, sat = _norm_color(color)
    if sat < min_sat:
        return white
    return min(table, key=lambda fc: sum((a - b) ** 2 for a, b in zip(fc[0], norm)))[1]


# Futurelight DMH-40: Farbrad (Kanal 4), Mitte des jeweiligen Bereichs
DMH40_WHEEL_CH = 3
DMH40_WHITE = 7
DMH40_WHEEL: list[tuple[tuple[int, int, int], int]] = [
    ((255, 0, 0), 22),        # Rot
    ((0, 0, 255), 37),        # Blau
    ((0, 255, 0), 52),        # Grün
    ((255, 255, 120), 67),    # Hellgelb
    ((255, 0, 255), 82),      # Magenta
    ((255, 110, 80), 97),     # Lachsrot
    ((100, 180, 255), 112),   # Hellblau
    ((255, 100, 180), 124),   # Pink
]
WHEEL_GAP = 0.25  # Farbrad ist mechanisch: höchstens alle 250 ms wechseln


def dmh40_wheel(color: tuple[int, int, int] | int | None) -> int | None:
    """Farbrad-Wert des DMH-40 für eine Wunschfarbe (None = Farbrad nicht anfassen)."""
    if isinstance(color, int):
        color = PALETTE[color % len(PALETTE)]
    if color is None:
        return None
    return nearest_fixed(color, DMH40_WHEEL, DMH40_WHITE)


def hold_mechanical(state: dict, key: str, want: int, now: float, gap: float = WHEEL_GAP) -> int:
    """Mechanische Räder schonen: neuer Wert erst, wenn der letzte Wechsel ≥ gap s her ist."""
    last = state.get(key)
    if last is None or (last[0] != want and now - last[1] >= gap):
        state[key] = (want, now)
        return want
    return last[0]


def dmh40_effect(layout: list, base: list[int], level: float, color, wheel: int | None) -> list[int]:
    """DMH-40: Dimmer im Takt (wie jede Dimmer-Lampe), Farbe über das Farbrad."""
    vals = apply_effect(layout, base, level, None)
    if wheel is not None and len(vals) > DMH40_WHEEL_CH:
        vals[DMH40_WHEEL_CH] = wheel
    return vals


# ---------- Sonder-Lampen ohne Dimmer/RGB ----------
# FE-1500: Kanal 1 Betriebsart (≥220 = DMX), Kanal 2 LED-Farben (feste Farbwerte, je Bereichsmitte).
FE_DMX = 230
FE_WHITE = 32
FE_COLORS: list[tuple[tuple[int, int, int], int]] = [
    ((255, 0, 0), 12),        # Rot
    ((0, 255, 0), 17),        # Grün
    ((0, 0, 255), 22),        # Blau
    ((255, 140, 0), 27),      # Amber
    ((255, 255, 255), 32),    # Weiß
    ((255, 128, 128), 37),    # Weiß + Rot
    ((255, 255, 0), 42),      # Rot + Grün
    ((0, 255, 255), 47),      # Grün + Blau
    ((128, 255, 128), 62),    # Weiß + Grün
    ((165, 255, 0), 67),      # Grün + Amber
    ((255, 70, 0), 72),       # Amber + Rot
    ((255, 0, 255), 77),      # Rot + Blau
    ((128, 128, 255), 82),    # Blau + Weiß
]
FE_LASER_RED, FE_LASER_GREEN, FE_LASER_BOTH = 30, 70, 110
FE_SPIN = 20  # Drehung „leicht an“, wenn sie gestoppt ist (0…4)


def fe1500_color(color: tuple[int, int, int] | int | None) -> int | None:
    if isinstance(color, int):
        color = PALETTE[color % len(PALETTE)]
    if color is None:
        return None
    return nearest_fixed(color, FE_COLORS, FE_WHITE)


def fe1500_laser(color: tuple[int, int, int] | None) -> int:
    """Laser hat nur Rot und Grün: warme Farben Rot, grüne Grün, sonst beide im Wechsel."""
    if color is None:
        return FE_LASER_BOTH
    r, g, b = color
    if g > r and g >= b:
        return FE_LASER_GREEN
    if r >= g and r >= b:
        return FE_LASER_RED
    return FE_LASER_BOTH


def fe1500_effect(base: list[int], level: float, color: tuple[int, int, int] | int | None,
                  threshold: float = 0.5, laser: bool = True) -> list[int]:
    """Beat-Effekt für den FE-1500 (kein Dimmer): LEDs und Laser im Takt an/aus,
    Farbe auf die nächste LED-Farbe gerundet, Drehung leicht an."""
    vals = list(base)
    if len(vals) < 2:
        return vals
    vals[0] = FE_DMX if not 220 <= vals[0] <= 255 else vals[0]  # DMX-Steuerung, sonst reagiert nichts
    if isinstance(color, int):
        color = PALETTE[color % len(PALETTE)]
    on = level >= threshold - 1e-9
    if color is not None:
        vals[1] = fe1500_color(color)
    elif vals[1] < 10:
        vals[1] = 160  # LEDs waren aus: alle Farben
    if len(vals) >= 9:
        if laser:
            if vals[5] < 10:  # kein Laser von Hand gewählt: passend zur Farbe
                vals[5] = fe1500_laser(color)
        else:
            vals[5] = 0
        for i in (7, 8):  # LED- und Laser-Drehung
            if vals[i] < 5:
                vals[i] = FE_SPIN
    if not on:
        vals[1] = 0  # zwischen den Schlägen LEDs aus
        if len(vals) >= 6:
            vals[5] = 0  # … und Laser aus
    return [max(0, min(255, int(v))) for v in vals]


SPECIAL_EFFECT = {"fe1500": fe1500_effect, "dmh40": dmh40_effect}


# ---------- Moving-Head-Tempo ----------
# Futurelight DMH-40, Kanal 3 (Index 2): Pan/Tilt-Geschwindigkeit, 0 = schnellste, 225 = langsamste;
# 226-255 sind Sonderfunktionen (Blackout) und werden nie geschrieben.
MH_SPEED_CHANNEL = {"dmh40": 2}
MH_SPEED_MAX = 225
BPM_SLOW = 70.0     # ab hier (und darunter) langsamste Bewegung der Automatik
BPM_MID = 128.0     # mittleres Tempo = Standard-Geschwindigkeit
BPM_FAST = 175.0    # ab hier (und darüber) schnellste Bewegung der Automatik
MH_SPEED_REACH = 0.8  # Anteil des Weges bis zum Anschlag, den die Automatik ausnutzt


def mh_speed_percent(bpm: float | None, base: float, auto: bool = True) -> float:
    """Bewegungsgeschwindigkeit in % (1 langsam ... 100 schnell) passend zum Tempo.

    Ohne Automatik oder ohne Tempo gilt die Standard-Geschwindigkeit. Sonst wird um sie herum
    glatt (Sinus-Übergang) zwischen BPM_SLOW (langsam) und BPM_FAST (schnell) abgebildet.
    """
    base = max(1.0, min(100.0, float(base)))
    if not auto or not bpm:
        return base
    b = float(bpm)
    t = (b - BPM_MID) / ((BPM_MID - BPM_SLOW) if b < BPM_MID else (BPM_FAST - BPM_MID))
    t = math.sin(max(-1.0, min(1.0, t)) * math.pi / 2)
    span = (100.0 - base) if t > 0 else (base - 1.0)
    return max(1.0, min(100.0, base + t * span * MH_SPEED_REACH))


def mh_speed_value(bpm: float | None, base: float, auto: bool = True) -> int:
    """DMX-Wert für den Geschwindigkeitskanal (0 = schnell ... 225 = langsam, nie darüber)."""
    pct = mh_speed_percent(bpm, base, auto)
    return max(0, min(MH_SPEED_MAX, round((100.0 - pct) / 99.0 * MH_SPEED_MAX)))


# ---------- Titel für die Tempo-Suche ----------

_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(remaster|remix|edit|version|live|mono|stereo|feat\.?|ft\.?|mix|deluxe|bonus)[^\)\]]*[\)\]]",
    re.I,
)
_DASH = re.compile(r"\s+-\s+.*(remaster|version|edit|live|mono|stereo|mix).*$", re.I)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.I)


def clean_title(title: str) -> str:
    t = _NOISE.sub("", title or "")
    t = _DASH.sub("", t)
    t = _FEAT.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_artist(artist: str) -> str:
    a = re.split(r"\s*(,|&| x | feat\.?| ft\.?|/)\s*", artist or "", maxsplit=1, flags=re.I)[0]
    return a.strip()


def track_key(artist: str, title: str) -> str:
    return f"{clean_artist(artist).lower()}|{clean_title(title).lower()}"


def _norm(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", s)


def same_artist(a: str, b: str) -> bool:
    na, nb = _norm(clean_artist(a)), _norm(clean_artist(b))
    return bool(na and nb) and (na in nb or nb in na)


# ---------- Tempo aus Audio (Fallback, wenn kein Tempo bekannt ist) ----------

def estimate_bpm(samples, rate: int) -> float | None:
    """Tempo aus Mono-Samples schätzen (Kammfilter über Energie-Anstiege). Braucht numpy.

    Für jedes Kandidaten-Tempo 60…200 BPM wird geprüft, wie stark die Anstiege auf ein
    gleichmäßiges Beat-Raster fallen (beste Phase). Leichte Bevorzugung um 120 BPM.
    """
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    if x.size < rate * 6:
        return None
    hop = 128
    n = x.size // hop
    env = np.log(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12))
    onset = np.convolve(np.maximum(0.0, np.diff(env)), [0.25, 0.5, 0.25], "same")
    if not np.any(onset > 0):
        return None
    fps = rate / hop
    grid = np.arange(onset.size)

    def score(bpm: float) -> float:
        per = fps * 60.0 / bpm
        nb = int((onset.size - 1) / per)
        if nb < 8:
            return 0.0
        pos = np.arange(nb)[None, :] * per + np.arange(0, per, 1.0)[:, None]
        vals = np.interp(pos.ravel(), grid, onset).reshape(pos.shape).mean(axis=1)
        return float(vals.max() - vals.mean())

    cands = np.arange(60.0, 200.01, 0.5)
    prior = np.exp(-0.5 * (np.log2(cands / 120.0)) ** 2)
    scores = np.array([score(b) for b in cands]) * prior
    if not np.any(scores > 0):
        return None
    best = float(cands[int(np.argmax(scores))])
    fine = np.arange(best - 0.5, best + 0.51, 0.1)
    best = float(fine[int(np.argmax([score(b) for b in fine]))])
    try:
        return round(clamp_bpm(best), 1)
    except ValueError:
        return None
