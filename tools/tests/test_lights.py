"""神光同步的测试：控制器数据用 2026-10-29 从本机 OpenRGB 1.1 抓下来的真实报文，
同步流程跑在一个真 TCP 的假服务端上，检查实际发出去的报文。"""
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import aura_sync  # noqa: E402
import openrgb_sdk as sdk  # noqa: E402

FIX = sorted((HERE / "fixtures").glob("controller*.bin"))
BLOBS = [p.read_bytes() for p in FIX]
DEVS = [sdk.parse_controller(i, b) for i, b in enumerate(BLOBS)]
# 客户端只该发这些报文；SAVEMODE（1102）之类会写进设备存储的不在里面
ALLOWED = {sdk.SET_CLIENT_NAME, sdk.REQUEST_PROTOCOL_VERSION, sdk.REQUEST_CONTROLLER_COUNT, sdk.REQUEST_CONTROLLER_DATA,
           sdk.RESIZEZONE, sdk.UPDATELEDS, sdk.SETCUSTOMMODE, sdk.UPDATEMODE}


def test_parse_real_controllers():
    assert [d.kind for d in DEVS] == ["dram", "dram", "gpu", "keyboard", "motherboard"]
    assert DEVS[2].name == "ASUS ROG ASTRAL GeForce RTX 5090 OC WHITE"
    mb = DEVS[4]
    assert [z.name for z in mb.zones][1:] == [f"Addressable RGB Header {i}" for i in (1, 2, 3)]
    assert mb.zones[1].leds_max == 500 and mb.zones[1].leds_count == 60
    assert len(mb.leds) == len(mb.colors) == sum(z.leds_count for z in mb.zones)
    for d in DEVS:
        if d.kind != "keyboard":
            assert d.mode_index("direct") is not None and d.mode_index("off") is not None
    assert len(DEVS[3].leds) == 84  # 键盘带矩阵排布，解析要能跳过
    assert DEVS[3].modes[DEVS[3].mode_index("custom")].flags & aura_sync.AUTO_SAVE


class FakeServer:
    """按 OpenRGB 的报文格式应答，记下客户端发来的每一个报文。"""

    def __init__(self, blobs):
        self.blobs, self.got, self.conn = blobs, [], None
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def reply(self, dev, pid, body):
        self.conn.sendall(sdk.HEADER.pack(sdk.MAGIC, dev, pid, len(body)) + body)

    def notify_list_changed(self):
        self.reply(0, sdk.DEVICE_LIST_UPDATED, b"")

    def serve(self):
        self.conn, _ = self.srv.accept()
        buf = b""
        while True:
            chunk = self.conn.recv(65536)
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
                    self.reply(0, pid, struct.pack("<I", 6))
                elif pid == sdk.REQUEST_CONTROLLER_COUNT:
                    self.reply(0, pid, struct.pack("<I", len(self.blobs)))
                elif pid == sdk.REQUEST_CONTROLLER_DATA:
                    assert struct.unpack("<I", body)[0] == 4  # 服务端报 6，客户端也只按 4 要数据
                    self.reply(dev, pid, self.blobs[dev])

    def sent(self, pid):
        return [(d, b) for d, p, b in self.got if p == pid]

    def pids(self):
        return {p for _, p, _ in self.got}


def conf(**kw):
    return {**aura_sync.DEFAULTS, "theme": "aurora", **kw}


def wait_for(cond, s=5):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("等不到预期的报文")


def assert_turned_off(srv, devs):
    """每台设备最后收到的是：全黑一帧，再切 Off 模式。"""
    for dev in devs:
        mine = [(p, b) for d, p, b in srv.got if d == dev and p in (sdk.UPDATELEDS, sdk.UPDATEMODE)]
        (p1, leds), (p2, mode) = mine[-2:]
        assert p1 == sdk.UPDATELEDS and set(leds[6:]) == {0}
        size, idx, nlen = struct.unpack_from("<IiH", mode)
        assert p2 == sdk.UPDATEMODE and size == len(mode) and DEVS[dev].modes[idx].name == "Off"
        assert mode[10:10 + nlen].rstrip(b"\0") == b"Off"


