#!/usr/bin/env python3
"""常驻推送：每秒给屏发心跳，同时把额度面板送上屏。

面板有两种送法（config 里的 renderer）：
  native（默认，只用于分屏）：屏上跑一个原生绘制的常驻程序（tools/native_panel.py），
      电脑每分钟送一次数据、每 5 秒保活，倒计时在屏上按秒走；换主题或布局时才重发一次 config。
      屏上程序起不来时，窄条上还有一张同主题的静态图兜底。
  png：主机画好图，按 push_minutes 推上屏（做法甲）。整屏布局总是走这种。

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
DEFAULTS = {"theme": "starship", "layout": "split", "push_minutes": 15, "device_enabled": False, "renderer": "native"}
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


def push_round(scr, c, turn, pushed_videos, last):
    theme, layout = c["theme"], c["layout"]
    view = "strip" if layout == "split" else "usage"
    png = render(theme, view)
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
    say(f"第 {turn + 1} 轮：{theme} · {'分屏' if layout == 'split' else '整屏'} · {name}" + ("" if scr.live else f"（演练，已组 {scr.frames_sent()} 帧）"))


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
    applied, data_at = None, 0.0
    with scr.dev.heartbeat(telemetry):
        while True:
            c = conf()
            use_native = native is not None and c["renderer"] == "native" and c["layout"] == "split"
            key = (c["theme"], c["layout"], use_native)
            wait = max(1, int(c["push_minutes"])) * 60
            try:
                if use_native:
                    wait = NATIVE_TICK_S
                    if key != applied:  # 换了主题或布局：重发一次 config，窄条先放静态图兜底
                        push_round(scr, c, turn, pushed, last)
                        turn += 1
                        applied = key
                        native_panel.build_bundle(c["theme"])
                        native.close()  # 下面重连时顺带把新主题的文件同步上去
                        data_at = 0
                    fresh = native.ensure()
                    if fresh or time.time() - data_at >= NATIVE_DATA_S:
                        native.send({"t": "state", "theme": native_panel.theme_spec(c["theme"]), "data": native_panel.fresh_data()})
                        if fresh or data_at == 0:
                            say(f"原生面板已连上：{c['theme']}")
                        data_at = time.time()
                    else:
                        native.send({"t": "ping"})
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
