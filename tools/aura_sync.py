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
  lights_devices      参与同步的设备类型，默认主板、内存、显卡
  lights_argb_leds    主板每路 ARGB 接口设成多少颗灯（多设无害，只影响流光长短）
  lights_on_exit      off 关灯 / keep 停在最后一帧

模式带「自动存盘」标记的设备（比如 Keychron 键盘，一切模式就写进键盘存储）一律跳过，
写进 lights_devices 也不碰。同步关着时只识别设备，不切任何模式。
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
KEEPALIVE_S = 2.0  # 颜色没变也隔这么久重推一次，设备丢了一帧也能补上
AUTO_SAVE = 1 << 9  # OpenRGB 的 MODE_FLAG_AUTOMATIC_SAVE
FALLBACK = "#2DE2C8"


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


CHECKS = {
    "theme": (None, lambda v: isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]+", v)),
    "lights_enabled": (None, lambda v: isinstance(v, bool)),
    "lights_effect": (str, lambda v: v in EFFECTS),
    "lights_brightness": (int, lambda v: type(v) is int and 0 <= v <= 100),
    "lights_devices": (None, lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)),
    "lights_argb_leds": (int, lambda v: type(v) is int and 1 <= v <= 500),
    "lights_on_exit": (str, lambda v: v in ("off", "keep")),
}


def clean(raw, prev):
    """缺的键用默认值；写坏的键（类型不对、超范围）沿用上一次的值。"""
    if not isinstance(raw, dict):
        return dict(prev)
    out = {**raw, **DEFAULTS}
    for key, (conv, ok) in CHECKS.items():
        if key not in raw:
            continue
        try:
            v = conv(raw[key]) if conv and not isinstance(raw[key], bool) else raw[key]
            out[key] = v if ok(v) else prev[key]
        except (TypeError, ValueError, OverflowError):  # json 认 Infinity、1e400，int() 会溢出
            out[key] = prev[key]
    return out


def load_conf(path, prev):
    """读不到或读到半截（管理器正在写）就整份沿用上一次的。"""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(prev)
    return clean(raw, prev)


