# 龙屏（ryuo-studio）

给 ROG 龙王 4 代（RYUO IV SLC 360 ARGB）冷头上那块 2240×1080 曲面屏做的一整套 Linux 方案：自己写的驱动、可切换的主题包、AI 额度面板、本机生成的裸眼 3D 循环视频，以及一个本机网页管理器。不依赖 ASUS InfoHub，不改屏幕系统，不碰固件。

> 状态：开发中。已在 Ubuntu、1.0.7 固件的真机上跑通分屏、图片推送和原生实时面板；Windows 版写好了，还没在真 Windows 上验过。

## 里面有什么

| 目录 | 做什么 |
|---|---|
| `driver/` | `ryuo` Python 包：USB HID 报文编解码、命令构造、按固件版本的能力表、心跳。净室实现，只参考公开的协议事实。 |
| `docs/` | `protocol.md` 协议说明（每项标注在哪个固件版本验证过）、`firmware.md` 固件版本调研。 |
| `themes/` | 主题包。每个包一个目录：`theme.json`（名字、布局、两种可调颜色）、`theme.css`（配色和字体）、`assets/`（整屏、主区、窄条三种尺寸的背景）、`video-prompt.txt`（生成视频用的描述）。 |
| `render/panel.html` | 额度面板的渲染页：窄条 634×1080、用量整屏 2240×1080、分屏效果图。 |
| `tools/` | `snapshot.py` 读额度（先问 magpie，再用本机快照补）、`render.py` 无头 Chrome 出图、`gen_video.py` 调本机 ComfyUI 里的 MiniMax H3 生成首尾相接的循环视频、`finish_video.py` 放大并转成屏幕能播的 HEVC、`pusher.py` 常驻推送、`aura_sync.py` 神光同步（`openrgb_sdk.py` 是它用的 OpenRGB 客户端）、`hostsys.py` 所有跟操作系统打交道的地方（温度和占用、时区、找 Chrome 和 adb、开关后台服务），Linux 和 Windows 各一套。 |
| `app/` | 管理器：挑主题、看实时效果、生成视频、改推送设置、开关推送。 |
| `desktop/` | 桌面应用：`bash desktop/install.sh` 装好后在应用中心搜「龙屏」，可固定到 Dock。推送和管理器做成登录自启的用户服务（`--no-auto` 则只在点图标时启动），点图标打开独立窗口，不用记端口。 |

## 额度面板怎么上屏

**原生实时版（默认，分屏用）**：屏的 adb 默认就是 root，`tools/native_panel.py` 把 `overlay/` 编出的程序和字体、图标、背景放进屏上的 `/data/local/tmp/ryuo/`，用 `app_process` 拉起来。它在右边窄条上方开一个图层，用安卓原生画布照 `render/panel.html` 的样子画面板。电脑经 `adb forward` 每分钟送一次数据、每 5 秒保活；倒计时、时间进度和「数据过期」提示在屏上按秒自己走，换数据时圆环有过渡动画。20 秒收不到消息它就自己藏起来；被杀或屏重启后，推送程序会重新拉起。不装应用、不动系统分区。编译：`bash overlay/build.sh`（需要 JDK 17、Android 33 的 `android.jar` 和 d8）。

**图片版（做法甲，整屏布局用，也是原生版的兜底）**：主机每 15 分钟读一次额度快照，用当前主题画好一张图，用 `adb push` 放进屏上的 `/sdcard/pcMedia/`，再通过 HID 发一次 `config` 让屏重新加载。每轮换一个带时间的新文件名，旧的随后删掉。

两种方式都要主机每秒给屏发一次心跳（CPU、显卡温度），不然约 10 秒后屏回到待机画面，推送程序在后台一直发。

## 神光同步（整机灯效跟主题走）

`tools/aura_sync.py` 经本机 OpenRGB 服务端给主板（含三路 ARGB，龙王的风扇和冷头灯接在这上面）、内存、显卡推实时颜色，30 帧（内存、显卡走 SMBus，15 帧）。颜色和屏上面板同一对：主色取主题的标题色，副色取 `theme.css` 的 `--accent2`；管理器里换主题，灯在 1.5 秒内渐变过去。主题想给灯单独定颜色，在 `theme.json` 里加 `"lights": {"colors": ["#..", "#.."], "effect": "breathe"}`。

需要 OpenRGB 1.0 以上（本机 2026-10-29 装的是官方 1.1 的 Debian trixie 包和配套的 `libhidapi-hotplug`）。`desktop/install.sh` 会装两个用户服务：`ryuo-openrgb`（只听 127.0.0.1 的服务端）和 `ryuo-lights`（同步本身）。`python3 -I tools/aura_sync.py --preview` 不连设备，只打印当前主题算出来的颜色。

