#!/usr/bin/env bash
# 用无头 Chrome 把 render/panel.html 截成图：bash render.sh 输出.png "theme=starship&view=strip" [宽 高]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$1"; q="$2"; w="${3:-2240}"; h="${4:-1080}"
prof="${XDG_CACHE_HOME:-$HOME/.cache}/ryuo-studio/chrome"
mkdir -p "$prof" "$(dirname "$out")"
google-chrome --headless=new --user-data-dir="$prof" --force-device-scale-factor=1 --hide-scrollbars \
  --window-size="$w,$h" --virtual-time-budget=4000 --screenshot="$out" "file://$here/render/panel.html?$q" >/dev/null 2>&1