def test_sync_pushes_selected_devices_and_turns_off():
    srv = FakeServer(BLOBS)
    lights, fader = aura_sync.Lights(port=srv.port), aura_sync.Fader()
    c = conf()
    lights.connect(c)
    assert not srv.sent(sdk.SETCUSTOMMODE)  # 只识别，不切模式
    lights.activate(c)
    fader.set(aura_sync.look_of(c), 0.0)
    lights.push(fader, 1.0)
    wait_for(lambda: len(srv.sent(sdk.UPDATELEDS)) == 4)
    assert sorted(d for d, _ in srv.sent(sdk.SETCUSTOMMODE)) == [0, 1, 2, 4]  # 键盘默认不参与
    for dev, body in srv.sent(sdk.UPDATELEDS):
        size, n = struct.unpack_from("<IH", body)
        assert size == len(body) and n == len(DEVS[dev].leds)
        assert body[6:10] != b"\0\0\0\0"  # 推的是主题色，不是黑
    lights.release("off")
    assert_turned_off(srv, [0, 1, 2, 4])
    assert srv.pids() <= ALLOWED
    lights.close()


def test_keyboard_with_auto_save_is_never_touched():
    srv = FakeServer(BLOBS)
    lights, fader = aura_sync.Lights(port=srv.port), aura_sync.Fader()
    c = conf(lights_devices=["keyboard", "dram"])
    lights.connect(c)
    lights.activate(c)
    fader.set(aura_sync.look_of(c), 0.0)
    lights.push(fader, 1.0)
    lights.release("off")
    assert {p for d, p, _ in srv.got if d == 3} <= {sdk.REQUEST_CONTROLLER_DATA, 0, sdk.SET_CLIENT_NAME,
                                                    sdk.REQUEST_PROTOCOL_VERSION}
    assert sorted(d for d, _ in srv.sent(sdk.SETCUSTOMMODE)) == [0, 1]
    lights.close()


def test_argb_headers_resized_to_setting():
    srv = FakeServer(BLOBS)
    lights = aura_sync.Lights(port=srv.port)
    c = conf(lights_argb_leds=45)
    lights.connect(c)
    lights.activate(c)
    sizes = [(dev,) + struct.unpack("<ii", b) for dev, b in srv.sent(sdk.RESIZEZONE)]
    assert sizes == [(4, 1, 45), (4, 2, 45), (4, 3, 45)]  # 只动主板可变长的三路，固定长的不碰
    lights.close()
    srv = FakeServer(BLOBS)
    lights = aura_sync.Lights(port=srv.port)
    lights.connect(conf())
    lights.activate(conf())
    assert not srv.sent(sdk.RESIZEZONE)  # 已经是 60 就不再设
    lights.close()


