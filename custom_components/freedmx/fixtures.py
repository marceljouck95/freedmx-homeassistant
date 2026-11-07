"""Kanalbeschreibungen bekannter Geräte (aus den Bedienungsanleitungen).

Jeder Kanal: Name und optional Funktionsbereiche (von, bis, Bezeichnung).
Die Reihenfolge entspricht dem Layout der passenden Voreinstellung in const.PRESETS.
"""

from __future__ import annotations

from typing import Any

R = tuple[int, int, str]

# Futurelight DMH-40, 14-Kanal-Modus (Bedienungsanleitung, englischer Teil)
DMH40: list[dict[str, Any]] = [
    {"name": "Pan", "ranges": []},
    {"name": "Tilt", "ranges": []},
    {"name": "Pan/Tilt-Geschwindigkeit", "ranges": [
        (0, 225, "schnell → langsam"), (226, 235, "Blackout bei Pan/Tilt-Bewegung"),
        (236, 245, "Blackout bei Farb-/Gobowechsel"), (246, 255, "keine Funktion")]},
    {"name": "Farbrad", "ranges": [
        (0, 14, "Offen / Weiß"), (15, 29, "Rot"), (30, 44, "Blau"), (45, 59, "Grün"),
        (60, 74, "Hellgelb"), (75, 89, "Magenta"), (90, 104, "Lachsrot"), (105, 119, "Hellblau"),
        (120, 127, "Pink"), (128, 189, "Regenbogen vorwärts, langsamer werdend"),
        (190, 193, "Stopp"), (194, 255, "Regenbogen rückwärts, schneller werdend")]},
    {"name": "Gobo-Rad", "ranges": [
        (0, 9, "Offen"), (10, 19, "Gobo 1"), (20, 29, "Gobo 2"), (30, 39, "Gobo 3"),
        (40, 49, "Gobo 4"), (50, 59, "Gobo 5"), (60, 69, "Gobo 6"), (70, 79, "Gobo 7"),
        (80, 95, "Gobo 1 Shake"), (96, 111, "Gobo 2 Shake"), (112, 127, "Gobo 3 Shake"),
        (128, 143, "Gobo 4 Shake"), (144, 159, "Gobo 5 Shake"), (160, 175, "Gobo 6 Shake"),
        (176, 191, "Gobo 7 Shake"), (192, 255, "Gobo-Rad dreht, schneller werdend")]},
    {"name": "Gobo-Drehung", "ranges": [
        (0, 127, "Gobo-Index (Winkel)"), (128, 189, "vorwärts, langsamer werdend"),
        (190, 193, "Stopp"), (194, 255, "rückwärts, schneller werdend")]},
    {"name": "Prisma", "ranges": [
        (0, 31, "Offen"), (32, 63, "3-fach-Prisma"), (64, 95, "3D-Prisma"), (96, 127, "Frost"),
        (128, 255, "Makros 1–16")]},
    {"name": "Prisma-Drehung", "ranges": [
        (0, 127, "Prisma-Index"), (128, 189, "vorwärts, langsamer werdend"),
        (190, 193, "Stopp"), (194, 255, "rückwärts, schneller werdend")]},
    {"name": "Fokus", "ranges": [(0, 255, "fern → nah")]},
    {"name": "Shutter / Strobe", "ranges": [
        (0, 31, "Geschlossen"), (64, 95, "Strobe, schneller werdend"),
        (128, 159, "Puls-Effekt"), (192, 223, "Zufalls-Strobe, schneller werdend"),
        (224, 255, "Offen")]},
    {"name": "Dimmer", "ranges": []},
    {"name": "Reset / Programme", "ranges": [
        (0, 19, "normal"), (80, 84, "Reset alles"), (85, 87, "Reset Pan/Tilt"),
        (88, 90, "Reset Farben"), (91, 93, "Reset Gobos"), (97, 99, "Reset andere Motoren"),
        (100, 239, "Interne Programme 1–7"), (240, 255, "Musiksteuerung")]},
    {"name": "Pan fein", "ranges": []},
    {"name": "Tilt fein", "ranges": []},
]

