#!/usr/bin/env python3
"""点「龙屏」图标走这里：确保推送和管理器在跑，等管理器能连上，再开一个独立的应用窗口。
Linux 的桌面入口是 desktop/ryuo-studio.sh；Windows 的开始菜单快捷方式用 pythonw 跑这个。
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostsys  # noqa: E402

URL = "http://127.0.0.1:7788/"


def up():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL + "api/state", timeout=2):
            return True
    except OSError:
        return False


def main():
    for name in hostsys.SERVICES:
        if not hostsys.service_active(name):
            hostsys.service("start", name)
    for _ in range(40):
        if up():
            break
        time.sleep(0.25)
    prof = hostsys.OUT / "app-window"
    subprocess.Popen([hostsys.chrome(), f"--app={URL}", "--class=ryuo-studio", f"--user-data-dir={prof}",
                      "--no-first-run", "--no-default-browser-check", "--window-size=1280,900"], **hostsys.no_window())


if __name__ == "__main__":
    main()