def run_daemon(tmp_path, srv, cfg):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    p = subprocess.Popen([sys.executable, "-I", str(HERE.parent / "aura_sync.py"), "--port", str(srv.port),
                          "--config", str(path), "--status", str(tmp_path / "lights.json")],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p, path


def stop(p):
    p.send_signal(signal.SIGTERM)
    out = p.communicate(timeout=10)[0]
    assert p.returncode == 0, out
    return out


def test_daemon_turns_lights_off_on_sigterm(tmp_path):
    srv = FakeServer(BLOBS)
    p, _ = run_daemon(tmp_path, srv, conf())
    wait_for(lambda: len(srv.sent(sdk.UPDATELEDS)) >= 12)
    out = stop(p)
    assert "已退出" in out
    assert_turned_off(srv, [0, 1, 2, 4])
    assert srv.pids() <= ALLOWED
    assert json.loads((tmp_path / "lights.json").read_text())["running"] is False


def test_disabled_never_switches_modes_even_when_devices_change(tmp_path):
    srv = FakeServer(BLOBS)
    p, _ = run_daemon(tmp_path, srv, conf(lights_enabled=False, lights_on_exit="keep"))
    wait_for(lambda: len(srv.sent(sdk.REQUEST_CONTROLLER_DATA)) >= 5)
    srv.notify_list_changed()  # 插拔 USB 时服务端会发这个
    wait_for(lambda: len(srv.sent(sdk.REQUEST_CONTROLLER_DATA)) >= 10)
    time.sleep(0.3)
    stop(p)
    assert not srv.sent(sdk.SETCUSTOMMODE) and not srv.sent(sdk.UPDATELEDS) and not srv.sent(sdk.UPDATEMODE)


def test_toggle_off_then_on_via_config(tmp_path):
    srv = FakeServer(BLOBS)
    p, path = run_daemon(tmp_path, srv, conf())
    wait_for(lambda: len(srv.sent(sdk.SETCUSTOMMODE)) == 4)
    path.write_text(json.dumps(conf(lights_enabled=False)))
    wait_for(lambda: len(srv.sent(sdk.UPDATEMODE)) == 4)
    assert_turned_off(srv, [0, 1, 2, 4])
    path.write_text(json.dumps(conf()))
    wait_for(lambda: len(srv.sent(sdk.SETCUSTOMMODE)) == 8)
    stop(p)


def test_bad_config_values_keep_previous():
    prev = conf(lights_brightness=40)
    got = aura_sync.clean({"theme": "../etc", "lights_brightness": None, "lights_effect": "rainbow",
                           "lights_argb_leds": True, "lights_devices": "dram", "lights_enabled": "yes"}, prev)
    for k in ("theme", "lights_brightness", "lights_effect", "lights_argb_leds", "lights_devices", "lights_enabled"):
        assert got[k] == prev[k], k
    assert aura_sync.clean([1, 2], prev) == prev
    assert aura_sync.clean({"lights_brightness": "55"}, prev)["lights_brightness"] == 55
    assert aura_sync.clean({}, prev)["lights_brightness"] == aura_sync.DEFAULTS["lights_brightness"]


def test_half_written_config_keeps_previous(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"theme": "neon", "lights_ena')
    prev = conf()
    assert aura_sync.load_conf(path, prev) == prev


def test_broken_theme_falls_back(tmp_path, monkeypatch):
    (tmp_path / "themes" / "bad").mkdir(parents=True)
    (tmp_path / "themes" / "bad" / "theme.json").write_text(json.dumps({"colors": None, "lights": {"colors": ["#fff"]}}))
    monkeypatch.setattr(aura_sync, "ROOT", tmp_path)
    effect, cols = aura_sync.palette("bad", conf())
    assert effect == "flow" and cols[0] == aura_sync.hex_rgb(aura_sync.FALLBACK)


def test_palette_matches_screen_theme():
    effect, cols = aura_sync.palette("aurora", conf())
    assert effect == "flow"
    assert cols == [(0x5E, 0xF2, 0xB0), (0x8B, 0x6C, 0xFF)]  # 标题色 + theme.css 的 --accent2


def test_fade_between_themes():
    f = aura_sync.Fader()
    f.set(("a", "static", ((255, 0, 0), (0, 0, 0)), 100), 0.0)
    f.set(("b", "static", ((0, 0, 255), (0, 0, 0)), 100), 10.0)
    mid = aura_sync.render(DEVS[0], f, 10.0 + aura_sync.FADE_S / 2)
    assert mid[0] == (128, 0, 128)
    assert aura_sync.render(DEVS[0], f, 10.0 + aura_sync.FADE_S + 0.1)[0] == (0, 0, 255)


def test_static_colour_is_resent_as_keepalive():
    srv = FakeServer(BLOBS[:1])
    lights, fader = aura_sync.Lights(port=srv.port), aura_sync.Fader()
    c = conf(lights_effect="static")
    lights.connect(c)
    lights.activate(c)
    fader.set(("x", "static", ((10, 20, 30), (0, 0, 0)), 100), 0.0)
    for t in (5.0, 5.5, 6.0, 7.1):
        lights.push(fader, t)
    wait_for(lambda: len(srv.sent(sdk.UPDATELEDS)) == 2)  # 5.0 推一次；颜色没变，过了 2 秒在 7.1 再补一次
    lights.close()
