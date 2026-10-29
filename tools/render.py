#!/usr/bin/env python3
"""用无头 Chrome（Windows 上没有就用 Edge）把 render/panel.html 截成图。

  python3 -I tools/render.py 输出.png "theme=starship&view=strip" [宽 高]
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostsys  # noqa: E402


def render(out, query, w=2240, h=1080):
    out = Path(out)
    prof = Path.home() / ".cache" / "ryuo-studio" / "chrome"
    prof.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    page = (hostsys.ROOT / "render" / "panel.html").as_uri() + "?" + query
    subprocess.run([hostsys.chrome(), "--headless=new", f"--user-data-dir={prof}", "--force-device-scale-factor=1",
                    "--hide-scrollbars", f"--window-size={w},{h}", "--virtual-time-budget=4000", f"--screenshot={out}", page],
                   check=True, capture_output=True, timeout=120, **hostsys.no_window())
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    render(a[0], a[1], *(int(x) for x in a[2:4]))
