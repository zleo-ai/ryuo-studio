#!/usr/bin/env python3
"""做法甲的常驻推送：主机画好用量图，按时推上屏，同时每秒给屏发心跳。

  python3 -I tools/pusher.py            # 读 ~/.config/ryuo-studio/config.json
  python3 -I tools/pusher.py --once     # 只推一轮就退出（调试用）

config 里 device_enabled=false 时只演练：照样出图、照样组帧，但不调 adb、不开 HID，
只把要做的事打印出来。

文件名不用 ryuo 开头：屏会先去官方预设目录里按「包含」匹配找同名文件。
为什么要轮流用两个文件名：屏上的图片加载器很可能按路径缓存，同名覆盖不一定会刷新
（还没在真机验证），所以 a/b 两个名字交替，每次推完重发一次 config，旧的那张随后删掉。
代价是每轮画面会重新加载一次，大约 1.6 秒。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "driver"))
from ryuo.device import RyuoDevice  # noqa: E402
from ryuo.transport import FakeTransport  # noqa: E402

CONF = Path.home() / ".config" / "ryuo-studio" / "config.json"
OUT = Path.home() / ".local" / "share" / "ryuo-studio"
SCREEN_DIR = "/sdcard/pcMedia/"
DEFAULTS = {"theme": "starship", "layout": "split", "push_minutes": 15, "device_enabled": False}


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def conf():
    try:
        return {**DEFAULTS, **json.loads(CONF.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


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
            from ryuo.transport import HidTransport
            self.dev = RyuoDevice(HidTransport())
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


def push_round(scr, c, turn, pushed_videos):
    theme, layout = c["theme"], c["layout"]
    view = "strip" if layout == "split" else "usage"
    png = render(theme, view)
    name = f"lp-panel-{'ab'[turn % 2]}.png"
    old = f"lp-panel-{'ab'[(turn + 1) % 2]}.png"
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
    else:  # 整屏：视频和用量大图轮流放
        dev.set_media(([video.name] if video else []) + [name], play_mode="Cycle" if video else "Single")
    dev.apply()
    if turn:
        dev.delete_media([old])
    say(f"第 {turn + 1} 轮：{theme} · {'分屏' if layout == 'split' else '整屏'} · {name}" + ("" if scr.live else f"（演练，已组 {scr.frames_sent()} 帧）"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    c = conf()
    scr = Screen(live=bool(c["device_enabled"]))
    say("开始推送" + ("" if scr.live else "（演练模式：不连屏幕）"))
    pushed, turn = set(), 0
    with scr.dev.heartbeat(telemetry):
        while True:
            c = conf()
            try:
                push_round(scr, c, turn, pushed)
            except (subprocess.SubprocessError, OSError, ValueError) as e:
                say(f"这一轮没推成：{e}；下一轮再试")
            turn += 1
            if a.once:
                break
            time.sleep(max(1, int(c["push_minutes"])) * 60)
    scr.dev.close()


if __name__ == "__main__":
    main()
