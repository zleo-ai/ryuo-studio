#!/usr/bin/env bash
# 装上龙神屏的 HID 权限规则。要输一次管理员密码。
# 撤回：sudo rm /etc/udev/rules.d/70-ryuo-iv.rules && sudo udevadm control --reload && sudo udevadm trigger
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
sudo install -m 0644 "$here/70-ryuo-iv.rules" /etc/udev/rules.d/70-ryuo-iv.rules
sudo udevadm control --reload
sudo udevadm trigger --action=change --subsystem-match=hidraw
sleep 1
for d in /sys/class/hidraw/hidraw*; do
  if grep -q 'HID_ID=0003:00001C75:00001C76' "$d/device/uevent" 2>/dev/null; then
    n="/dev/$(basename "$d")"
    if [ -r "$n" ] && [ -w "$n" ]; then echo "好了：$n 现在可以读写"; else echo "规则装了，但 $n 还不能读写，把这句话发给我"; fi
  fi
done
