#!/usr/bin/env bash
# 龙屏应用入口：确保管理器和推送都在跑，再用独立窗口打开管理器。
# 由 ryuo-studio.desktop 调用；重复点击只会把已有窗口叫到前面。
set -u
systemctl --user start ryuo-manager.service ryuo-pusher.service
for _ in $(seq 1 50); do
  curl -s --noproxy '*' -o /dev/null -m 1 http://127.0.0.1:7788/api/state && break
  sleep 0.2
done
exec google-chrome --app=http://127.0.0.1:7788/ --class=ryuo-studio \
  --user-data-dir="${XDG_DATA_HOME:-$HOME/.local/share}/ryuo-studio/app-window" \
  --no-first-run --no-default-browser-check --window-size=1280,900 >/dev/null 2>&1
