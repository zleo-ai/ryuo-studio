#!/usr/bin/env python3
"""真机小测试：把一个文件放上屏、按指定布局播放一会儿，期间用屏自带的 screencap 截图，最后停发心跳看多久回待机。

  python3 -I tools/hw_test.py full lp-test-hevc.mp4 --seconds 20
  python3 -I tools/hw_test.py split lp-test-hevc.mp4 lp-panel-a.png --seconds 20

文件从 ~/.local/share/ryuo-studio/test/ 取；截图存到同目录的 shots/。不删屏上原有的文件。
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "driver"))
from ryuo.device import RyuoDevice  # noqa: E402
from ryuo.transport import open_default  # noqa: E402

TEST = Path.home() / ".local" / "share" / "ryuo-studio" / "test"
SHOTS = TEST / "shots"


def host_tz():
    """电脑当前时区名，比如 Asia/Shanghai；屏的时钟按这个显示。"""
    try:
        return str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def adb(*a, out=None):
    return subprocess.run(["adb", *a], check=True, timeout=120, stdout=out, stderr=subprocess.PIPE)


def shot(tag):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{time.strftime('%H%M%S')}-{tag}.png"
    with p.open("wb") as f:
        adb("exec-out", "screencap", "-p", out=f)
    print("截图", p, flush=True)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("layout", choices=["full", "split"])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--colors", nargs=2, default=["#2DE2C8", "#E6EEF6"], metavar=("TITLE", "CONTENT"))
    ap.add_argument("--refresh", help="播放中途把这张图用原名覆盖窄条那张，看屏会不会换")
    ap.add_argument("--swap", help="播放中途把这张图换个名字放上去，再重发一次 config")
    a = ap.parse_args()

    if not a.no_push:
        for f in a.files:
            adb("push", str(TEST / f), "/sdcard/pcMedia/" + f)
            print("已放上屏", f, flush=True)

    dev = RyuoDevice(open_default())
    print("固件", dev.detect_firmware().version, flush=True)
    dev.set_layout(a.layout)
    if a.layout == "split":
        dev.set_media([a.files[0]], zone=0)
        dev.set_media([a.files[1]], zone=1)
    else:
        dev.set_media([a.files[0]])
    # 分屏时第三个读数会画到右边窄条上，所以只留两个
    dev.set_slots(["Date&Time", "CPU Temperature"] if a.layout == "split" else ["Date&Time", "CPU Temperature", "GPU Temperature"])
    dev.set_colors(*a.colors)
    dev.screen.time_zone = host_tz()
    dev.apply()
    t0 = time.time()
    with dev.heartbeat(lambda: {"cpu": {"temperature": 45}, "gpu": {"temperature": 44}}):
        time.sleep(max(0, t0 + 4 - time.time()))
        shot(f"{a.layout}-4s")
        if a.refresh:
            adb("push", str(TEST / a.refresh), "/sdcard/pcMedia/" + a.files[-1])
            dev.set_colors(*a.colors)  # 发一次 preset，看会不会顺带重新读图
            print("已原名覆盖", a.files[-1], flush=True)
            time.sleep(9)
            shot("after-same-name")
        if a.swap:
            adb("push", str(TEST / a.swap), "/sdcard/pcMedia/" + a.swap)
            dev.set_media([a.swap], zone=1)
            t1 = time.time()
            dev.apply()
            print("换名并重发 config", a.swap, flush=True)
            for k in (0.5, 1.2, 2.5, 6):
                time.sleep(max(0, t1 + k - time.time()))
                shot(f"swap-{k}s")
        time.sleep(max(0, t0 + a.seconds - 3 - time.time()))
        shot(f"{a.layout}-{a.seconds - 3}s")
        time.sleep(max(0, t0 + a.seconds - time.time()))
    print("停发心跳", flush=True)
    dev.close()


if __name__ == "__main__":
    main()