设置写在 `~/.config/ryuo-studio/config.json`，都可省：`lights_enabled`、`lights_effect`（`flow` 流光 / `breathe` 呼吸 / `static` 常亮）、`lights_brightness`（0–100，默认 70）、`lights_devices`（默认主板、内存、显卡）、`lights_argb_leds`（主板每路 ARGB 接口设成多少颗灯，默认 60）、`lights_on_exit`（`off` 关灯 / `keep` 停在最后一帧）。写坏的值会被忽略，沿用上一次的。同步关着时只识别设备，不切任何模式；电脑睡眠醒来会重新接管一次。

模式带「自动存盘」标记的设备一律跳过，写进 `lights_devices` 也不碰：本机的 Keychron 键盘就是这样，OpenRGB 一给它切模式就会写进键盘存储。

还没做的：管理器页面里的开关；键盘（见上）；鼠标 ROG HARPE II ACE（OpenRGB 1.1 还不认）；龙王风扇每把的真实灯数（查不到公开数据，多设无害，只影响流光长短）。

## Windows

> 状态：代码和安装脚本写好了，单元测试把 Windows 那一半都跑到了，但还没在真 Windows 上装过。碰到问题请开 issue。

要准备的东西：

- Python 3.11 以上（python.org 版，安装时勾上 Add to PATH）。
- Android SDK Platform-Tools 里的 `adb`，加进 PATH，或者用环境变量 `RYUO_ADB` 指到 `adb.exe`。
- Chrome；没有的话会用系统自带的 Edge。
- 想显示 CPU 温度的话，装 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)，在 Options 里打开 Remote Web Server（端口 8085），并让它开机启动。不装的话温度显示破折号，别的照常。显卡温度和占用走 `nvidia-smi`，只支持 N 卡。
- 「生成视频」要用 `ffmpeg` 和本机 ComfyUI；只放现成的视频就不需要。
- 浑天铜仪、墨夜流金两套主题用的中文衬线体（Noto Serif CJK）不在仓库里。没装的话，这两套主题的字会换成屏上自带的无衬线体。

装：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\install_windows.ps1
```

它会装 `hidapi`（Windows 上连屏幕要用），建两个计划任务 `RyuoStudio-pusher` 和 `RyuoStudio-manager`（登录时启动，挂了 10 秒后自己重启，日志在 `%USERPROFILE%\.local\share\ryuo-studio\`），再在开始菜单放一个「龙屏」快捷方式，点它就打开管理器窗口。加 `-NoAuto` 就不开机自启，只在点图标时启动；加 `-Uninstall` 删掉任务和快捷方式。

和 Linux 不一样的地方：

- 连屏幕走 hidapi，不走 hidraw，也不用装 udev 规则。要是华硕的 Armoury Crate 或 InfoHub 开着，它们可能会占着屏幕的连接，先关掉。
- 屏上的时区用「GMT+08:00」这种偏移写法：Windows 拿不到 IANA 时区名，有夏令时的地方，切换后最多一分钟屏上时间就跟着改过来（每次送数据都重算偏移）。
- 想自己编屏上程序的话，用 `python overlay\build.py`（Linux 上 `overlay/build.sh` 也还能用）。

## 额度从哪来

面板显示 Claude、Codex、Grok、Cursor 四家订阅的用量和重置时间。`tools/snapshot.py` 先问 magpie 的 `magpie accounts --json`（装了的话），缺的窗口再从一份快照 JSON 里补，路径用 `RYUO_QUOTA_SNAPSHOT` 指定，格式写在 `snapshot.py` 开头。两样都没有的话，面板会显示「数据过期」，不会编数。

## 加一个主题

复制 `themes/starship/`，改 `theme.json` 里的 `id`、`name`、`layout`（`rings` 环形表 / `tubes` 漏刻 / `cards` 玻璃卡）和 `colors`，换掉 `assets/` 里的三张背景和 `theme.css` 里的配色字体即可。管理器会自动列出来。

## 安全边界

- 驱动拒绝发送会触发固件更新的命令（`upgrade`、`transport` 等）。
- 不升级固件：官方路径没有降级，升级失败的案例不少。
- 默认演练模式：没在配置里打开 `device_enabled` 之前，所有上屏操作只打印，不发送。
- 神光同步只用实时推色和临时硬件模式，从不发「存进设备」（OpenRGB 的 SAVEMODE）；重启或断电后各设备回到它自己存的样子。

## 授权

代码：[MIT](LICENSE)。字体见 `themes/_shared/fonts/FONTS.md`（都是 SIL OFL 1.1）。四家 AI 的图标是各自公司的商标，不随仓库分发，用 `tools/fetch_logos.sh` 在本机下载。

---

**English.** Linux toolkit for the ROG RYUO IV curved AIO screen: a clean-room HID driver (`driver/`), switchable theme packs, an AI-quota panel rendered on the host and pushed to the screen, seamless-loop "3D" clips generated locally with MiniMax H3 via ComfyUI, and a localhost web manager. No firmware changes; dry-run by default. MIT licensed.
