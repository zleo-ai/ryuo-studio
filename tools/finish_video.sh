#!/usr/bin/env bash
# 把模型出的视频放大成屏幕用的两个版本（HEVC、无声）：
#   *-full.mp4  2240×1080 整屏
#   *-main.mp4  1606×1080 分屏主区（从右边裁，保留靠近弯折处的主体）
set -euo pipefail
src="$1"; base="${2%.mp4}"
ffmpeg -v error -y -i "$src" -vf "scale=2240:-2:flags=lanczos,crop=2240:1080" -an -c:v libx265 -preset slow -crf 20 -pix_fmt yuv420p -tag:v hvc1 "$base-full.mp4"
ffmpeg -v error -y -i "$src" -vf "scale=-2:1080:flags=lanczos,crop=1606:1080:iw-1606:0" -an -c:v libx265 -preset slow -crf 20 -pix_fmt yuv420p -tag:v hvc1 "$base-main.mp4"
