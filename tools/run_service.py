#!/usr/bin/env python3
"""Windows 上的后台服务外壳（Linux 用 systemd，不走这里）：计划任务登录时用 pythonw 跑它。

  pythonw -I tools/run_service.py pusher     # 或 manager

把输出写进日志、pid 写进 ~/.local/share/ryuo-studio/<名字>.pid；程序退出或出错就 10 秒后重来，
跟 systemd 的 Restart=always 一样。用 runpy 在本进程里跑，停任务时不会留下孤儿进程。
"""
import os
import runpy
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = {"pusher": ROOT / "tools" / "pusher.py", "manager": ROOT / "app" / "server.py"}
OUT = Path.home() / ".local" / "share" / "ryuo-studio"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in SCRIPTS:
        sys.exit("用法：run_service.py pusher|manager")
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / ("app.log" if name == "manager" else "pusher.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log  # pythonw 没有控制台，不重定向的话 print 会丢
    (OUT / f"{name}.pid").write_text(str(os.getpid()))
    script = SCRIPTS[name]
    while True:
        sys.argv = [str(script)]
        sys.path[:] = [p for p in sys.path if p]  # 每轮从干净的 sys.path 开始，脚本自己会补
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit:
            pass
        except Exception:  # noqa: BLE001 - 外壳要兜住一切，记下来再重启
            traceback.print_exc()
        print(time.strftime("%H:%M:%S"), f"{name} 退出了，10 秒后重启", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
