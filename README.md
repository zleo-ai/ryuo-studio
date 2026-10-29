# 龙屏（ryuo-studio）

给 ROG 龙王 4 代（RYUO IV SLC 360 ARGB）冷头上那块 2240×1080 曲面屏做的一整套 Linux 方案：自己写的驱动、可切换的主题包、AI 额度面板、本机生成的裸眼 3D 循环视频，以及一个本机网页管理器。不依赖 ASUS InfoHub，不改屏幕系统，不碰固件。

> 状态：开发中。已在 1.0.7 固件的真机上跑通分屏、图片推送和原生实时面板。

## 里面有什么

| 目录 | 做什么 |
|---|---|
| `driver/` | `ryuo` Python 包：USB HID 报文编解码、命令构造、按固件版本的能力表、心跳。净室实现，只参考公开的协议事实。 |
| `docs/` | `protocol.md` 协议说明（每项标注在哪个固件版本验证过）、`firmware.md` 固件版本调研。 |
| `themes/` | 主题包。每个包一个目录：`theme.json`（名字、布局、两种可调颜色）、`theme.css`（配色和字体）、`assets/`（整屏、主区、窄条三种尺寸的背景）、`video-prompt.txt`（生成视频用的描述）。 |
| `render/panel.html` | 额度面板的渲染页：窄条 634×1080、用量整屏 2240×1080、分屏效果图。 |
| `tools/` | `snapshot.py` 读额度快照、`render.sh` 无头 Chrome 出图、`gen_video.py` 调本机 ComfyUI 里的 MiniMax H3 生成首尾相接的循环视频、`finish_video.sh` 放大并转成屏幕能播的 HEVC、`pusher.py` 常驻推送。 |
| `app/` | 管理器：挑主题、看实时效果、生成视频、改推送设置、开关推送。 |
| `desktop/` | 桌面应用：`bash desktop/install.sh` 装好后在应用中心搜「龙屏」，可固定到 Dock。推送和管理器做成登录自启的用户服务（`--no-auto` 则只在点图标时启动），点图标打开独立窗口，不用记端口。 |

## 额度面板怎么上屏

**原生实时版（默认，分屏用）**：屏的 adb 默认就是 root，`tools/native_panel.py` 把 `overlay/` 编出的程序和字体、图标、背景放进屏上的 `/data/local/tmp/ryuo/`，用 `app_process` 拉起来。它在右边窄条上方开一个图层，用安卓原生画布照 `render/panel.html` 的样子画面板。电脑经 `adb forward` 每分钟送一次数据、每 5 秒保活；倒计时、时间进度和「数据过期」提示在屏上按秒自己走，换数据时圆环有过渡动画。20 秒收不到消息它就自己藏起来；被杀或屏重启后，推送程序会重新拉起。不装应用、不动系统分区。编译：`bash overlay/build.sh`（需要 JDK 17、Android 33 的 `android.jar` 和 d8）。

**图片版（做法甲，整屏布局用，也是原生版的兜底）**：主机每 15 分钟读一次额度快照，用当前主题画好一张图，用 `adb push` 放进屏上的 `/sdcard/pcMedia/`，再通过 HID 发一次 `config` 让屏重新加载。每轮换一个带时间的新文件名，旧的随后删掉。

两种方式都要主机每秒给屏发一次心跳（CPU、显卡温度），不然约 10 秒后屏回到待机画面，推送程序在后台一直发。

## 加一个主题

复制 `themes/starship/`，改 `theme.json` 里的 `id`、`name`、`layout`（`rings` 环形表 / `tubes` 漏刻 / `cards` 玻璃卡）和 `colors`，换掉 `assets/` 里的三张背景和 `theme.css` 里的配色字体即可。管理器会自动列出来。

## 安全边界

- 驱动拒绝发送会触发固件更新的命令（`upgrade`、`transport` 等）。
- 不升级固件：官方路径没有降级，升级失败的案例不少。
- 默认演练模式：没在配置里打开 `device_enabled` 之前，所有上屏操作只打印，不发送。

## 授权

代码：TODO（打算用 MIT）。字体见 `themes/_shared/fonts/FONTS.md`（都是 SIL OFL 1.1）。四家 AI 的图标是各自公司的商标，不随仓库分发，用 `tools/fetch_logos.sh` 在本机下载。

---

**English.** Linux toolkit for the ROG RYUO IV curved AIO screen: a clean-room HID driver (`driver/`), switchable theme packs, an AI-quota panel rendered on the host and pushed to the screen, seamless-loop "3D" clips generated locally with MiniMax H3 via ComfyUI, and a localhost web manager. No firmware changes; dry-run by default.
