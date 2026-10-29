#!/usr/bin/env bash
# 装龙屏桌面应用：系统服务（推送、管理器）+ 应用中心图标。不需要管理员密码。
#   bash desktop/install.sh            装好，登录后自动开推送和管理器
#   bash desktop/install.sh --no-auto  只装，不开机自启（点图标时才启动）
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$units" "$apps" "$icons" "$HOME/.local/share/ryuo-studio"
install -m 644 "$here/ryuo-pusher.service" "$here/ryuo-manager.service" "$units/"
install -m 644 "$here/ryuo-studio.svg" "$icons/ryuo-studio.svg"
sed "s|@HOME@|$HOME|g" "$here/ryuo-studio.desktop" > "$apps/ryuo-studio.desktop"
systemctl --user daemon-reload
if [ "${1:-}" != "--no-auto" ]; then
  systemctl --user enable ryuo-manager.service ryuo-pusher.service
fi
update-desktop-database "$apps" 2>/dev/null || true
gtk-update-icon-cache -q "${icons%/scalable/apps}" 2>/dev/null || true
echo "装好了：应用中心里搜「龙屏」，右键可固定到 Dock。"
