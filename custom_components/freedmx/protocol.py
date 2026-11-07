"""freeDMX-AP-Protokoll (nativ, UDP) und Kanalberechnung.

Quelle: freedmxprotocol.txt aus dem QLC+-Plugin von Rodolphe Dejeunes
(github.com/rdejeun/qlcplus, plugins/freedmx), Lizenz CC BY-SA 4.0.
Die Protokollangaben stammen nicht von Steinigke/Eurolite (laut Dokument nicht validiert).

Ablauf:
  1. Handshake: Client sendet E5 39 60 00. Gerät antwortet A7 und AA.
  2. Danach kommt jede Sekunde F0 64 als Lebenszeichen. Fehlt es, gilt die Verbindung als getrennt.
  3. Alle 40 ms sendet der Client einen Frame aus 512 Kanälen x 3 Byte = 1536 Byte,
     aufgeteilt in 7 UDP-Pakete (6 x 250 Byte + 36 Byte).
  4. Ende: Client sendet AC 00.

Kanalwort (3 Byte je DMX-Kanal, Kanal 0-basiert):
  B1 = <1> NTC <0> <0> <0> Nb9 Nb8 Vb8   (NTC = 1 außer beim letzten Kanal)
  B2 = <0> Nb7 ... Nb1
  B3 = <0> Vb7 ... Vb1
"""

from __future__ import annotations

DEFAULT_HOST = "192.168.4.1"
DEFAULT_PORT = 10100

FRAME_CHANNELS = 512
FRAME_BYTES_PER_CHANNEL = 3
DATAGRAM_SIZE = 250
FRAME_INTERVAL = 0.04  # 25 Frames pro Sekunde

HANDSHAKE = b"\xE5\x39\x60\x00"
TERMINATE = b"\xAC\x00"
ACK_WORD = b"\xF0\x64"
HANDSHAKE_REPLIES = (b"\xA7", b"\xAA")
ACK_TIMEOUT = 5.0  # Sekunden ohne Lebenszeichen -> getrennt

# Kanal-Wortcode je Kanalbereich (wie im Dokument)
_CODE_0 = 0xC0  # Kanäle 0-127
_CODE_1 = 0xC2  # Kanäle 128-255
_CODE_2 = 0xC4  # Kanäle 256-383
_CODE_3 = 0xC6  # Kanäle 384-510
_CODE_LAST = 0x86  # Kanal 511 (NTC = 0)


def _code_for(channel: int) -> int:
    if channel == FRAME_CHANNELS - 1:
        return _CODE_LAST
    if channel >= 384:
        return _CODE_3
    if channel >= 256:
        return _CODE_2
    if channel >= 128:
        return _CODE_1
    return _CODE_0


def build_frame(values: bytes | bytearray | list[int]) -> bytes:
    """512 DMX-Werte in den 1536-Byte-Frame des freeDMX umwandeln."""
    if len(values) != FRAME_CHANNELS:
        raise ValueError("Es werden genau 512 Werte benötigt")
    frame = bytearray(FRAME_CHANNELS * FRAME_BYTES_PER_CHANNEL)
    for ch in range(FRAME_CHANNELS):
        value = int(values[ch]) & 0xFF
        pos = ch * FRAME_BYTES_PER_CHANNEL
        frame[pos] = _code_for(ch) | ((value >> 7) & 0x01)
        frame[pos + 1] = ch & 0x7F
        frame[pos + 2] = value & 0x7F
    return bytes(frame)


def split_frame(frame: bytes) -> list[bytes]:
    """Frame in UDP-Pakete zu je 250 Byte aufteilen (letztes Paket Rest)."""
    return [frame[i : i + DATAGRAM_SIZE] for i in range(0, len(frame), DATAGRAM_SIZE)]


def build_datagrams(values: bytes | bytearray | list[int]) -> list[bytes]:
    return split_frame(build_frame(values))


# ---------- Kanal-Layout und Lampenwerte ----------

TOKEN_DIMMER = "D"
TOKEN_UNUSED = "X"
TOKEN_PAN = "P"        # Pan grob (High-Byte der 16-Bit-Position)
TOKEN_PAN_FINE = "p"   # Pan fein (Low-Byte)
TOKEN_TILT = "T"       # Tilt grob
TOKEN_TILT_FINE = "t"  # Tilt fein
COLOR_TOKENS = ("R", "G", "B", "W")
POSITION_TOKENS = (TOKEN_PAN, TOKEN_PAN_FINE, TOKEN_TILT, TOKEN_TILT_FINE)
VALID_TOKENS = {TOKEN_DIMMER, TOKEN_UNUSED, *COLOR_TOKENS, *POSITION_TOKENS}

POSITION_MAX = 65535
POSITION_CENTER = 32768

