"""神光同步的测试：控制器数据用 2026-10-29 从本机 OpenRGB 1.1 抓下来的真实报文，
同步流程跑在一个真 TCP 的假服务端上，检查实际发出去的报文。"""
import socket
import struct
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import aura_sync  # noqa: E402
import openrgb_sdk as sdk  # noqa: E402

FIX = sorted((HERE / "fixtures").glob("controller*.bin"))
BLOBS = [p.read_bytes() for p in FIX]


def test_parse_real_controllers():
    devs = [sdk.parse_controller(i, b) for i, b in enumerate(BLOBS)]
    assert [d.kind for d in devs] == ["dram", "dram", "gpu", "keyboard", "motherboard"]
    assert devs[2].name == "ASUS ROG ASTRAL GeForce RTX 5090 OC WHITE"
    mb = devs[4]
    assert [z.name for z in mb.zones][1:] == [f"Addressable RGB Header {i}" for i in (1, 2, 3)]
    assert mb.zones[1].leds_max == 500 and mb.zones[1].leds_count == 60
    assert len(mb.leds) == len(mb.colors) == sum(z.leds_count for z in mb.zones)
    for d in devs:
        if d.kind != "keyboard":
            assert d.mode_index("direct") is not None and d.mode_index("off") is not None
    assert len(devs[3].leds) == 84  # 键盘带矩阵排布，解析要能跳过


class FakeServer:
    """按 OpenRGB 的报文格式应答，记下客户端发来的每一个报文。"""

    def __init__(self, blobs):
        self.blobs, self.got = blobs, []
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def reply(self, conn, dev, pid, body):
        conn.sendall(sdk.HEADER.pack(sdk.MAGIC, dev, pid, len(body)) + body)

    def serve(self):
        conn, _ = self.srv.accept()
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            while len(buf) >= 16:
                magic, dev, pid, size = sdk.HEADER.unpack_from(buf)
                if len(buf) < 16 + size:
                    break
                body, buf = buf[16:16 + size], buf[16 + size:]
                self.got.append((dev, pid, body))
                if pid == sdk.REQUEST_PROTOCOL_VERSION:
                    self.reply(conn, 0, pid, struct.pack("<I", 6))
                elif pid == sdk.REQUEST_CONTROLLER_COUNT:
                    self.reply(conn, 0, pid, struct.pack("<I", len(self.blobs)))
                elif pid == sdk.REQUEST_CONTROLLER_DATA:
                    assert struct.unpack("<I", body)[0] == 4  # 服务端报 6，客户端也只按 4 要数据
                    self.reply(conn, dev, pid, self.blobs[dev])

    def sent(self, pid):
        return [(d, b) for d, p, b in self.got if p == pid]


def conf(**kw):
    return {**aura_sync.DEFAULTS, "theme": "aurora", **kw}


def wait_for(cond):
    for _ in range(200):
        if cond():
            return
        threading.Event().wait(0.01)
    raise AssertionError("等不到服务端收到报文")


def test_sync_pushes_selected_devices_and_turns_off_on_exit():
    srv = FakeServer(BLOBS)
    lights, fader = aura_sync.Lights(port=srv.port), aura_sync.Fader()
    c = conf()
    lights.connect(c)
    fader.set(aura_sync.look_of(c), 0.0)
    lights.push(fader, 1.0)
    wait_for(lambda: len(srv.sent(sdk.UPDATELEDS)) == 4)

    assert sorted(d for d, _ in srv.sent(sdk.SETCUSTOMMODE)) == [0, 1, 2, 4]  # 键盘默认不参与
    for dev, body in srv.sent(sdk.UPDATELEDS):
        size, n = struct.unpack_from("<IH", body)
        assert size == len(body) and n == len(sdk.parse_controller(dev, BLOBS[dev]).leds)
        assert body[6:10] != b"\0\0\0\0"  # 推的是主题色，不是黑

    lights.release("off")
    wait_for(lambda: len(srv.sent(sdk.UPDATEMODE)) == 4)
    for dev, body in srv.sent(sdk.UPDATEMODE):
        d = sdk.parse_controller(dev, BLOBS[dev])
        size, idx, nlen = struct.unpack_from("<IiH", body)
        assert size == len(body) and d.modes[idx].name == "Off"
        assert body[10:10 + nlen].rstrip(b"\0") == b"Off"
    assert not srv.sent(1102)  # 从不把模式存进设备
    lights.close()


def test_resize_only_empty_argb_headers():
    # 把主板 3 路 ARGB 的灯数改回 0（OpenRGB 1.1 新装时就是这样），看同步会不会替它设上
    mb = bytearray(BLOBS[4])
    d = sdk.parse_controller(4, bytes(mb))
    for z in d.zones[1:]:
        name = z.name.encode() + b"\0"
        at = mb.find(struct.pack("<H", len(name)) + name) + 2 + len(name) + 4 + 8  # 跳过名字、类型、最小、最大
        assert struct.unpack_from("<I", mb, at)[0] == 60
        struct.pack_into("<I", mb, at, 0)
    assert all(z.leds_count == 0 for z in sdk.parse_controller(4, bytes(mb)).zones[1:])
    srv = FakeServer([BLOBS[0], bytes(mb)])
    lights = aura_sync.Lights(port=srv.port)
    lights.connect(conf(lights_argb_leds=45))
    wait_for(lambda: len(srv.sent(sdk.SETCUSTOMMODE)) == 2)
    sizes = [struct.unpack("<ii", b) + (dev,) for dev, b in srv.sent(sdk.RESIZEZONE)]
    assert sizes == [(1, 45, 1), (2, 45, 1), (3, 45, 1)]
    lights.close()


def test_palette_matches_screen_theme():
    effect, cols = aura_sync.palette("aurora", conf())
    assert effect == "flow"
    assert cols == [(0x5E, 0xF2, 0xB0), (0x8B, 0x6C, 0xFF)]  # 标题色 + theme.css 的 --accent2


def test_fade_between_themes():
    f = aura_sync.Fader()
    f.set(("a", "static", ((255, 0, 0), (0, 0, 0)), 100), 0.0)
    f.set(("b", "static", ((0, 0, 255), (0, 0, 0)), 100), 10.0)
    dev = sdk.parse_controller(0, BLOBS[0])
    mid = aura_sync.render(dev, f, 10.0 + aura_sync.FADE_S / 2)
    assert mid[0] == (128, 0, 128)
    assert aura_sync.render(dev, f, 10.0 + aura_sync.FADE_S + 0.1)[0] == (0, 0, 255)
