#!/usr/bin/env python3
"""神光同步：整机灯效跟着龙屏当前主题走。

经本机 OpenRGB 服务端（127.0.0.1:6742，见 desktop/ryuo-openrgb.service）给主板（含三路 ARGB，
龙王的风扇和冷头灯接在这上面）、内存、显卡推实时颜色。主题、效果、亮度都读
~/.config/ryuo-studio/config.json，管理器里换主题，灯在 1.5 秒内渐变过去。

  python3 -I tools/aura_sync.py               # 常驻
  python3 -I tools/aura_sync.py --preview     # 不连设备，只打印当前主题算出来的颜色

只用实时推色（Direct）模式和临时硬件模式，从不把模式存进设备（见 openrgb_sdk.py）；
重启或断电后各设备回到它自己存的样子。

config 里的键（都可省）：
  lights_enabled      true / false，false 时按 lights_on_exit 处理后空等
  lights_effect       flow 流光 / breathe 呼吸 / static 常亮；主题里写了 lights.effect 时以主题为准
  lights_brightness   0–100
  lights_devices      参与同步的设备类型，默认主板、内存、显卡；键盘要的话加 "keyboard"
  lights_argb_leds    ARGB 接口灯数为 0 时替它设成多少（多设无害，只影响流光长短）
  lights_on_exit      off 关灯 / keep 停在最后一帧
"""
import argparse
import colorsys
import json
import math
import re
import signal
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import openrgb_sdk as sdk  # noqa: E402

CONF = Path.home() / ".config" / "ryuo-studio" / "config.json"
STATUS = Path.home() / ".local" / "share" / "ryuo-studio" / "lights.json"
DEFAULTS = {"theme": "starship", "lights_enabled": True, "lights_effect": "flow", "lights_brightness": 70,
            "lights_devices": ["motherboard", "dram", "gpu"], "lights_argb_leds": 60, "lights_on_exit": "off"}
EFFECTS = ("flow", "breathe", "static")
FPS = 30
SLOW_FPS = 15  # 内存、显卡走 SMBus，推得太勤没意义
FADE_S = 1.5
FLOW_S, BREATHE_S, SWAY_S = 8.0, 7.0, 6.0


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def conf():
    try:
        return {**DEFAULTS, **json.loads(CONF.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def companion(rgb):
    """主题只给了一个主色时，配一个色相偏一点、暗一点的副色，流光才有层次。"""
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))
    r, g, b = colorsys.hsv_to_rgb((h - 0.1) % 1.0, min(1.0, s * 1.1), v * 0.55)
    return (round(r * 255), round(g * 255), round(b * 255))


def css_vars(theme):
    try:
        css = (ROOT / "themes" / theme / "theme.css").read_text()
    except OSError:
        return {}
    return {m[0]: m[1] for m in re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})", css)}


def palette(theme, c):
    """返回 (效果, [主色, 副色])，和屏上面板同一对颜色：主色取标题色，副色取 theme.css 的 --accent2。
    主题可在 theme.json 里写 lights.colors / lights.effect 单独给灯定。"""
    try:
        t = json.loads((ROOT / "themes" / theme / "theme.json").read_text())
    except (OSError, ValueError):
        t = {}
    lights = t.get("lights") or {}
    cols = [hex_rgb(x) for x in lights.get("colors", [])][:2]
    if not cols:
        css = css_vars(theme)
        title = t.get("colors", {}).get("title") or css.get("accent") or "#2DE2C8"
        cols = [hex_rgb(title)] + ([hex_rgb(css["accent2"])] if "accent2" in css else [])
    if len(cols) == 1:
        cols.append(companion(cols[0]))
    effect = lights.get("effect") or c["lights_effect"]
    return (effect if effect in EFFECTS else "flow"), cols


def mix(a, b, w):
    return tuple(a[i] + (b[i] - a[i]) * w for i in range(3))


def frame(effect, cols, t, n, brightness):
    """n 颗灯在 t 秒时的颜色。位置按区内顺序从 0 到 1 均分。"""
    a, b = cols
    scale = max(0, min(100, brightness)) / 100
    out = []
    for k in range(n):
        if effect == "flow":
            p = k / max(1, n)
            c = mix(a, b, 0.5 + 0.5 * math.sin(2 * math.pi * (p - t / FLOW_S)))
            level = 0.85 + 0.15 * math.sin(2 * math.pi * t / SWAY_S)
        elif effect == "breathe":
            c, level = a, 0.25 + 0.75 * (0.5 - 0.5 * math.cos(2 * math.pi * t / BREATHE_S))
        else:
            c, level = a, 1.0
        out.append(tuple(round(x * level * scale) for x in c))
    return out


def device_frame(dev, effect, cols, t, brightness):
    """每个区各自从头算一遍，再按 OpenRGB 的灯序拼起来。"""
    out = []
    for z in dev.zones:
        out += frame(effect, cols, t, z.leds_count, brightness)
    return out