# Eurolite LED FE-1500 Hybrid Laser Flower, 9 Kanäle (Bedienungsanleitung S. 13/14)
FE1500: list[dict[str, Any]] = [
    {"name": "Betriebsart", "ranges": [
        (0, 9, "Blackout"), (10, 24, "Auto 0: alles"), (25, 39, "Auto 1: Laser + LED"),
        (40, 54, "Auto 2: Laser + Strobe"), (55, 69, "Auto 3: LED + Strobe"),
        (70, 84, "Auto 4: LED"), (85, 99, "Auto 5: Laser"), (100, 114, "Auto 6: Strobe-LEDs"),
        (115, 129, "Musik 0: alles"), (130, 144, "Musik 1: Laser + LED"),
        (145, 159, "Musik 2: Laser + Strobe"), (160, 174, "Musik 3: LED + Strobe"),
        (175, 189, "Musik 4: LED"), (190, 204, "Musik 5: Laser"), (205, 219, "Musik 6: Strobe-LEDs"),
        (220, 255, "DMX-Steuerung")]},
    {"name": "LED-Farben", "ranges": [
        (0, 9, "aus"), (10, 14, "Rot"), (15, 19, "Grün"), (20, 24, "Blau"), (25, 29, "Amber"),
        (30, 34, "Weiß"), (35, 39, "Weiß + Rot"), (40, 44, "Rot + Grün"), (45, 49, "Grün + Blau"),
        (50, 54, "Blau + Amber"), (55, 59, "Amber + Weiß"), (60, 64, "Weiß + Grün"),
        (65, 69, "Grün + Amber"), (70, 74, "Amber + Rot"), (75, 79, "Rot + Blau"),
        (80, 84, "Blau + Weiß"), (85, 89, "Rot + Grün + Blau"), (90, 94, "Rot + Grün + Amber"),
        (95, 99, "Rot + Grün + Weiß"), (100, 104, "Rot + Amber + Blau"),
        (105, 109, "Rot + Weiß + Blau"), (110, 114, "Rot + Amber + Weiß"),
        (115, 119, "Amber + Grün + Blau"), (120, 124, "Blau + Grün + Weiß"),
        (125, 129, "Amber + Grün + Weiß"), (130, 134, "Amber + Weiß + Blau"),
        (135, 139, "RGB + Amber"), (140, 144, "RGB + Weiß"), (145, 149, "Blau + Grün + Weiß + Amber"),
        (150, 154, "Rot + Grün + Amber + Weiß"), (155, 159, "Weiß + Rot + Blau + Amber"),
        (160, 164, "alle Farben"), (165, 209, "Farbwechsel (eine Farbe)"),
        (210, 255, "Farbwechsel (zwei Farben)")]},
    {"name": "Farbwechsel-Tempo", "ranges": [(0, 255, "langsam → schnell")]},
    {"name": "LED-Strobe", "ranges": [
        (0, 4, "kein Strobe"), (5, 254, "Strobe, schnell → langsam"), (255, 255, "Musik-Strobe")]},
    {"name": "Weiße Strobe-LEDs", "ranges": [
        (0, 9, "aus"), (10, 19, "Lauflicht 1"), (20, 29, "Lauflicht 2"), (30, 39, "Lauflicht 3"),
        (40, 49, "Lauflicht 4"), (50, 59, "Lauflicht 5"), (60, 69, "Lauflicht 6"),
        (70, 79, "Lauflicht 7"), (80, 89, "Lauflicht 8"), (90, 99, "Lauflicht 9"),
        (100, 109, "Lauflicht 10"), (110, 255, "dauerhaft an")]},
    {"name": "Laser", "ranges": [
        (0, 9, "aus"), (10, 49, "Rot"), (50, 89, "Grün"), (90, 129, "Rot/Grün im Wechsel"),
        (130, 169, "Rot an + Grün Strobe"), (170, 209, "Grün an + Rot Strobe"),
        (210, 255, "Rot + Grün Strobe")]},
    {"name": "Laser-Strobe", "ranges": [
        (0, 4, "kein Strobe"), (5, 254, "Strobe, langsam → schnell"), (255, 255, "Musik-Strobe")]},
    {"name": "LED-Drehung", "ranges": [
        (0, 4, "Stopp"), (5, 127, "vorwärts, langsam → schnell"), (128, 133, "Stopp"),
        (134, 255, "rückwärts, langsam → schnell")]},
    {"name": "Laser-Drehung", "ranges": [
        (0, 4, "Stopp"), (5, 127, "vorwärts, langsam → schnell"), (128, 133, "Stopp"),
        (134, 255, "rückwärts, langsam → schnell")]},
]

# Eurolite LED Bar RGB 126/10, 5 Kanäle (Angabe des Nutzers)
LEDBAR5: list[dict[str, Any]] = [
    {"name": "Rot", "ranges": []},
    {"name": "Grün", "ranges": []},
    {"name": "Blau", "ranges": []},
    {"name": "Dimmer", "ranges": []},
    {"name": "Shutter / Strobe", "ranges": [(0, 24, "aus (kein Strobe)"), (25, 255, "Strobe, schneller werdend")]},
]

PROFILES: dict[str, list[dict[str, Any]]] = {
    "dmh40": DMH40,
    "fe1500": FE1500,
    "ledbar5": LEDBAR5,
}

TOKEN_NAMES = {
    "D": "Dimmer", "R": "Rot", "G": "Grün", "B": "Blau", "W": "Weiß",
    "P": "Pan", "p": "Pan fein", "T": "Tilt", "t": "Tilt fein", "X": "frei",
}


def channel_info(preset: str | None, layout: list) -> list[dict[str, Any]]:
    """Beschreibung je Kanal der Lampe (Name, Funktionsbereiche, steuerbar?)."""
    profile = PROFILES.get(preset or "")
    if profile is not None and len(profile) != len(layout):
        profile = None
    result = []
    for i, token in enumerate(layout):
        entry = profile[i] if profile else None
        name = entry["name"] if entry else (
            TOKEN_NAMES.get(token, f"Kanal {i + 1}") if isinstance(token, str) else f"Kanal {i + 1}"
        )
        result.append({
            "nr": i + 1,
            "name": name,
            # feste Kanäle (Zahl) sind frei einstellbar; Dimmer/Farbe/Pan/Tilt steuert das Licht bzw. der Plan
            "frei": isinstance(token, int) or token == "X",
            "rolle": token if isinstance(token, str) else "fest",
            "bereiche": [list(r) for r in (entry["ranges"] if entry else [])],
        })
    return result