def hex_rgb(h):
    if not isinstance(h, str) or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", h):
        raise ValueError(f"不是 #RRGGBB 颜色：{h!r}")
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
    主题可在 theme.json 里写 lights.colors / lights.effect 单独给灯定。主题文件写坏了就用默认青色。"""
    try:
        t = json.loads((ROOT / "themes" / theme / "theme.json").read_text())
        lights = t.get("lights") or {}
        cols = [hex_rgb(x) for x in lights.get("colors") or []][:2]
        if not cols:
            css = css_vars(theme)
            title = (t.get("colors") or {}).get("title") or css.get("accent") or FALLBACK
            cols = [hex_rgb(title)] + ([hex_rgb(css["accent2"])] if "accent2" in css else [])
        effect = lights.get("effect")
    except (OSError, ValueError, TypeError, AttributeError):
        cols, effect = [hex_rgb(FALLBACK)], None
    if len(cols) == 1:
        cols.append(companion(cols[0]))
    effect = effect if effect in EFFECTS else c["lights_effect"]
    return effect, cols


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
    return (c["theme"], effect, tuple(cols), c["lights_brightness"])


def render(dev, fader, now):
    _, effect, cols, bright = fader.cur
    new = device_frame(dev, effect, list(cols), now, bright)
    w = fader.weight(now)
    if w >= 1:
        return new
    _, e0, c0, b0 = fader.old
    old = device_frame(dev, e0, list(c0), now, b0)
    return [tuple(round(x) for x in mix(o, n, w)) for o, n in zip(old, new)]


def setup_key(c):
    return (tuple(c["lights_devices"]), c["lights_argb_leds"])


class Lights:
    def __init__(self, host="127.0.0.1", port=6742):
        self.host, self.port = host, port
        self.cli, self.devs, self.key, self.active = None, [], None, False
        self.last, self.sent_at = {}, {}

    def connect(self, c):
        self.cli = sdk.Client(self.host, self.port)
        self.setup(c)

    def setup(self, c):
        """只识别要同步的设备，不切模式（同步关着时也会走到这里）。"""
        want, devs = set(c["lights_devices"]), []
        for d in self.cli.controllers():
            if d.kind not in want:
                continue
            direct = d.mode_index("direct")  # 和 OpenRGB 的 SetCustomMode 同样先挑 Direct 再挑 Custom
            direct = d.mode_index("custom") if direct is None else direct
            used = [i for i in (direct, d.mode_index("off")) if i is not None]
            if direct is None or any(d.modes[i].flags & AUTO_SAVE for i in used):
                say(f"跳过 {d.name}：" + ("没有实时推色模式" if direct is None else "切模式会写进设备存储"))
                continue
            devs.append(d)
        self.devs, self.key, self.active = devs, setup_key(c), False
        say("灯效设备：" + "、".join(f"{d.name}（{len(d.leds)} 颗）" for d in devs) if devs else "没找到要同步的灯效设备")

    def activate(self, c):
        """切到实时推色；主板 ARGB 接口的灯数不对就先设好。"""
        for k, d in enumerate(self.devs):
            sized = False
            for zi, z in enumerate(d.zones):
                want = max(z.leds_min, min(z.leds_max, c["lights_argb_leds"]))
                if z.leds_max > z.leds_min and z.leds_count != want:
                    self.cli.resize_zone(d.index, zi, want)
                    sized = True
            if sized:
                self.devs[k] = d = self.cli.controller(d.index)
            self.cli.set_custom_mode(d.index)
        self.active, self.last, self.sent_at = True, {}, {}

    def push(self, fader, now):
        for d in self.devs:
            fps = SLOW_FPS if d.location.startswith("I2C") else FPS
            since = now - self.sent_at.get(d.index, -KEEPALIVE_S)
            if since < 1 / fps:
                continue
            cols = render(d, fader, now)
            if cols == self.last.get(d.index) and since < KEEPALIVE_S:
                continue
            self.cli.update_leds(d.index, cols)
            self.last[d.index], self.sent_at[d.index] = cols, now

    def release(self, how):
        """退出或关掉同步时：off 先推全黑再切硬件的 Off 模式，keep 停在最后一帧。
        先推黑是因为 OpenRGB 可能在切完 Off 之后才把排着队的上一帧写下去。"""
        if self.cli is None or not self.active:
            return
        self.active = False
        if how == "keep":
            return
        for d in self.devs:
            self.cli.update_leds(d.index, [(0, 0, 0)] * len(d.leds))
            off = d.mode_index("off")
            if off is not None:
                self.cli.update_mode(d.index, off, d.modes[off])
        for d in self.devs:  # 等服务端把排队的命令处理完再走，断得太快它可能来不及
            self.cli.controller(d.index)
        self.last = {}

    def close(self):
        if self.cli is not None:
            self.cli.close()
        self.cli, self.devs, self.active = None, [], False


def write_status(path, **kw):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": int(time.time()), **kw}, ensure_ascii=False))
    except OSError:
        pass


def preview(path):
    c = load_conf(path, DEFAULTS)
    look = look_of(c)
    print(f"主题 {look[0]} · 效果 {look[1]} · 亮度 {look[3]} · 主色 {look[2][0]} · 副色 {look[2][1]}")
    for t in (0, 2, 4, 6):
        print(f"  {t} 秒：", " ".join("#%02X%02X%02X" % x for x in frame(look[1], list(look[2]), t, 8, look[3])))


NET_ERRORS = (OSError, struct.error, ValueError, IndexError)  # 后三种是收到残缺报文


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6742)
    ap.add_argument("--config", type=Path, default=CONF)
    ap.add_argument("--status", type=Path, default=STATUS)
    a = ap.parse_args()
    if a.preview:
        return preview(a.config)

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    lights, fader = Lights(a.host, a.port), Fader()
    c, conf_at, status_at, backoff = dict(DEFAULTS), float("-inf"), float("-inf"), 2
    skew = time.time() - time.monotonic()
    try:
        while not stop:
            now = time.monotonic()
            if now - conf_at >= 1:
                c, conf_at = load_conf(a.config, c), now
                fader.set(look_of(c), now)
            # 睡眠时单调时钟不走、墙上时钟走，两者差值一跳就是刚醒过来：设备可能掉回了自己存的模式
            resumed = abs(time.time() - now - skew) > 5
            skew = time.time() - now
            try:
                if lights.cli is None:
                    lights.connect(c)
                    backoff = 2
                    say("已连上 OpenRGB")
                lights.cli.drain()
                if lights.cli.list_changed or lights.key != setup_key(c):
                    if lights.key != setup_key(c):
                        lights.release(c["lights_on_exit"])  # 被移出名单的设备也要放掉
                    say("设备有增减或设置变了，重新识别")
                    lights.setup(c)
                if c["lights_enabled"]:
                    if not lights.active or resumed:
                        lights.activate(c)
                    lights.push(fader, now)
                elif lights.active:
                    lights.release(c["lights_on_exit"])
                    say("同步已关")
                if now - status_at >= 5:
                    status_at = now
                    write_status(a.status, running=True, enabled=c["lights_enabled"], theme=fader.cur[0],
                                 effect=fader.cur[1],
                                 devices=[{"name": d.name, "kind": d.kind, "leds": len(d.leds)} for d in lights.devs])
            except NET_ERRORS as e:
                say(f"OpenRGB 连不上或断了：{e}；{backoff} 秒后重试")
                write_status(a.status, running=False, error=str(e))
                lights.close()
                for _ in range(backoff * 10):
                    if stop:
                        break
                    time.sleep(0.1)
                backoff = min(30, backoff * 2)
                continue
            time.sleep(1 / FPS)
    finally:  # 不管怎么退出，都按设置把灯放掉
        try:
            lights.release(c["lights_on_exit"])
        except NET_ERRORS:
            pass
        lights.close()
        write_status(a.status, running=False, enabled=c["lights_enabled"])
        say("已退出")


if __name__ == "__main__":
    main()
