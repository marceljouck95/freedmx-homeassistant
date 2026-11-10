"""Szenen- und Positionslogik ohne Home-Assistant-Abhängigkeit (testbar)."""

from __future__ import annotations

import uuid
from typing import Any

from .const import POS_ID, POS_NAME, POS_PAN, POS_TILT
from .protocol import FRAME_CHANNELS, layout_to_text, parse_layout, percent_to_16bit


def new_id() -> str:
    return uuid.uuid4().hex


def make_scene(
    name: str,
    snapshots: dict[str, list[int]],
    states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Szene (Speicherplatz) aus den aktuellen Kanalwerten je Lampe bauen.

    states: optional je Lampe der Licht-Zustand (an, Helligkeit, Farbe, eigene Kanalwerte),
    damit beim Abrufen auch die Anzeige in Home Assistant stimmt.
    """
    scene: dict[str, Any] = {
        "id": new_id(),
        "name": name,
        "channels": {lamp_id: list(values) for lamp_id, values in snapshots.items()},
    }
    if states:
        scene["state"] = {k: dict(v) for k, v in states.items() if k in snapshots}
    return scene


def lamp_span(lamp: dict[str, Any]) -> tuple[int, int]:
    """(Startkanal, Länge) einer Lampe."""
    layout = parse_layout(lamp["layout"])
    return int(lamp["start_address"]), len(layout)


def position_writes(
    lamp: dict[str, Any], pan_16: int, tilt_16: int
) -> list[tuple[int, int]]:
    """Liste (DMX-Kanal, Wert) für Pan/Tilt einer Lampe. Leer ohne Positionskanäle."""
    from .protocol import TOKEN_PAN, TOKEN_PAN_FINE, TOKEN_TILT, TOKEN_TILT_FINE, split16

    layout = parse_layout(lamp["layout"])
    start = int(lamp["start_address"])
    pan_c, pan_f = split16(pan_16)
    tilt_c, tilt_f = split16(tilt_16)
    values = {
        TOKEN_PAN: pan_c,
        TOKEN_PAN_FINE: pan_f,
        TOKEN_TILT: tilt_c,
        TOKEN_TILT_FINE: tilt_f,
    }
    writes = []
    for offset, token in enumerate(layout):
        if token in values:
            channel = start + offset
            if 1 <= channel <= FRAME_CHANNELS:
                writes.append((channel, values[token]))
    return writes


def position_16bit(position: dict[str, Any]) -> tuple[int, int]:
    return percent_to_16bit(position[POS_PAN]), percent_to_16bit(position[POS_TILT])


def make_position(name: str, pan: float, tilt: float) -> dict[str, Any]:
    return {POS_ID: new_id(), POS_NAME: name, POS_PAN: float(pan), POS_TILT: float(tilt)}


__all__ = [
    "layout_to_text",
    "lamp_span",
    "make_position",
    "make_scene",
    "new_id",
    "position_16bit",
    "position_writes",
    "make_guard",
    "lamp_target",
]


# ---------- Raum-Begrenzung ----------
LAMP_MOUNT = "mount"


def lamp_target(lamp: dict[str, Any], room_data: dict | None, buf) -> dict | None:
    """Aktuellen Lichtpunkt einer Lampe aus dem Kanalpuffer (x, y in Metern)."""
    from .geometry import Mount, floor_point
    from .protocol import POSITION_MAX, read_position

    if LAMP_MOUNT not in lamp:
        return None
    layout = parse_layout(lamp["layout"])
    start = int(lamp["start_address"])
    pos = read_position(layout, list(buf[start - 1 : start - 1 + len(layout)]))
    if pos is None:
        return None
    mount = Mount.from_dict(lamp[LAMP_MOUNT])
    pan = pos[0] / POSITION_MAX * 100
    tilt = pos[1] / POSITION_MAX * 100
    x, y, hits = floor_point(mount, pan, tilt)
    return {"x": round(x, 2), "y": round(y, 2), "boden": hits, "pan": round(pan, 2), "tilt": round(tilt, 2)}


def make_guard(lamp: dict[str, Any], room_data: dict | None):
    """Funktion, die den Kanalpuffer so korrigiert, dass der Lichtpunkt im Raum bleibt.

    None, wenn die Lampe keine Positionskanäle oder keine Montagedaten hat.
    """
    from .geometry import Mount, Room, limit_percent
    from .protocol import POSITION_MAX, read_position

    layout = parse_layout(lamp["layout"])
    if LAMP_MOUNT not in lamp or not any(t in ("P", "T", "p", "t") for t in layout):
        return None
    room = Room.from_dict(room_data)
    if not room.limit:
        return None
    mount = Mount.from_dict(lamp[LAMP_MOUNT])
    start = int(lamp["start_address"])
    end = start + len(layout) - 1

    def guard(buf, first: int, last: int) -> bool:
        if last < start or first > end:
            return False
        current = read_position(layout, list(buf[start - 1 : end]))
        if current is None:
            return False
        pan = current[0] / POSITION_MAX * 100
        tilt = current[1] / POSITION_MAX * 100
        new_pan, new_tilt, _, _ = limit_percent(room, mount, pan, tilt)
        if abs(new_pan - pan) < 1e-6 and abs(new_tilt - tilt) < 1e-6:
            return False
        for channel, value in position_writes(
            lamp, percent_to_16bit(new_pan), percent_to_16bit(new_tilt)
        ):
            buf[channel - 1] = value
        return True

    return guard