MODE_BRIGHTNESS = "brightness"
MODE_RGB = "rgb"
MODE_RGBW = "rgbw"


def split16(value: int) -> tuple[int, int]:
    """16-Bit-Position in (grob, fein) zerlegen."""
    value = max(0, min(POSITION_MAX, int(value)))
    return value >> 8, value & 0xFF


def percent_to_16bit(percent: float) -> int:
    """0-100 % auf 16-Bit abbilden."""
    return round(max(0.0, min(100.0, float(percent))) / 100 * POSITION_MAX)


def parse_layout(text: str) -> list[str | int]:
    """Kanal-Layout wie 'D,R,G,B,0,X' einlesen.

    D = Dimmer, R/G/B/W = Farbkanäle, X = ungenutzt (0),
    P/T = Pan/Tilt grob, p/t = Pan/Tilt fein (je aus der 16-Bit-Position),
    Zahl 0-255 = fester Wert (z. B. Modus- oder Strobe-Kanal).
    """
    tokens = [t.strip() for t in text.replace(";", ",").split(",")]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise ValueError("Layout ist leer")
    result: list[str | int] = []
    for token in tokens:
        upper = token.upper()
        if token in POSITION_TOKENS:  # p und t sind klein, P und T gross
            result.append(token)
        elif upper in VALID_TOKENS and upper not in POSITION_TOKENS:
            result.append(upper)
        elif token.isdigit() and 0 <= int(token) <= 255:
            result.append(int(token))
        else:
            raise ValueError(f"Ungültiger Kanal: {token}")
    if len(result) > FRAME_CHANNELS:
        raise ValueError("Zu viele Kanäle")
    return result


def layout_to_text(layout: list[str | int]) -> str:
    return ",".join(str(t) for t in layout)


def has_position(layout: list[str | int]) -> bool:
    return any(t in POSITION_TOKENS for t in layout)


def read_position(
    layout: list[str | int], values: list[int]
) -> tuple[int, int] | None:
    """Pan/Tilt (16 Bit) aus den aktuellen Kanalwerten einer Lampe lesen.

    Gibt None zurück, wenn das Layout keine Positionskanäle hat.
    Fehlt ein Feinkanal, wird nur das Grobbyte verwendet.
    """
    if not has_position(layout):
        return None
    pos = {t: values[i] for i, t in enumerate(layout) if t in POSITION_TOKENS}

    def combine(coarse: str, fine: str) -> int:
        high = pos.get(coarse, POSITION_CENTER >> 8)
        low = pos.get(fine, 0)
        return (high << 8) | low

    return combine(TOKEN_PAN, TOKEN_PAN_FINE), combine(TOKEN_TILT, TOKEN_TILT_FINE)


def color_mode_for(layout: list[str | int]) -> str:
    tokens = {t for t in layout if isinstance(t, str)}
    if {"R", "G", "B"} <= tokens:
        return MODE_RGBW if "W" in tokens else MODE_RGB
    return MODE_BRIGHTNESS


def compute_values(
    layout: list[str | int],
    mode: str,
    is_on: bool,
    brightness: int,
    color: tuple[int, ...] | None,
    position: tuple[int, int] | None = None,
) -> list[int]:
    """DMX-Werte einer Lampe aus dem Zustand berechnen.

    Mit Dimmer-Kanal: Dimmer = Helligkeit, Farben unskaliert.
    Ohne Dimmer-Kanal: Farben werden mit der Helligkeit skaliert.
    Position (Pan, Tilt in 0-65535) bleibt auch bei ausgeschalteter Lampe gesetzt.
    """
    has_dimmer = TOKEN_DIMMER in layout
    if mode == MODE_RGB and color:
        colors = dict(zip(("R", "G", "B"), color))
    elif mode == MODE_RGBW and color:
        colors = dict(zip(("R", "G", "B", "W"), color))
    else:
        colors = {t: 255 for t in COLOR_TOKENS}

    pan, tilt = position if position is not None else (POSITION_CENTER, POSITION_CENTER)
    pan_c, pan_f = split16(pan)
    tilt_c, tilt_f = split16(tilt)
    pos_values = {
        TOKEN_PAN: pan_c,
        TOKEN_PAN_FINE: pan_f,
        TOKEN_TILT: tilt_c,
        TOKEN_TILT_FINE: tilt_f,
    }

    bri = max(0, min(255, int(brightness)))
    out: list[int] = []
    for token in layout:
        if isinstance(token, int):
            out.append(token)
        elif token in pos_values:
            out.append(pos_values[token])
        elif token == TOKEN_UNUSED or not is_on:
            out.append(0)
        elif token == TOKEN_DIMMER:
            out.append(bri)
        else:
            value = int(colors.get(token, 0))
            if not has_dimmer:
                value = round(value * bri / 255)
            out.append(max(0, min(255, value)))
    return out
