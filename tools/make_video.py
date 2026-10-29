#!/usr/bin/env python3
"""管理器里「生成视频」走这里：先让 ComfyUI 出原片，再放大成屏幕用的两个版本。

  python3 -I tools/make_video.py 参考图.png 原片.mp4 成片.mp4 --prompt-file x.txt --seconds 10 --seed 1
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import finish_video  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("ref")
ap.add_argument("raw")
ap.add_argument("final")
ap.add_argument("--prompt-file", required=True)
ap.add_argument("--seconds", default="10")
ap.add_argument("--seed", default="1")
a = ap.parse_args()
subprocess.run([sys.executable, "-I", str(HERE / "gen_video.py"), a.ref, a.raw, "--prompt-file", a.prompt_file,
                "--seconds", a.seconds, "--seed", a.seed], check=True)
finish_video.finish(a.raw, a.final)
