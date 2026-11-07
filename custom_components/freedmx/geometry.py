"""Raumgeometrie für Moving Heads (ohne Home-Assistant-Abhängigkeit, testbar).

Koordinaten (Meter), Blick von der Wand mit den Köpfen in den Raum:
  x = Abstand von der linken Wand (0 … Breite)
  y = Abstand von der Rückwand/Kopfwand (0 … Tiefe)
  Höhe = Montagehöhe des Kopfes über dem Boden

Winkelmodell (am Gerät beobachtet: Pan 50 % / Tilt 50 % = waagerecht nach vorn):
  Richtung a   = Winkel um die Senkrechte, 0° = nach vorn (+y), + = nach rechts (+x)
  Neigung  e   = Winkel unter der Waagerechten, 90° = senkrecht nach unten
  Pan-Grad     = pan_sign  * a + pan_offset   → Pan %  = 50 + Grad / Pan-Bereich  * 100
  Tilt-Grad    = tilt_sign * e + tilt_offset  → Tilt % = 50 + Grad / Tilt-Bereich * 100
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

TOLERANCE = 0.05  # Meter
FAR = 1000.0  # Meter: Ersatzweite, wenn der Strahl nicht auf den Boden trifft

ROOM_WIDTH = "width"
ROOM_DEPTH = "depth"
ROOM_MARGIN = "margin"
ROOM_LIMIT = "limit"

MOUNT_X = "x"
MOUNT_Y = "y"
MOUNT_HEIGHT = "height"
MOUNT_PAN_RANGE = "pan_range"
MOUNT_TILT_RANGE = "tilt_range"
MOUNT_PAN_INVERT = "pan_invert"
MOUNT_TILT_INVERT = "tilt_invert"
MOUNT_PAN_OFFSET = "pan_offset"
MOUNT_TILT_OFFSET = "tilt_offset"
MOUNT_HOME_PAN = "home_pan"
MOUNT_HOME_TILT = "home_tilt"

ROOM_DEFAULTS: dict[str, Any] = {
    ROOM_WIDTH: 3.5,
    ROOM_DEPTH: 10.0,
    ROOM_MARGIN: 0.3,
    ROOM_LIMIT: True,
}
MOUNT_DEFAULTS: dict[str, Any] = {
    MOUNT_X: 1.75,
    MOUNT_Y: 0.3,
    MOUNT_HEIGHT: 2.5,
    MOUNT_PAN_RANGE: 540.0,
    MOUNT_TILT_RANGE: 270.0,
    MOUNT_PAN_INVERT: False,
    MOUNT_TILT_INVERT: False,
    MOUNT_PAN_OFFSET: 0.0,
    MOUNT_TILT_OFFSET: 0.0,
    MOUNT_HOME_PAN: 50.0,
    MOUNT_HOME_TILT: 50.0,
}


@dataclass(frozen=True)
class Room:
    width: float
    depth: float
    margin: float = 0.0
    limit: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Room":
        d = {**ROOM_DEFAULTS, **(data or {})}
        return cls(
            float(d[ROOM_WIDTH]), float(d[ROOM_DEPTH]), float(d[ROOM_MARGIN]), bool(d[ROOM_LIMIT])
        )

    def bounds(self) -> tuple[float, float, float, float]:
        m = max(0.0, min(self.margin, self.width / 2, self.depth / 2))
        return m, self.width - m, m, self.depth - m

    def inside(self, x: float, y: float, tol: float = 0.0) -> bool:
        x0, x1, y0, y1 = self.bounds()
        tol += 1e-9
        return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol


@dataclass(frozen=True)
class Mount:
    x: float
    y: float
    height: float
    pan_range: float = 540.0
    tilt_range: float = 270.0
    pan_invert: bool = False
    tilt_invert: bool = False
    pan_offset: float = 0.0
    tilt_offset: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Mount":
        d = {**MOUNT_DEFAULTS, **(data or {})}
        return cls(
            float(d[MOUNT_X]),
            float(d[MOUNT_Y]),
            max(0.1, float(d[MOUNT_HEIGHT])),
            max(1.0, float(d[MOUNT_PAN_RANGE])),
            max(1.0, float(d[MOUNT_TILT_RANGE])),
            bool(d[MOUNT_PAN_INVERT]),
            bool(d[MOUNT_TILT_INVERT]),
            float(d[MOUNT_PAN_OFFSET]),
            float(d[MOUNT_TILT_OFFSET]),
        )


def _wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def aim_percent(mount: Mount, x: float, y: float) -> tuple[float, float]:
    """Bodenpunkt (x, y) → (Pan %, Tilt %)."""
    dx, dy = x - mount.x, y - mount.y
    horiz = math.hypot(dx, dy)
    a = math.degrees(math.atan2(dx, dy)) if horiz > 1e-6 else 0.0
    e = math.degrees(math.atan2(mount.height, horiz))
    pan_deg = (-a if mount.pan_invert else a) + mount.pan_offset
    tilt_deg = (-e if mount.tilt_invert else e) + mount.tilt_offset
    pan = 50.0 + pan_deg / mount.pan_range * 100.0
    tilt = 50.0 + tilt_deg / mount.tilt_range * 100.0
    return max(0.0, min(100.0, pan)), max(0.0, min(100.0, tilt))


def floor_point(mount: Mount, pan: float, tilt: float) -> tuple[float, float, bool]:
    """(Pan %, Tilt %) → Bodenpunkt (x, y, trifft_boden)."""
    pan_deg = (pan - 50.0) / 100.0 * mount.pan_range - mount.pan_offset
    tilt_deg = (tilt - 50.0) / 100.0 * mount.tilt_range - mount.tilt_offset
    a = -pan_deg if mount.pan_invert else pan_deg
    e = -tilt_deg if mount.tilt_invert else tilt_deg
    e = _wrap180(e)
    if e > 90.0:  # über die Senkrechte hinaus: Strahl zeigt nach hinten
        e = 180.0 - e
        a += 180.0
    elif e < -90.0:
        e = -180.0 - e
        a += 180.0
    hits = e > 0.5
    horiz = mount.height / math.tan(math.radians(e)) if hits else FAR
    horiz = min(horiz, FAR)
    rad = math.radians(a)
    return mount.x + horiz * math.sin(rad), mount.y + horiz * math.cos(rad), hits


def clamp_point(room: Room, mount: Mount, x: float, y: float) -> tuple[float, float]:
    """Punkt in den erlaubten Bereich holen; die Richtung vom Kopf aus bleibt erhalten."""
    x0, x1, y0, y1 = room.bounds()
    if room.inside(x, y):
        return x, y
    # Lotpunkt des Kopfes (in den Raum geholt) als Ursprung des Strahls.
    ox = max(x0, min(x1, mount.x))
    oy = max(y0, min(y1, mount.y))
    dx, dy = x - ox, y - oy
    t = 1.0
    if dx > 1e-9:
        t = min(t, (x1 - ox) / dx)
    elif dx < -1e-9:
        t = min(t, (x0 - ox) / dx)
    if dy > 1e-9:
        t = min(t, (y1 - oy) / dy)
    elif dy < -1e-9:
        t = min(t, (y0 - oy) / dy)
    t = max(0.0, t)
    cx, cy = ox + dx * t, oy + dy * t
    return max(x0, min(x1, cx)), max(y0, min(y1, cy))


def limit_percent(
    room: Room, mount: Mount, pan: float, tilt: float
) -> tuple[float, float, float, float]:
    """Pan/Tilt so begrenzen, dass der Lichtpunkt im Raum bleibt.

    Rückgabe: (Pan %, Tilt %, x, y) – unverändert, wenn der Punkt schon erlaubt ist.
    """
    x, y, hits = floor_point(mount, pan, tilt)
    if not room.limit:
        return pan, tilt, x, y
    # 5 cm Toleranz: die 16-Bit-Rundung soll keine Dauerkorrektur auslösen.
    if hits and room.inside(x, y, TOLERANCE):
        return pan, tilt, x, y
    cx, cy = clamp_point(room, mount, x, y)
    new_pan, new_tilt = aim_percent(mount, cx, cy)
    return new_pan, new_tilt, cx, cy


def calibrate_offsets(
    mount: Mount, pan: float, tilt: float, x: float, y: float
) -> tuple[float, float]:
    """Versätze so bestimmen, dass (Pan %, Tilt %) genau auf den gemessenen Bodenpunkt zeigt.

    Rückgabe: (pan_offset, tilt_offset) in Grad. Bereiche und Umkehrung bleiben unverändert.
    """
    dx, dy = x - mount.x, y - mount.y
    horiz = math.hypot(dx, dy)
    a = math.degrees(math.atan2(dx, dy)) if horiz > 1e-6 else 0.0
    e = math.degrees(math.atan2(mount.height, horiz))
    pan_cmd = (pan - 50.0) / 100.0 * mount.pan_range
    tilt_cmd = (tilt - 50.0) / 100.0 * mount.tilt_range
    pan_offset = _wrap180(pan_cmd - (-a if mount.pan_invert else a))
    tilt_offset = _wrap180(tilt_cmd - (-e if mount.tilt_invert else e))
    return round(pan_offset, 2), round(tilt_offset, 2)
