#!/usr/bin/env python3
"""电脑这头的原生面板：备好屏上要用的文件、拉起屏上的常驻程序、按时送数据。

  python3 -I tools/native_panel.py --theme starship        # 单独试：送一次数据，然后每 5 秒保活
  python3 -I tools/native_panel.py --theme liquid --once   # 只送一次就退出（20 秒后屏上自己藏起来）

屏上的程序（overlay/src/RyuoPanel.java）用 app_process 以 root 跑，不装应用、不改系统分区，
文件都在 /data/local/tmp/ryuo/ 下。屏重启后程序就没了，下次连上时这里会重新拉起。
"""
import argparse
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path.home() / ".local" / "share" / "ryuo-studio"
BUNDLE = OUT / "native" / "bundle"
DEVICE_DIR = "/data/local/tmp/ryuo"
SOCKET = "ryuo-panel"
LOCAL_PORT = 7791
DEX = ROOT / "overlay" / "build" / "panel.dex"
SYSTEM_FONTS = Path("/usr/share/fonts/opentype/noto")
# Claude 保留品牌橙，其余图标按主题里的 logo 颜色上色
LOGOS = {"claude": ("claude-color.svg", False), "codex": ("codex.svg", True), "grok": ("grok.svg", True), "cursor": ("cursor-mono.svg", True)}
CSS_KEYS = {"ink": "ink", "dim": "dim", "faint": "faint", "line": "line", "accent": "accent", "accent2": "accent2",
            "hot": "hot", "mark": "mark", "warn": "warn", "logoInk": "logo-ink"}


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def adb(*args, check=True, capture=False, timeout=120):
    r = subprocess.run(["adb", *args], check=check, timeout=timeout, capture_output=capture, text=capture)
    return r.stdout if capture else None


# ---------------- 主题 ----------------

def css_color(v):
    """#RGB / #RRGGBB / #RRGGBBAA / rgba(r,g,b,a) → 安卓认的 #AARRGGBB。"""
    v = v.strip()
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 8:
            h = h[6:] + h[:6]
        return "#" + (h if len(h) == 8 else "FF" + h).upper()
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if not m:
        raise ValueError(f"认不出的颜色：{v}")
    parts = [x.strip() for x in m.group(1).split(",")]
    r, g, b = (int(x) for x in parts[:3])
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return f"#{round(a * 255):02X}{r:02X}{g:02X}{b:02X}"


def theme_spec(theme):
    tdir = ROOT / "themes" / theme
    meta = json.loads((tdir / "theme.json").read_text())
    css = (tdir / "theme.css").read_text()
    vars_ = dict(re.findall(r"--([\w-]+):([^;}]+)", css))
    spec = {
        "id": theme,
        "layout": meta["layout"],
        "colors": {k: css_color(vars_[v]) for k, v in CSS_KEYS.items()},
        "fonts": meta["native_fonts"],
        "strip": f"themes/{theme}/strip.jpg",
        "logos": {k: {"file": f"logos/{k}.png", "mono": mono} for k, (_f, mono) in LOGOS.items()},
        "scrim": meta.get("scrim", 0.74),
    }
    for opt in ("labels", "series"):  # 标题文字、同心光环每圈的颜色，主题没写就用布局默认
        if opt in meta:
            spec[opt] = meta[opt]
    return spec


# ---------------- 屏上文件 ----------------

def render_logo(svg, out):
    """用无头 Chrome 把图标画成 256×256 的透明底 PNG；单色图标画成白色，屏上再按主题上色。"""
    body = svg.read_text().replace('height="1em"', 'height="256"').replace('width="1em"', 'width="256"')
    page = out.with_suffix(".html")
    page.write_text(f'<!doctype html><body style="margin:0;background:transparent;color:#fff">{body}</body>')
    prof = OUT / "native" / "chrome"
    subprocess.run(["google-chrome", "--headless=new", f"--user-data-dir={prof}", "--force-device-scale-factor=1",
                    "--hide-scrollbars", "--default-background-color=00000000", "--window-size=256,256",
                    f"--screenshot={out}", f"file://{page}"], check=True, capture_output=True, timeout=60)
    page.unlink()


def build_bundle(theme):
    """把这个主题要用的东西放进本地暂存目录，跟屏上的目录结构一一对应。"""
    spec = theme_spec(theme)
    (BUNDLE / "fonts").mkdir(parents=True, exist_ok=True)
    (BUNDLE / "logos").mkdir(exist_ok=True)
    (BUNDLE / "themes" / theme).mkdir(parents=True, exist_ok=True)
    if not DEX.exists():
        subprocess.run(["bash", str(ROOT / "overlay" / "build.sh")], check=True)
    shutil.copy2(DEX, BUNDLE / "panel.dex")
    for role in spec["fonts"].values():
        if not role:
            continue
        name = Path(role["file"]).name
        dst = BUNDLE / "fonts" / name
        if dst.exists():
            continue
        src = ROOT / "themes" / "_shared" / "fonts" / "ttf" / name
        if not src.exists():
            src = SYSTEM_FONTS / name  # 中文衬线体屏上没有，从电脑上拿（SIL OFL）
        shutil.copy2(src, dst)
    shutil.copy2(ROOT / "themes" / theme / "assets" / "strip.jpg", BUNDLE / "themes" / theme / "strip.jpg")
    for key, (fname, _mono) in LOGOS.items():
        svg = ROOT / "themes" / "_shared" / "logos" / fname
        png = BUNDLE / "logos" / f"{key}.png"
        if svg.exists() and (not png.exists() or png.stat().st_mtime < svg.stat().st_mtime):
            render_logo(svg, png)
    return spec


