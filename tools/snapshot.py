#!/usr/bin/env python3
"""把本机额度快照整理成屏幕要显示的四家用量，写成 data.js 给 render/panel.html 用。

用法：
  python3 -I make_data.py                      # 读真实快照，按现在时间算
  python3 -I make_data.py --stale-minutes 47   # 假装快照已经 47 分钟没更新（画「过期」样子用）
"""
import argparse
import calendar
import json
import time
from pathlib import Path

SNAPSHOT = Path.home() / ".local/state/aios/quota-snapshot.json"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "render" / "data.js"
LOGO_DIR = ROOT / "themes" / "_shared" / "logos"
# 每家用哪个图标；Claude 保留品牌橙，其余跟随主题文字颜色
LOGO = {"claude": "claude-color.svg", "codex": "codex.svg", "grok": "grok.svg", "cursor": "cursor-mono.svg"}
STALE_AFTER_MIN = 30
WEEK_MS = 7 * 24 * 3600 * 1000

# 每家取哪个窗口当主数字、哪个当小字
PICK = [
    ("claude", "Claude", "anthropic", "anthropic:7d", "7 天", ("anthropic:5h", "5 小时")),
    ("codex", "Codex", "openai-codex", "openai-codex:primary", "7 天", None),
    ("grok", "Grok", "xai-oauth", "xai-oauth:credits:1w", "7 天", ("xai-oauth:product:grokbuild:1w", "Build")),
    ("cursor", "Cursor", "cursor", "cursor:usd:individual-auto", "本月", ("cursor:usd:individual-api", "其他模型")),
]
WEEKDAY = "一二三四五六日"


def window_start_ms(win, reset_ms):
    if win.get("duration_ms"):
        return reset_ms - win["duration_ms"]
    if win["id"].startswith("cursor:"):
        # 按自然月计费：上一个重置点是上个月同一天同一时刻
        t = time.localtime(reset_ms / 1000)
        y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
        d = min(t.tm_mday, calendar.monthrange(y, m)[1])
        return int(time.mktime((y, m, d, t.tm_hour, t.tm_min, t.tm_sec, 0, 0, -1)) * 1000)
    return reset_ms - WEEK_MS  # Codex 等没写时长的周窗口


def fmt_reset(reset_ms, now_ms):
    t = time.localtime(reset_ms / 1000)
    n = time.localtime(now_ms / 1000)
    hm = time.strftime("%H:%M", t)
    if (t.tm_year, t.tm_yday) == (n.tm_year, n.tm_yday):
        day = "今天"
    elif reset_ms - now_ms < 2 * 86400000 and (t.tm_yday - n.tm_yday) % 365 == 1:
        day = "明天"
    else:
        day = f"{t.tm_mon}/{t.tm_mday} 周{WEEKDAY[t.tm_wday]}"
    return f"{day} {hm}"


def fmt_left(ms):
    h = ms / 3600000
    if h < 1:
        return f"{max(1, round(h * 60))} 分钟"
    if h < 48:
        return f"{round(h)} 小时"
    return f"{int(h // 24)} 天"


def pace_word(used, elapsed):
    if elapsed < 10:
        return "刚重置", "calm"
    r = used / elapsed if elapsed else 0
    if r > 1.25:
        return "偏快", "hot"
    if r < 0.7:
        return "有富余", "calm"
    return "正好", "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-minutes", type=int, default=None)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    snap = json.loads(SNAPSHOT.read_text())
    gen = snap["generated_at_ms"]
    now = gen + a.stale_minutes * 60000 if a.stale_minutes is not None else int(time.time() * 1000)
    wins = {w["id"]: w for p in snap["providers"] for w in p["windows"]}

    rows = []
    for key, name, _prov, wid, sub, second in PICK:
        w = wins.get(wid)
        if not w or w.get("used_pct") is None:
            rows.append({"key": key, "name": name, "sub": sub, "missing": True})
            continue
        reset = w["resets_at_ms"]
        start = window_start_ms(w, reset)
        elapsed = max(0.0, min(100.0, (now - start) / (reset - start) * 100))
        used = float(w["used_pct"])
        word, tone = pace_word(used, elapsed)
        if used >= 85:
            tone = "hot"
        row = {
            "key": key, "name": name, "sub": sub,
            "used": round(used), "usedExact": used,
            "elapsed": round(elapsed, 1),
            "left": round(100 - used),
            "resetAt": fmt_reset(reset, now), "resetIn": fmt_left(reset - now), "resetMs": reset,
            "pace": word, "tone": tone,
        }
        if second and second[0] in wins:
            row["second"] = {"label": second[1], "used": round(float(wins[second[0]]["used_pct"]))}
        rows.append(row)

    age_min = (now - gen) / 60000
    nxt = min((r for r in rows if not r.get("missing")), key=lambda r: r["resetMs"])
    data = {
        "generatedAt": time.strftime("%H:%M", time.localtime(gen / 1000)),
        "generatedDate": time.strftime("%m-%d", time.localtime(gen / 1000)),
        "now": time.strftime("%H:%M", time.localtime(now / 1000)),
        "ageMin": round(age_min),
        "stale": age_min > STALE_AFTER_MIN,
        "next": {"name": nxt["name"], "resetIn": nxt["resetIn"], "left": nxt["left"]},
        "rows": rows,
    }
    logos = {}
    for key, fname in LOGO.items():
        f = LOGO_DIR / fname
        if f.exists():  # 开源版不带图标，缺了就只显示文字
            logos[key] = f.read_text().replace('height="1em"', "").replace('width="1em"', "")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("window.SNAP = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
                           + "window.LOGOS = " + json.dumps(logos, ensure_ascii=False) + ";\n")
    print(json.dumps(data, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
