#!/usr/bin/env python3
"""常驻推送：每秒给屏发心跳，同时把额度面板送上屏。

面板有两种送法（config 里的 renderer）：
  native（默认）：屏上跑一个原生绘制的常驻程序（tools/native_panel.py），
      电脑每分钟送一次数据、每 5 秒保活，倒计时在屏上按秒走；换主题或布局时才重发一次 config。
      分屏：面板盖在右边窄条上，底下垫一张同主题的静态图兜底。
      整屏：屏只循环放视频，面板每隔 full_hide_seconds 秒淡入盖在压暗的视频上 full_show_seconds 秒。
  png：主机画好图，按 push_minutes 推上屏（做法甲）。

  python3 -I tools/pusher.py            # 读 ~/.config/ryuo-studio/config.json
  python3 -I tools/pusher.py --once     # 只推一轮就退出（调试用）

config 里 device_enabled=false 时只演练：照样出图、照样组帧，但不调 adb、不开 HID，
只把要做的事打印出来。

文件名不用 ryuo 开头：屏会先去官方预设目录里按「包含」匹配找同名文件。
为什么每轮换一个新文件名：1.0.7 实测，屏上的图片加载器按路径缓存，同名覆盖不刷新，
连「先删再放同名文件」都会显示最早那次的旧图。所以每轮用带时间的新名字，推完重发一次
config，再删掉上一张。重发 config 时左边视频会从头播，图片半秒内换上，没有黑屏。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "driver"))
sys.path.insert(0, str(ROOT / "tools"))
from ryuo.device import RyuoDevice  # noqa: E402
from ryuo.transport import FakeTransport  # noqa: E402

CONF = Path.home() / ".config" / "ryuo-studio" / "config.json"
OUT = Path.home() / ".local" / "share" / "ryuo-studio"
SCREEN_DIR = "/sdcard/pcMedia/"
DEFAULTS = {"theme": "starship", "layout": "split", "push_minutes": 15, "device_enabled": False, "renderer": "native",
            "full_show_seconds": 15, "full_hide_seconds": 45, "panel_width": 634, "panel_glass": True,
            "hud": True, "hud_items": ["cpu", "gpu", "mem"]}
NATIVE_TICK_S, NATIVE_DATA_S = 5, 60


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def conf():
    try:
        return {**DEFAULTS, **json.loads(CONF.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def host_tz():
    """电脑当前时区名（如 Asia/Shanghai）；不传的话屏按世界标准时间显示。"""
    try:
        return str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def cpu_temp():
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (hw / "name").read_text().strip() in ("k10temp", "coretemp", "zenpower"):
                return round(int((hw / "temp1_input").read_text()) / 1000)
        except OSError:
            continue
    return None


def gpu_temp():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


_cpu_prev = None


def cpu_load():
    """两次读 /proc/stat 之间 CPU 忙的比例；第一次没有上一次，返回 None。"""
    global _cpu_prev
    try:
        v = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
    except (OSError, ValueError):
        return None
    idle, total = v[3] + v[4], sum(v)
    prev, _cpu_prev = _cpu_prev, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])))


def mem():
    try:
        kv = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()}
    except (OSError, ValueError, IndexError):
        return None
    total, avail = kv["MemTotal"], kv["MemAvailable"]
    return {"pct": round(100 * (total - avail) / total), "usedGb": (total - avail) / 1048576, "totalGb": total / 1048576}


def gpu():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        temp, load = (int(x) for x in out.splitlines()[0].split(","))
        return {"temp": temp, "load": load}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def sys_stats():
    """屏上左边那排占用：读不到的项不写，屏上显示破折号。"""
    out = {"t": "sys"}
    c = {}
    if (t := cpu_temp()) is not None:
        c["temp"] = t
    if (ld := cpu_load()) is not None:
        c["load"] = ld
    out["cpu"] = c
    if (g := gpu()) is not None:
        out["gpu"] = g
    if (m := mem()) is not None:
        out["mem"] = m
    return out


def telemetry():
    t = {}
    if (c := cpu_temp()) is not None:
        t["cpu"] = {"temperature": c}
    if (g := gpu_temp()) is not None:
        t["gpu"] = {"temperature": g}
    return t


def render(theme, view):
    w, h = (634, 1080) if view == "strip" else (2240, 1080)
    out = OUT / "renders" / f"push-{view}.png"
    subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "snapshot.py")], check=True, capture_output=True)
    subprocess.run(["bash", str(ROOT / "tools" / "render.sh"), str(out), f"theme={theme}&view={view}", str(w), str(h)], check=True)
    return out


def latest_video(theme, kind):
    vids = sorted((OUT / "videos" / theme).glob(f"*-{kind}.mp4"), key=lambda p: p.stat().st_mtime)
    return vids[-1] if vids else None


class Screen:
    """真机和演练两种模式共用一套流程；演练时用假传输层，并且不调 adb。"""

    def __init__(self, live):
        self.live = live
        if live:  # 只有 Maple 允许连接屏幕、装好 udev 规则以后才会走到这里
            from ryuo.transport import open_default
            self.dev = RyuoDevice(open_default())
        else:
            self.fake = FakeTransport()
            self.dev = RyuoDevice(self.fake)

    def adb(self, *args):
        cmd = ["adb", *args]
        if not self.live:
            say("演练 · 会执行：" + " ".join(cmd))
            return
        subprocess.run(cmd, check=True, timeout=120)

    def frames_sent(self):
        return 0 if self.live else len(self.fake.frames)


def push_round(scr, c, turn, pushed_videos, last, native=False):
    """native=True 且整屏时只放视频（面板由屏上程序叠上去），不用出图。
    原生分屏开了玻璃：屏幕按整屏放视频，窄条由屏上程序半透明叠在右边，宽度随意。"""
    theme, layout = c["theme"], c["layout"]
    glass = native and layout == "split" and c.get("panel_glass")
    if glass:
        layout = "full"
    video_only = native and layout != "split"
    name = None
    if not video_only:
        png = render(theme, "strip" if layout == "split" else "usage")
        name = f"lp-panel-{time.strftime('%m%d%H%M%S')}.png"
        scr.adb("push", str(png), SCREEN_DIR + name)

    video = latest_video(theme, "main" if layout == "split" else "full")
    if video and video.name not in pushed_videos:
        scr.adb("push", str(video), SCREEN_DIR + video.name)
        pushed_videos.add(video.name)

    dev = scr.dev
    dev.set_layout(layout)
    if layout == "split":
        dev.set_media([video.name] if video else [name], zone=0)
        dev.set_media([name], zone=1)
        # 1.0.7 实测：分屏时第三个读数会画到右边窄条上，所以只留两个
        dev.set_slots(["Date&Time", "CPU Temperature"])
    elif video_only:
        dev.set_media([video.name] if video else [], play_mode="Single")
        # 屏幕自带的时间、温度：6 个固定位置，数组下标就是位置，空串是空位。
        # 整屏默认全空（会和浮出的面板叠在一起）；玻璃分屏默认左边两项（第三项会被面板盖住）
        # 开了左边时钟（hud）就由屏上程序自己画，屏幕自带的读数关掉免得叠在一起
        if glass:
            dev.set_slots([] if c.get("hud") else c.get("split_slots", ["Date&Time", "CPU Temperature"]))
        else:
            dev.set_slots(c.get("full_slots", []))
    else:  # 整屏：视频和用量大图轮流放
        dev.set_media(([video.name] if video else []) + [name], play_mode="Cycle" if video else "Single")
        dev.set_slots(["Date&Time", "CPU Temperature", "GPU Temperature"])
    colors = json.loads((ROOT / "themes" / theme / "theme.json").read_text())["colors"]
    dev.set_colors(colors["title"], colors["content"])
    dev.screen.time_zone = host_tz()
    dev.apply()
    if last.get("name"):
        dev.delete_media([last["name"]])
    last["name"] = name
    say(f"第 {turn + 1} 轮：{theme} · {'分屏' if layout == 'split' else '整屏'} · {name or '只放视频'}" + ("" if scr.live else f"（演练，已组 {scr.frames_sent()} 帧）"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    c = conf()
    scr = Screen(live=bool(c["device_enabled"]))
    native = None
    if scr.live:
        import native_panel
        native = native_panel.NativePanel()
    say("开始推送" + ("" if scr.live else "（演练模式：不连屏幕）"))
    pushed, turn, last = set(), 0, {}
    applied, data_at, sent_mode = None, 0.0, None
    with scr.dev.heartbeat(telemetry):
        while True:
            c = conf()
            use_native = native is not None and c["renderer"] == "native"
            key = (c["theme"], c["layout"], use_native, bool(c["panel_glass"]), bool(c["hud"]),
                   json.dumps([c.get("split_slots"), c.get("full_slots")]))
            wait = max(1, int(c["push_minutes"])) * 60
            try:
                if use_native:
                    wait = NATIVE_TICK_S
                    if key != applied:  # 换了主题或布局：重发一次 config，窄条先放静态图兜底
                        push_round(scr, c, turn, pushed, last, native=True)
                        turn += 1
                        applied = key
                        native_panel.build_bundle(c["theme"])
                        native.close()  # 下面重连时顺带把新主题的文件同步上去
                        data_at = 0
                    fresh = native.ensure()
                    mode = {"full": c["layout"] != "split", "show": c["full_show_seconds"], "hide": c["full_hide_seconds"],
                            "width": int(c["panel_width"]) if c["panel_glass"] else 634, "glass": bool(c["panel_glass"]),
                            "hud": bool(c["hud"]), "hudItems": list(c["hud_items"])}
                    if mode != sent_mode:  # 只改了浮出节奏：马上送，不用重发 config
                        data_at = 0
                    if fresh or time.time() - data_at >= NATIVE_DATA_S:
                        sent_mode = mode
                        native.send({"t": "state", "theme": native_panel.theme_spec(c["theme"]), "data": native_panel.fresh_data(), "mode": mode})
                        if fresh or data_at == 0:
                            say(f"原生面板已连上：{c['theme']}")
                        data_at = time.time()
                    else:
                        native.send(sys_stats() if c["hud"] else {"t": "ping"})
                else:
                    if native is not None and native.sock is not None:
                        native.send({"t": "hide"})
                        native.close()
                    push_round(scr, c, turn, pushed, last)
                    turn += 1
                    applied = key
            except (subprocess.SubprocessError, OSError, ValueError) as e:
                say(f"这一轮没推成：{e}；稍后再试")
                if native is not None:
                    native.close()
                wait = min(wait, 30)
            if a.once:
                break
            time.sleep(wait)
    scr.dev.close()


if __name__ == "__main__":
    main()
