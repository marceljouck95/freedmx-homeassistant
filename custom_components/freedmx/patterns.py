"""20 Bewegungsmuster für Moving Heads (ohne Home-Assistant-Abhängigkeit, testbar).

Jedes Muster liefert für die Phase t (0…1 = ein Durchlauf) einen Punkt (u, v) in -1…1.
u = quer (links/rechts), v = längs (Kopfwand/vorn). Die Integration bildet das auf den
erlaubten Bodenbereich ab; die Raum-Begrenzung gilt zusätzlich.
"""

from __future__ import annotations

import math
import random

TAU = 2 * math.pi


def _tri(x: float) -> float:
    """Dreieckswelle -1…1…-1 über eine Periode."""
    x = x % 1.0
    return 4 * x - 1 if x < 0.5 else 3 - 4 * x


def _poly(points: list[tuple[float, float]], t: float) -> tuple[float, float]:
    n = len(points)
    pos = (t % 1.0) * n
    i = int(pos)
    f = pos - i
    a, b = points[i % n], points[(i + 1) % n]
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f


def _rand_path(t: float, seed: int) -> tuple[float, float]:
    rnd = random.Random(seed)
    pts = [(rnd.uniform(-1, 1), rnd.uniform(-1, 1)) for _ in range(8)]
    n = len(pts)
    pos = (t % 1.0) * n
    i = int(pos)
    f = pos - i
    f = f * f * (3 - 2 * f)  # weich
    a, b = pts[i % n], pts[(i + 1) % n]
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f


PATTERNS: dict[str, tuple[str, callable]] = {
    "kreis": ("Kreis", lambda t: (math.cos(TAU * t), math.sin(TAU * t))),
    "acht": ("Acht", lambda t: (math.sin(TAU * t), math.sin(2 * TAU * t))),
    "welle_quer": ("Welle quer", lambda t: (_tri(t), 0.6 * math.sin(3 * TAU * t))),
    "welle_laengs": ("Welle längs", lambda t: (0.6 * math.sin(3 * TAU * t), _tri(t))),
    "kreuz": ("Kreuz", lambda t: (_tri(t * 2), 0.0) if (t % 1) < 0.5 else (0.0, _tri(t * 2))),
    "quadrat": ("Quadrat", lambda t: _poly([(-1, -1), (1, -1), (1, 1), (-1, 1)], t)),
    "dreieck": ("Dreieck", lambda t: _poly([(0, -1), (1, 1), (-1, 1)], t)),
    "zickzack": ("Zickzack", lambda t: _poly([(-1, -1), (1, -0.5), (-1, 0), (1, 0.5), (-1, 1), (1, 0.5), (-1, 0), (1, -0.5)], t)),
    "spirale": ("Spirale", lambda t: ((0.15 + 0.85 * abs(_tri(t))) * math.cos(4 * TAU * t), (0.15 + 0.85 * abs(_tri(t))) * math.sin(4 * TAU * t))),
    "pendel_quer": ("Pendel quer", lambda t: (math.sin(TAU * t), 0.0)),
    "pendel_laengs": ("Pendel längs", lambda t: (0.0, math.sin(TAU * t))),
    "ellipse": ("Ellipse", lambda t: (0.5 * math.cos(TAU * t), math.sin(TAU * t))),
    "stern": ("Stern", lambda t: _poly([(math.sin(TAU * k * 2 / 5), -math.cos(TAU * k * 2 / 5)) for k in range(5)], t)),
    "lissajous": ("Lissajous 3:2", lambda t: (math.sin(3 * TAU * t), math.sin(2 * TAU * t + math.pi / 4))),
    "zufall": ("Zufall sanft", lambda t: _rand_path(t, 7)),
    "scanner": ("Scanner", lambda t: _poly([(-1, -1), (1, -1), (1, -0.33), (-1, -0.33), (-1, 0.33), (1, 0.33), (1, 1), (-1, 1)], t)),
    "herz": ("Herz", lambda t: (
        (16 * math.sin(TAU * t) ** 3) / 17,
        -(13 * math.cos(TAU * t) - 5 * math.cos(2 * TAU * t) - 2 * math.cos(3 * TAU * t) - math.cos(4 * TAU * t)) / 17)),
    "blume": ("Blume", lambda t: (math.cos(3 * TAU * t) * math.cos(TAU * t), math.cos(3 * TAU * t) * math.sin(TAU * t))),
    "diagonale": ("Ping-Pong diagonal", lambda t: (_tri(t), _tri(t))),
    "suchlicht": ("Suchlicht", lambda t: (math.sin(TAU * t), 0.8 * math.sin(TAU * t * 0.5 + 1) ** 2 * 2 - 0.8)),
}

MODES = {"gleich": "Gleich", "gespiegelt": "Gespiegelt", "versetzt": "Versetzt"}


def point(name: str, t: float, index: int = 0, count: int = 1, mode: str = "gleich") -> tuple[float, float]:
    """Punkt (u, v) in -1…1 für Kopf Nr. index von count."""
    func = PATTERNS[name][1]
    if mode == "versetzt" and count > 1:
        t = t + index / count
    u, v = func(t % 1.0)
    if mode == "gespiegelt" and index % 2 == 1:
        u = -u
    return max(-1.0, min(1.0, u)), max(-1.0, min(1.0, v))


def to_room(u: float, v: float, bounds: tuple[float, float, float, float], size: float) -> tuple[float, float]:
    """(u, v) auf den erlaubten Bereich (x0, x1, y0, y1) abbilden; size 0…1 = Anteil der Fläche."""
    x0, x1, y0, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx + u * (x1 - x0) / 2 * size, cy + v * (y1 - y0) / 2 * size
