#!/usr/bin/env python3
"""龙屏管理器：本机网页（只监听 127.0.0.1），用来挑主题、看效果、生成视频、管推送。

  python3 -I app/server.py            # 打开 http://127.0.0.1:7788

连屏幕这件事默认关着（config 里 device_enabled=false），关着时所有「上屏」操作只演练、不发送。
"""
import json
import mimetypes
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"
CONF_DIR = Path.home() / ".config" / "ryuo-studio"
CONF = CONF_DIR / "config.json"
OUT = Path.home() / ".local" / "share" / "ryuo-studio"
DEFAULT_CONF = {"theme": "starship", "layout": "split", "push_minutes": 15, "device_enabled": False,
                "renderer": "native", "full_show_seconds": 15, "full_hide_seconds": 45}
PORT = 7788

JOBS = {}
LOG = []
RENDER_LOCK = threading.Lock()  # 无头 Chrome 共用一个配置目录，同时开两个会互相打架


def log(msg):
    LOG.append({"t": time.strftime("%H:%M:%S"), "msg": msg})
    del LOG[:-200]


def load_conf():
    try:
        return {**DEFAULT_CONF, **json.loads(CONF.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_CONF)


def save_conf(c):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(c, ensure_ascii=False, indent=2))


def themes():
    res = []
    for f in sorted((ROOT / "themes").glob("*/theme.json")):
        t = json.loads(f.read_text())
        vids = sorted((OUT / "videos" / t["id"]).glob("*-full.mp4"))  # 每段还有一个 -main 主区版，列表只放整屏版
        t["videos"] = [{"name": v.name, "url": f"/out/videos/{t['id']}/{v.name}", "mb": round(v.stat().st_size / 1e6, 1)} for v in vids]
        res.append(t)
    return res


def run_job(kind, cmd):
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"id": jid, "kind": kind, "state": "running", "started": time.strftime("%H:%M"), "tail": ""}
    log(f"开始：{kind}")

    def work():
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT)
        for line in p.stdout:
            JOBS[jid]["tail"] = line.strip()[-300:]
        JOBS[jid]["state"] = "done" if p.wait() == 0 else "failed"
        log(f"{'完成' if JOBS[jid]['state'] == 'done' else '失败'}：{kind}")
    threading.Thread(target=work, daemon=True).start()
    return jid


def render(theme, view, data="fresh"):
    w, h = (634, 1080) if view == "strip" else (2240, 1080)
    out = OUT / "renders" / f"{theme}-{view}-{data}.png"
    with RENDER_LOCK:
        _render(theme, view, data, out, w, h)
    return out


def _render(theme, view, data, out, w, h):
    subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "snapshot.py")], check=True, capture_output=True)
    subprocess.run(["bash", str(ROOT / "tools" / "render.sh"), str(out), f"theme={theme}&view={view}&data={data}", str(w), str(h)], check=True)


def device_status(conf):
    if not conf["device_enabled"]:
        return {"connected": False, "mode": "演练", "note": "还没允许连接屏幕；所有上屏操作只演练、不发送。"}
    try:
        sys.path.insert(0, str(ROOT / "driver"))
        import ryuo  # noqa: F401
    except ImportError:
        return {"connected": False, "mode": "缺驱动", "note": "没找到 driver/ryuo。"}
    return {"connected": False, "mode": "待接入", "note": "驱动已在，真机连接要等 udev 规则装好后再开。"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def file(self, path: Path, base: Path):
        try:
            path = path.resolve()
            path.relative_to(base.resolve())
        except (ValueError, OSError):
            return self.send(404, {"error": "not found"})
        if not path.is_file():
            return self.send(404, {"error": "not found"})
        self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == "/":
            return self.file(WEB / "index.html", WEB)
        if p.startswith("/web/"):
            return self.file(WEB / p[5:], WEB)
        if p.startswith("/repo/"):  # 主题素材、渲染页、字体
            return self.file(ROOT / p[6:], ROOT)
        if p.startswith("/out/"):
            return self.file(OUT / p[5:], OUT)
        conf = load_conf()
        if p == "/api/state":
            return self.send(200, {"conf": conf, "themes": themes(), "device": device_status(conf),
                                   "jobs": list(JOBS.values())[-20:], "log": LOG[-50:]})
        if p == "/api/render":
            try:
                out = render(q.get("theme", conf["theme"]), q.get("view", "strip"), q.get("data", "fresh"))
            except subprocess.CalledProcessError as e:
                return self.send(500, {"error": f"出图失败：{e}"})
            return self.send(200, {"url": f"/out/renders/{out.name}?t={int(time.time())}"})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        b = self.body()
        conf = load_conf()
        if p == "/api/conf":
            for k in ("theme", "layout", "push_minutes", "renderer", "full_show_seconds", "full_hide_seconds"):
                if k in b:
                    conf[k] = b[k]
            save_conf(conf)
            how = "实时" if conf.get("renderer", "native") == "native" else f"每 {conf['push_minutes']} 分钟推一次图"
            log(f"设置已保存：主题 {conf['theme']} · {'分屏' if conf['layout'] == 'split' else '整屏'} · {how}")
            return self.send(200, conf)
        if p == "/api/video":
            theme = b.get("theme", conf["theme"])
            tdir = ROOT / "themes" / theme
            prompt = tdir / "video-prompt.txt"
            if not prompt.exists():
                return self.send(400, {"error": "这个主题还没写视频描述"})
            stem = f"{theme}-{time.strftime('%m%d-%H%M')}"
            raw = OUT / "raw" / f"raw-{stem}.mp4"
            final = OUT / "videos" / theme / f"{stem}.mp4"
            final.parent.mkdir(parents=True, exist_ok=True)
            seed = int(b.get("seed", int(time.time()) % 100000))
            # 先出原片，再放大成整屏和主区两个 HEVC 版本
            script = (f'"{sys.executable}" -I "{ROOT}/tools/gen_video.py" "{tdir}/assets/ref-wide.png" "{raw}" '
                      f'--prompt-file "{prompt}" --seconds {float(b.get("seconds", 10))} --seed {seed} '
                      f'&& bash "{ROOT}/tools/finish_video.sh" "{raw}" "{final}"')
            return self.send(200, {"job": run_job(f"生成「{theme}」视频", ["bash", "-c", script])})
        if p == "/api/apply":
            log(f"{'上屏' if conf['device_enabled'] else '演练上屏'}：主题 {conf['theme']}，{'分屏' if conf['layout'] == 'split' else '整屏'}")
            return self.send(200, {"sent": False, "dry_run": not conf["device_enabled"]})
        return self.send(404, {"error": "not found"})


def main():
    log("管理器已启动")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"龙屏管理器：http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
