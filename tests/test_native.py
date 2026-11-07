"""Tests gegen die Byte-Beispiele aus freedmxprotocol.txt und gegen einen simulierten freeDMX."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "freedmx"

# Paket ohne __init__ laden (Home Assistant ist hier nicht installiert)
pkg = types.ModuleType("fdmx")
pkg.__path__ = [str(ROOT)]
sys.modules["fdmx"] = pkg

# Minimal-Stubs für homeassistant.core, nur für device.py
ha = types.ModuleType("homeassistant")
ha_core = types.ModuleType("homeassistant.core")
class HomeAssistant: ...
ha_core.HomeAssistant = HomeAssistant
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.core"] = ha_core

from fdmx import protocol as p          # noqa: E402
from fdmx import device as d            # noqa: E402
from fdmx import scenes as sc           # noqa: E402


def word(frame, ch):
    i = ch * 3
    return frame[i:i + 3].hex(" ").upper()


def test_protocol_words_match_document():
    frame = p.build_frame(bytes(512))
    assert len(frame) == 1536
    assert word(frame, 0) == "C0 00 00"       # Kanal 1
    assert word(frame, 127) == "C0 7F 00"
    assert word(frame, 128) == "C2 00 00"
    assert word(frame, 256) == "C4 00 00"
    assert word(frame, 384) == "C6 00 00"
    assert word(frame, 510) == "C6 7E 00"
    assert word(frame, 511) == "86 7F 00"     # letzter Kanal, NTC = 0

def test_value_bit7_and_low_bits():
    vals = bytearray(512); vals[0] = 200; vals[1] = 0x80
    frame = p.build_frame(vals)
    assert word(frame, 0) == "C1 00 48"       # 200 = 1100 1000
    assert word(frame, 1) == "C1 01 00"       # 128 -> Bit 7 gesetzt, unteren Bits 0

def test_datagram_split_matches_document():
    dgs = p.build_datagrams(bytes(512))
    assert [len(x) for x in dgs] == [250] * 6 + [36]
    assert dgs[0][:3].hex(" ").upper() == "C0 00 00"
    assert dgs[1][:3].hex(" ").upper() == "53 00 C0"   # Dokument: "53 xx C0 54 xx"
    assert dgs[6][:3].hex(" ").upper() == "C6 74 00"   # Dokument: "C6 74 xx"

def test_constants():
    assert p.HANDSHAKE == bytes.fromhex("E5 39 60 00")
    assert p.TERMINATE == bytes.fromhex("AC 00")
    assert p.ACK_WORD == bytes.fromhex("F0 64")
    assert p.DEFAULT_PORT == 10100 and p.DEFAULT_HOST == "192.168.4.1"

def test_layout_and_lamp_values():
    L = p.parse_layout("d, r,g ,b,0,x,128")
    assert L == ["D", "R", "G", "B", 0, "X", 128]
    assert p.compute_values(L, "rgb", True, 128, (255, 10, 0)) == [128, 255, 10, 0, 0, 0, 128]
    assert p.compute_values(L, "rgb", False, 128, (255, 10, 0)) == [0, 0, 0, 0, 0, 0, 128]
    assert p.compute_values(["R", "G", "B"], "rgb", True, 128, (255, 100, 0)) == [128, 50, 0]
    for bad in ["", "Q", "300", "R,,-1"]:
        try:
            p.parse_layout(bad)
            raise AssertionError("fehlender Fehler: " + bad)
        except ValueError:
            pass


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
    def async_create_background_task(self, coro, name=None):
        return asyncio.create_task(coro)


async def fake_freedmx(port, log):
    """Simuliert das Gerät: antwortet auf Handshake, schickt jede Sekunde F0 64."""
    loop = asyncio.get_running_loop()

    class Proto(asyncio.DatagramProtocol):
        def __init__(self):
            self.transport = None
            self.addr = None
        def connection_made(self, transport):
            self.transport = transport
        def datagram_received(self, data, addr):
            self.addr = addr
            log.append(data)
            if data == p.HANDSHAKE:
                self.transport.sendto(b"\xA7", addr)
                self.transport.sendto(b"\xAA", addr)
                self.ack = loop.call_later(1.0, self.beat)
            elif data == p.TERMINATE:
                log.append(b"<terminated>")
        def beat(self):
            if self.transport and self.addr:
                self.transport.sendto(p.ACK_WORD, self.addr)
                self.ack = loop.call_later(1.0, self.beat)

    transport, proto = await loop.create_datagram_endpoint(Proto, local_addr=("127.0.0.1", port))
    return transport


def test_device_end_to_end():
    async def run():
        port = 19100
        log = []
        server = await fake_freedmx(port, log)
        dev = d.FreeDmxDevice(FakeHass(), "127.0.0.1", port, None)
        states = []
        dev.add_listener(lambda: states.append(dev.connected))
        await dev.async_start()
        dev.set_channels(1, [255, 0, 128])
        await asyncio.sleep(1.5)
        assert dev.connected, "Gerät sollte nach Handshake verbunden sein"
        assert log[0] == p.HANDSHAKE
        frames = [x for x in log if len(x) == 250 or len(x) == 36]
        assert len(frames) >= 7 * 10, f"zu wenige Frame-Pakete: {len(frames)}"
        assert frames[0][:3].hex(" ").upper() == "C1 00 7F"   # Kanal 1 = 255
        await dev.async_stop()
        await asyncio.sleep(0.1)
        assert log[-1] == b"<terminated>"
        server.close()
        assert states[0] is True and states[-1] is False
    asyncio.run(run())


def test_position_tokens_and_fine_bytes():
    L = p.parse_layout("P,T,0,0,0,0,0,0,0,0,D,0,p,t")
    assert L[0] == "P" and L[12] == "p" and L[13] == "t"
    assert p.parse_layout("p,t") == ["p", "t"]
    vals = p.compute_values(L, "brightness", False, 0, None, (0x1234, 0xABCD))
    assert vals[0] == 0x12 and vals[12] == 0x34     # Pan grob / fein
    assert vals[1] == 0xAB and vals[13] == 0xCD     # Tilt grob / fein
    assert vals[10] == 0                            # Dimmer aus
    assert p.read_position(L, vals) == (0x1234, 0xABCD)

def test_position_survives_lamp_off():
    L = ["P", "p", "T", "t", "D"]
    on = p.compute_values(L, "brightness", True, 255, None, (30000, 500))
    off = p.compute_values(L, "brightness", False, 255, None, (30000, 500))
    assert on[:4] == off[:4] and off[4] == 0

def test_percent_mapping_and_no_position():
    assert p.percent_to_16bit(0) == 0 and p.percent_to_16bit(100) == 65535
    assert p.percent_to_16bit(150) == 65535
    assert p.read_position(["D", "R"], [1, 2]) is None

def test_scene_capture_and_position_writes():
    lamp = {"id": "a1", "start_address": 130, "layout": "P,T,0,0,0,0,0,0,0,0,D,0,p,t"}
    writes = sc.position_writes(lamp, 0x1234, 0xABCD)
    assert (130, 0x12) in writes and (131, 0xAB) in writes
    assert (142, 0x34) in writes and (143, 0xCD) in writes
    assert len(writes) == 4
    assert sc.lamp_span(lamp) == (130, 14)
    scene = sc.make_scene("Test", {"a1": [1, 2, 3]})
    assert scene["name"] == "Test" and scene["channels"]["a1"] == [1, 2, 3]
    assert sc.position_writes({"start_address": 1, "layout": "D,R"}, 0, 0) == []
    pos = sc.make_position("Mitte", 50, 25)
    assert sc.position_16bit(pos) == (32768, 16384)


from fdmx import geometry as g          # noqa: E402

ROOM = g.Room(3.5, 10.0, 0.3, True)
HEAD = g.Mount(x=1.0, y=0.3, height=2.5)


def test_reference_forward_is_center():
    pan, tilt = g.aim_percent(HEAD, 1.0, 1000.0)  # weit vorn ~ waagerecht
    assert abs(pan - 50) < 0.01 and abs(tilt - 50) < 0.2


def test_aim_roundtrip():
    for x, y in [(0.5, 2.0), (3.0, 9.0), (1.0, 0.3 + 1e-3), (2.2, 5.5)]:
        pan, tilt = g.aim_percent(HEAD, x, y)
        fx, fy, hits = g.floor_point(HEAD, pan, tilt)
        assert hits and abs(fx - x) < 1e-6 and abs(fy - y) < 1e-6, (x, y, fx, fy)


def test_straight_down_and_inverted():
    pan, tilt = g.aim_percent(HEAD, 1.0, 0.3)
    assert abs(tilt - (50 + 90 / 270 * 100)) < 1e-6
    inv = g.Mount(x=1.0, y=0.3, height=2.5, tilt_invert=True, pan_invert=True)
    pan2, tilt2 = g.aim_percent(inv, 3.0, 4.0)
    assert abs(tilt2 - (100 - g.aim_percent(HEAD, 3.0, 4.0)[1])) < 1e-6
    assert pytest_approx(g.floor_point(inv, pan2, tilt2)[:2]) == pytest_approx(g.floor_point(HEAD, *g.aim_percent(HEAD, 3.0, 4.0))[:2])


def pytest_approx(t):
    return tuple(round(v, 6) for v in t)


def test_limit_keeps_inside_and_clamps_outside():
    pan, tilt = g.aim_percent(HEAD, 2.0, 5.0)
    assert g.limit_percent(ROOM, HEAD, pan, tilt)[:2] == (pan, tilt)
    # 50/50 = waagerecht -> trifft den Boden nie -> wird an die Vorderkante geholt
    np_, nt, x, y = g.limit_percent(ROOM, HEAD, 50, 50)
    assert ROOM.inside(x, y) and abs(y - 9.7) < 1e-6 and abs(x - 1.0) < 1e-6
    # nach rechts aus dem Raum (x = 6 m) -> Rand bei 3,2 m, Richtung bleibt
    pan, tilt = g.aim_percent(HEAD, 6.0, 5.0)
    _, _, x, y = g.limit_percent(ROOM, HEAD, pan, tilt)
    assert abs(x - 3.2) < 1e-6 and 0.3 < y < 5.0
    # nach oben (Tilt unter 50 %) -> Boden vorn
    _, _, x, y = g.limit_percent(ROOM, HEAD, 50, 20)
    assert ROOM.inside(x, y)


def test_guard_corrects_buffer():
    lamp = {"id": "a", "start_address": 130, "layout": "P,T,0,0,0,0,0,0,0,255,D,0,p,t",
            "mount": {"x": 1.0, "y": 0.3, "height": 2.5}}
    guard = sc.make_guard(lamp, {"width": 3.5, "depth": 10, "margin": 0.3, "limit": True})
    buf = bytearray(512)
    for ch, v in sc.position_writes(lamp, p.POSITION_CENTER, p.POSITION_CENTER):
        buf[ch - 1] = v
    assert guard(buf, 130, 143) is True
    t = sc.lamp_target(lamp, None, buf)
    assert t["boden"] and 9.6 < t["y"] < 9.8
    assert guard(buf, 130, 143) is False          # jetzt erlaubt
    assert guard(buf, 1, 10) is False             # andere Kanäle
    assert sc.make_guard({**lamp, "mount": lamp["mount"]}, {"limit": False}) is None
    assert sc.make_guard({k: v for k, v in lamp.items() if k != "mount"}, None) is None



def test_calibration_recovers_offsets():
    real = g.Mount(x=1.0, y=0.3, height=2.3, pan_offset=-70.0, tilt_offset=19.0)
    assumed = g.Mount(x=1.0, y=0.3, height=2.3)
    pan, tilt = g.aim_percent(assumed, 1.0, 3.3)          # Befehl mit falschen Annahmen
    rx, ry, hit = g.floor_point(real, pan, tilt)          # dort landet der Punkt wirklich
    po, to = g.calibrate_offsets(assumed, pan, tilt, rx, ry)
    assert abs(po + 70.0) < 0.05 and abs(to - 19.0) < 0.05, (po, to)
    fixed = g.Mount(x=1.0, y=0.3, height=2.3, pan_offset=po, tilt_offset=to)
    p2, t2 = g.aim_percent(fixed, 2.0, 6.0)
    fx, fy, _ = g.floor_point(real, p2, t2)
    assert abs(fx - 2.0) < 0.02 and abs(fy - 6.0) < 0.02


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