def digest(path):
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file() and p.name != "bundle.sha"):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


# ---------------- 屏上程序 ----------------

class NativePanel:
    def __init__(self):
        self.sock = None
        self.buf = b""
        self.pushed = None

    def sync(self):
        """有变化才推；屏上留一份指纹，换个进程来推也认得出没变。返回程序本身是否换了。"""
        d = digest(BUNDLE)
        if self.pushed is None:
            self.pushed = (adb("shell", f"cat {DEVICE_DIR}/bundle.sha 2>/dev/null", check=False, capture=True) or "").strip()
        if d == self.pushed:
            return False
        dex_before = adb("shell", f"sha256sum {DEVICE_DIR}/panel.dex 2>/dev/null", check=False, capture=True) or ""
        adb("shell", f"mkdir -p {DEVICE_DIR}")
        adb("push", "--sync", f"{BUNDLE}/.", DEVICE_DIR + "/", capture=True, timeout=300)
        adb("shell", f"echo {d} > {DEVICE_DIR}/bundle.sha")
        self.pushed = d
        dex_after = adb("shell", f"sha256sum {DEVICE_DIR}/panel.dex", check=False, capture=True) or ""
        return dex_before.split()[:1] != dex_after.split()[:1]

    def running(self):
        out = adb("shell", "pidof app_process; ps -A -o PID,ARGS | grep '[R]yuoPanel'", check=False, capture=True)
        return "RyuoPanel" in (out or "")

    def start(self):
        adb("shell", f"cd {DEVICE_DIR}; CLASSPATH={DEVICE_DIR}/panel.dex setsid nohup app_process {DEVICE_DIR} RyuoPanel"
                     f" > {DEVICE_DIR}/panel.log 2>&1 < /dev/null &")
        say("已在屏上拉起原生面板")

    def stop(self):
        self.close()
        pids = (adb("shell", "ps -A -o PID,ARGS | grep '[R]yuoPanel' | awk '{print $1}'", check=False, capture=True) or "").split()
        for pid in pids:
            adb("shell", f"kill {pid}", check=False)
        return pids

    def connect(self):
        adb("forward", f"tcp:{LOCAL_PORT}", f"localabstract:{SOCKET}", capture=True)
        for _ in range(30):
            try:
                s = socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=3)
                s.settimeout(5)
                self.sock = s
                self.send({"t": "ping"})
                return
            except OSError:
                self.close()
                time.sleep(0.3)
        raise OSError("连不上屏上的原生面板")

    def ensure(self):
        """文件同步到屏上、程序在跑、连接可用；程序文件变了就重启一次。
        已连着就什么都不做；返回 True 表示这次是新连上的（屏上程序可能是刚起来的，要重送主题）。"""
        if self.sock is not None:
            return False
        changed = self.sync()
        if changed and self.running():
            self.stop()
            time.sleep(0.5)
        if not self.running():
            self.start()
            time.sleep(1.5)
        self.connect()
        return True

    def send(self, msg):
        if self.sock is None:
            raise OSError("还没连上")
        try:
            self.sock.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode())
            while b"\n" not in self.buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise OSError("屏上的程序断开了")
                self.buf += chunk
        except OSError:
            self.close()
            raise
        line, self.buf = self.buf.split(b"\n", 1)
        reply = line.decode()
        if reply != "ok":
            raise ValueError(f"屏上回复：{reply}")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.buf = b""


def host_tz():
    try:
        return str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


def fresh_data(stale_minutes=None):
    out = OUT / "native" / "data.json"
    cmd = [sys.executable, "-I", str(ROOT / "tools" / "snapshot.py"), "--json", str(out)]
    if stale_minutes is not None:
        cmd += ["--stale-minutes", str(stale_minutes)]
    subprocess.run(cmd, check=True, capture_output=True)
    data = json.loads(out.read_text())
    data["tz"] = host_tz()
    if stale_minutes is not None:  # 演示过期样子：把生成时间往前挪
        data["generatedAtMs"] -= stale_minutes * 60000
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="starship")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stale-minutes", type=int)
    ap.add_argument("--stop", action="store_true", help="停掉屏上的程序")
    ap.add_argument("--full", action="store_true", help="整屏样子（试的时候立刻显示，停 --show 秒后循环）")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--hide", type=int, default=45)
    a = ap.parse_args()
    panel = NativePanel()
    if a.stop:
        say(f"已停掉：{panel.stop() or '本来就没在跑'}")
        return
    spec = build_bundle(a.theme)
    panel.ensure()
    panel.send({"t": "state", "theme": spec, "data": fresh_data(a.stale_minutes),
                "mode": {"full": a.full, "show": a.show, "hide": a.hide}})
    say(f"已送数据：{a.theme}")
    if a.once:
        return
    while True:
        time.sleep(5)
        panel.send({"t": "ping"})


if __name__ == "__main__":
    main()
