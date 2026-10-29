#!/usr/bin/env bash
# 下载四家 AI 的图标（LobeHub Icons，MIT）。图形是各公司的商标，只用于在屏幕上标出是谁的额度。
set -euo pipefail
dir="$(cd "$(dirname "$0")/.." && pwd)/themes/_shared/logos"
base="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.101.0/icons"
for n in claude-color codex grok cursor; do curl -fsSL --max-time 20 -o "$dir/$n.svg" "$base/$n.svg"; done
# Cursor 原图是黑色立方体，黑底上看不见，转成跟随文字颜色的单色版
sed -e 's/stop-color="#000"/stop-color="currentColor"/g' -e 's/fill="#555"/fill="currentColor" fill-opacity=".7"/' -e 's/fill="#000"/fill="currentColor"/' "$dir/cursor.svg" > "$dir/cursor-mono.svg"
echo "图标已下载到 $dir"