class Fader:
    """换主题或效果时，从旧画面渐变到新画面。"""

    def __init__(self):
        self.cur, self.old, self.since = None, None, 0.0

    def set(self, look, now):
        if look != self.cur:
            self.old, self.cur, self.since = self.cur, look, now

    def weight(self, now):
        if self.old is None:
            return 1.0
        w = (now - self.since) / FADE_S
        if w >= 1:
            self.old = None
            return 1.0
        return w


def look_of(c):
    effect, cols = palette(c["theme"], c)
    return (c["theme"], effect, tuple(cols), int(c["lights_brightness"]))


def render(dev, fader, now):
    _, effect, cols, bright = fader.cur
    new = device_frame(dev, effect, list(cols), now, bright)
    w = fader.weight(now)
    if w >= 1:
        return new
    _, e0, c0, b0 = fader.old
    old = device_frame(dev, e0, list(c0), now, b0)
    return [tuple(round(x) for x in mix(o, n, w)) for o, n in zip(old, new)]


class Lights:
    def __init__(self, host="127.0.0.1", port=6742):
        self.host, self.port = host, port
        self.cli, self.devs, self.last, self.sent_at = None, [], {}, {}

    def connect(self, c):
        self.cli = sdk.Client(self.host, self.port)
        self.setup(c)

    def setup(self, c):
        want = set(c["lights_devices"])
        devs = []
        for d in self.cli.controllers():
            if d.kind not in want:
                continue
            for zi, z in enumerate(d.zones):
                if z.leds_count == 0 and z.leds_max > z.leds_min:
                    self.cli.resize_zone(d.index, zi, max(z.leds_min, min(z.leds_max, int(c["lights_argb_leds"]))))
            d = self.cli.controller(d.index)
            self.cli.set_custom_mode(d.index)
            devs.append(d)
        self.devs, self.last, self.sent_at = devs, {}, {}
        say("灯效设备：" + "、".join(f"{d.name}（{len(d.leds)} 颗）" for d in devs) if devs else "没找到要同步的灯效设备")

    def push(self, fader, now):
        for d in self.devs:
            fps = SLOW_FPS if d.location.startswith("I2C") else FPS
            if now - self.sent_at.get(d.index, 0) < 1 / fps:
                continue
            cols = render(d, fader, now)
            if cols == self.last.get(d.index):
                continue
            self.cli.update_leds(d.index, cols)
            self.last[d.index], self.sent_at[d.index] = cols, now

    def release(self, how):
        """退出或关掉同步时：off 切到硬件的 Off 模式（没有就推全黑），keep 什么都不做。"""
        if self.cli is None or how == "keep":
            return
        for d in self.devs:
            off = d.mode_index("off")
            if off is not None:
                self.cli.update_mode(d.index, off, d.modes[off])
            else:
                self.cli.update_leds(d.index, [(0, 0, 0)] * len(d.leds))
        self.last = {}

    def close(self):
        if self.cli is not None:
            self.cli.close()
        self.cli, self.devs = None, []


def write_status(**kw):
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({"at": int(time.time()), **kw}, ensure_ascii=False))
    except OSError:
        pass


def preview():
    c = conf()
    look = look_of(c)
    print(f"主题 {look[0]} · 效果 {look[1]} · 亮度 {look[3]} · 主色 {look[2][0]} · 副色 {look[2][1]}")
    for t in (0, 2, 4, 6):
        print(f"  {t} 秒：", " ".join("#%02X%02X%02X" % x for x in frame(look[1], list(look[2]), t, 8, look[3])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6742)
    a = ap.parse_args()
    if a.preview:
        return preview()

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    lights, fader = Lights(a.host, a.port), Fader()
    c, conf_at, status_at, backoff, released = conf(), 0.0, 0.0, 2, False
    while not stop:
        now = time.monotonic()
        if now - conf_at >= 1:
            c, conf_at = conf(), now
            fader.set(look_of(c), now)
        try:
            if lights.cli is None:
                lights.connect(c)
                backoff, released = 2, False
                say("已连上 OpenRGB")
            lights.cli.drain()
            if lights.cli.list_changed:
                say("设备有增减，重新识别")
                lights.setup(c)
            if c["lights_enabled"]:
                if released:
                    lights.setup(c)
                    released = False
                lights.push(fader, now)
            elif not released:
                lights.release(c["lights_on_exit"])
                released = True
                say("同步已关")
            if now - status_at >= 5:
                status_at = now
                write_status(running=True, enabled=bool(c["lights_enabled"]), theme=fader.cur[0], effect=fader.cur[1],
                             devices=[{"name": d.name, "kind": d.kind, "leds": len(d.leds)} for d in lights.devs])
        except (OSError, struct.error, ValueError, IndexError) as e:  # 后三种是收到残缺报文
            say(f"OpenRGB 连不上或断了：{e}；{backoff} 秒后重试")
            write_status(running=False, error=str(e))
            lights.close()
            for _ in range(backoff * 10):
                if stop:
                    break
                time.sleep(0.1)
            backoff = min(30, backoff * 2)
            continue
        time.sleep(1 / FPS)
    try:
        lights.release(c["lights_on_exit"])
    except OSError:
        pass
    lights.close()
    write_status(running=False, enabled=bool(c["lights_enabled"]))
    say("已退出")


if __name__ == "__main__":
    main()
