#!/usr/bin/env python3
"""把模型出的视频放大成屏幕用的两个版本（HEVC、无声）：
  *-full.mp4  2240×1080 整屏
  *-main.mp4  1606×1080 分屏主区（从右边裁，保留靠近弯折处的主体）

  python3 -I tools/finish_video.py 原片.mp4 输出.mp4
"""
import shutil
import subprocess
import sys
from pathlib import Path

HEVC = ["-an", "-c:v", "libx265", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]


def finish(src, out):
    base = str(out)[:-4] if str(out).endswith(".mp4") else str(out)
    ff = shutil.which("ffmpeg") or "ffmpeg"
    for vf, name in (("scale=2240:-2:flags=lanczos,crop=2240:1080", "full"),
                     ("scale=-2:1080:flags=lanczos,crop=1606:1080:iw-1606:0", "main")):
        subprocess.run([ff, "-v", "error", "-y", "-i", str(src), "-vf", vf, *HEVC, f"{base}-{name}.mp4"], check=True)
    return Path(f"{base}-full.mp4"), Path(f"{base}-main.mp4")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    finish(sys.argv[1], sys.argv[2])
