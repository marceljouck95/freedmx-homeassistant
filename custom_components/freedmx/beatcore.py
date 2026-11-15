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


def effect_capable(layout: list) -> bool:
    return "D" in layout or all(c in layout for c in ("R", "G", "B"))


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
