[English](README.en.md)

# 龙屏 · ryuo-studio

给 ROG 龙王 4 代（RYUO IV SLC 360 ARGB）冷头上那块 2240×1080 曲面屏用的 Linux 方案：自己写的驱动、七套可切换主题、Claude / Codex / Grok / Cursor 的额度面板，还有一个只开在本机的网页管理器。不依赖 Armoury Crate 或 InfoHub，不改屏幕系统，不碰固件。

[![MIT](https://img.shields.io/github/license/zleo-ai/ryuo-studio)](LICENSE)
[![Stars](https://img.shields.io/github/stars/zleo-ai/ryuo-studio)](https://github.com/zleo-ai/ryuo-studio/stargazers)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Linux · Windows](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-555555)](#windows)

> 开发中。维护者在 Ubuntu、固件 V1.0.7 上跑通过分屏、图片推送和屏上原生面板，记录在 [`docs/protocol.md`](docs/protocol.md) 文末。驱动能力表里 1.0.7 的原生 HEVC 仍标成 `unknown`，默认还是推荐 1920×926 的 H.264。Windows 的安装脚本和单元测试写好了，还没在真 Windows 上装过。

<p>
<img src="docs/assets/hero.jpg" alt="雨夜霓虹主题的分屏效果：左边是城市夜景和时钟，右边窄条是四家 AI 的额度" width="100%">
</p>

雨夜霓虹的分屏效果图。左边主画面，右边弯过去的窄条放额度。图是本机用 `render/panel.html` 画的，不是冷头实拍。额度数字来自一份示例快照；时钟、日期和 CPU / GPU 温度是效果图里写死的示意值（46°C / 45°C）。

## 为什么做这个

这块屏官方只配了 Windows 上的 InfoHub。接到 Linux 上，它会停在待机画面。龙屏把「画什么、推什么、灯跟着什么颜色」都放回自己的电脑：屏幕走 USB HID，文件走 adb，灯走本机的 OpenRGB。

默认是演练。配置里没打开 `device_enabled` 之前，上屏操作只打印、不发送。驱动会拒绝 `upgrade`、`transport` 这一类会碰固件或让屏上服务崩溃的命令。官方那条升级路径不能降级，所以这个仓库不负责刷固件。

## 能做什么

- **七套主题。** 星舰舱、日冕、北境极光、墨夜流金、浑天铜仪、液态光谱、雨夜霓虹。布局有环形表、漏刻、玻璃卡、同心光环。管理器里点一下，画面、配色、字体一起换。
- **额度面板。** 显示 Claude、Codex、Grok、Cursor 四家的用量和重置时间。分屏时面板待在右边窄条；整屏时视频铺满，用量定时浮出来。
- **两种上屏方式。** 默认是屏上原生绘制，电脑每分钟送一次数，倒计时在屏上自己走。另一路是定时把画好的图 `adb push` 上去。两种都靠每秒一次的心跳把屏留在工作画面里。
- **循环视频。** 本机 ComfyUI 里的 MiniMax H3 按主题参考图生成首尾帧相同的片子（仓库里叫裸眼 3D），再转成屏幕能播的 HEVC。不装 ComfyUI 也可以只放自己的视频。
- **灯跟主题走。** 经本机 OpenRGB 给主板（含三路 ARGB）、内存、显卡推颜色。只用临时模式，不把灯效写进设备。
- **本机管理器。** `http://127.0.0.1:7788`。挑主题、看效果图、改推送、演练上屏。装好桌面项之后，应用中心里搜「龙屏」。

## 快速开始

需要 Python 3.11 以上。下面几条不连屏幕也能跑，都在这台环境里跑过。

```bash
git clone https://github.com/zleo-ai/ryuo-studio.git
cd ryuo-studio

# 管理器，只听 127.0.0.1:7788
python3 -I app/server.py
```

浏览器打开 <http://127.0.0.1:7788>。这时候按钮写的是「演练一次上屏」：屏幕没被允许连接，点下去只记一笔日志。

驱动同样默认演练，只打印报文：

```bash
cd driver
python3 -m ryuo profiles
python3 -m ryuo --body config --media clip.mp4 --slots "CPU Temperature" "Date&Time"
python3 -m pytest
```

不连灯，只看当前主题算出来的颜色：

```bash
python3 -I tools/aura_sync.py --preview
```

要在应用中心看到「龙屏」，并做成登录自启的用户服务：

```bash
bash desktop/install.sh            # 登录后开推送和管理器；本机有 openrgb 时一并开灯
bash desktop/install.sh --no-auto  # 只装，点图标才启动
```

这需要一个正常的 systemd 用户会话。HID 权限另一步，要管理员密码：`bash tools/install-udev.sh`。

真正往屏幕送东西之前，还要：

- Android SDK Platform-Tools 里的 `adb`（或环境变量 `RYUO_ADB`）。出厂的屏就能连，文件放进 `/sdcard/pcMedia/`。
- 在 `~/.config/ryuo-studio/config.json` 里把 `device_enabled` 设成 `true`。管理器页面上没有这个开关。
- 出效果图需要 Chrome 或 Chromium。Windows 上没有 Chrome 就用系统 Edge。
- 额度数字来自 magpie，或 `RYUO_QUOTA_SNAPSHOT` 指到的一份 JSON。两样都没有时，管理器打得开，但效果图出不来，程序也不会编一套数。见下文。

## 看起来是什么样

下面都是分屏效果图，和上屏用的是同一张渲染页。七套主题轮流换，额度是同一份示例数据。

![七套主题的分屏效果](docs/assets/themes.gif)

| 日冕 | 北境极光 |
| --- | --- |
| ![日冕](docs/assets/panel-eclipse.jpg) | ![北境极光](docs/assets/panel-aurora.jpg) |
| 墨夜流金 | 浑天铜仪 |
| ![墨夜流金](docs/assets/panel-inkgold.jpg) | ![浑天铜仪](docs/assets/panel-armillary.jpg) |
| 星舰舱（默认） | 液态光谱 |
| ![星舰舱](docs/assets/panel-starship.jpg) | ![液态光谱](docs/assets/panel-liquid.jpg) |

这些图里的百分比不是任何人的真实账单。示例快照是：Claude 7 天 42%（5 小时窗口 18%）、Codex 27%、Grok 63%（Build 21%）、Cursor 本月 48%（其他模型 11%）。分屏左侧的 `15:58`、`10-29 周四`、CPU 46°C、GPU 45°C 写在 `render/panel.html` 里，只给效果图用，不是这台电脑的传感器。

管理器本身（暗色，屏幕处于演练）：

![龙屏管理器：主题列表和用量效果图](docs/assets/manager.png)

![推送设置。底部说明还没允许连接屏幕](docs/assets/manager-settings.png)

这台环境没有接龙王冷头，所以没有「屏上实况」。那一栏要 `device_enabled` 打开、并且 adb 认得到屏幕，才会每 5 秒截一张真机画面。

## 额度怎么上屏

**原生实时（默认，分屏用）。** 屏的 adb 出厂就是 root。`tools/native_panel.py` 把 `overlay/` 编出来的程序连同字体、图标、背景放进屏上的 `/data/local/tmp/ryuo/`，用 `app_process` 拉起来，在窄条上按 `render/panel.html` 的样子画。电脑经 `adb forward` 每分钟送一次数据、每 5 秒保活；倒计时和「数据过期」在屏上按秒自己走。20 秒收不到消息，图层自己藏起来。不装应用，不动系统分区。编译：`bash overlay/build.sh`，需要 JDK 17、Android 33 的 `android.jar` 和 d8（默认在 `~/.local/share/ryuo-toolchain/`）。Windows 上用 `python overlay/build.py`。

**图片（整屏布局，也是原生版的兜底）。** 主机按设定的分钟数读一次额度，用当前主题画一张图，`adb push` 到 `/sdcard/pcMedia/`，再经 HID 发一次 `config` 让屏重新加载。每轮换一个新文件名，因为同名覆盖不会刷新。

两条路都要主机每秒发一次心跳（CPU、显卡温度）。大约 10 秒没有心跳，屏回到待机。推送程序在后台一直发。

## 额度从哪来

`tools/snapshot.py` 先问 magpie 的 `magpie accounts --json`（装了的话），缺的窗口再从一份快照 JSON 里补。路径用 `RYUO_QUOTA_SNAPSHOT` 指定，默认是 `~/.local/state/aios/quota-snapshot.json`。格式是 `{"generated_at_ms", "providers":[{"windows":[{"id","used_pct","resets_at_ms", ...}]}]}`，窗口 id 写在 `snapshot.py` 开头。

没有 magpie、也没有这份快照时，面板不会编数。`snapshot.py` 目前会因为一个窗口都没有而退出，管理器里的效果图就出不来。管理器页面、主题切换和驱动演练不受影响。

四家图标是各公司的商标，不进仓库。本机执行 `bash tools/fetch_logos.sh` 下载；缺了图标时面板只显示名字。

## 加一个主题

复制 `themes/starship/`，改 `theme.json` 里的 `id`、`name`、`layout`（`rings` 环形表、`tubes` 漏刻、`cards` 玻璃卡、`orbits` 同心光环）和 `colors`，换掉 `assets/` 里的三张背景（整屏、主区、窄条）和 `theme.css`。管理器会自己列出来。想给灯单独定颜色，加 `"lights": {"colors": ["#..", "#.."], "effect": "breathe"}`。

## 神光同步

`tools/aura_sync.py` 经本机 OpenRGB（`127.0.0.1:6742`）给主板、内存、显卡推颜色，主板 30 帧，内存和显卡走 SMBus、15 帧。主色取主题标题色，副色取 `theme.css` 的 `--accent2`。管理器里换主题，灯在 1.5 秒内渐变过去。

需要 OpenRGB 1.0 以上。`desktop/install.sh` 在找得到 `openrgb` 时会装两个用户服务：`ryuo-openrgb`（只听 127.0.0.1）和 `ryuo-lights`。

设置都在 `~/.config/ryuo-studio/config.json`，都可以不写：

| 键 | 作用 |
| --- | --- |
| `lights_enabled` | 关着时只识别设备，不切模式 |
| `lights_effect` | `flow` 流光 / `breathe` 呼吸 / `static` 常亮。主题里写了 `lights.effect` 时以主题为准 |
| `lights_brightness` | 0–100，默认 70 |
| `lights_devices` | 默认主板、内存、显卡 |
| `lights_argb_leds` | 主板每路 ARGB 设成多少颗灯，默认 60。多设无害，只影响流光长短 |
| `lights_on_exit` | `off` 关灯 / `keep` 停在最后一帧 |

写坏的值会被忽略，沿用上一次。同步只用实时推色和临时硬件模式，从不发 OpenRGB 的 SAVEMODE。带「自动存盘」标记的设备一律跳过，写进 `lights_devices` 也不碰：本机的 Keychron 键盘就是这样，一切模式就会写进键盘自己的存储。电脑睡眠醒来会重新接管一次。

## 配置

管理器把选择写进 `~/.config/ryuo-studio/config.json`。常用键：

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `theme` | `starship` | 主题 id |
| `layout` | `split` | `split` 面板常驻右边；`full` 整屏，用量定时浮出 |
| `renderer` | `native` | `native` 屏上绘制；`png` 定时推图 |
| `device_enabled` | `false` | `true` 才真正连接屏幕。只能改这个文件 |
| `push_minutes` | `15` | 图片模式的刷新间隔 |
| `full_show_seconds` / `full_hide_seconds` | 15 / 45 | 整屏时用量浮出多久、隔多久再浮 |
| `panel_width` | `634` | 面板宽度。634 正好是弯曲的那段 |
| `panel_glass` | `true` | 透明玻璃，视频铺满、面板叠上去 |
| `hud` / `hud_items` | 开，`cpu` `gpu` `mem` | 左边自己画的时钟和占用 |

日志和生成的图在 `~/.local/share/ryuo-studio/`。

## Windows

> 代码和 `desktop/install_windows.ps1` 写好了，单元测试覆盖了 Windows 那一半，但还没在真 Windows 上装过。碰到问题请开 issue。

要准备的东西：

- Python 3.11 以上（python.org 的安装包，勾上 Add to PATH）。
- Android SDK Platform-Tools 里的 `adb`，放进 PATH，或用 `RYUO_ADB` 指到 `adb.exe`。
- Chrome；没有就用系统自带的 Edge。
- 想显示 CPU 温度：装 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)，在 Options 里打开 Remote Web Server（端口 8085），并让它开机启动。不装的话温度是破折号，别的照常。显卡温度和占用走 `nvidia-smi`，只支持 NVIDIA。
- 生成视频要用 `ffmpeg` 和本机 ComfyUI。只放现成视频就不需要。
- 浑天铜仪、墨夜流金用的中文衬线体（Noto Serif CJK）不在仓库里。没装的话，这两套主题的字会换成屏上自带的无衬线体。

```powershell
powershell -ExecutionPolicy Bypass -File desktop\install_windows.ps1
```

脚本会装 `hidapi`，建两个计划任务 `RyuoStudio-pusher` 和 `RyuoStudio-manager`（登录时启动，挂了大约 10 秒后自己重启，日志在 `%USERPROFILE%\.local\share\ryuo-studio\`），再在开始菜单放一个「龙屏」。`-NoAuto` 不设开机自启；`-Uninstall` 删掉任务和快捷方式，配置和视频留着。

和 Linux 不一样的地方：

- 连屏幕走 hidapi，不走 hidraw，也不用 udev 规则。Armoury Crate 或 InfoHub 开着的话可能会占着连接，先关掉。
- 屏上时区用 `GMT+08:00` 这种偏移。Windows 拿不到 IANA 时区名；有夏令时的地方，切换后最多一分钟，屏上时间会跟着改（每次送数据都重算偏移）。

## 安全边界

- 驱动拒绝发送 `upgrade`、`transport`、`transported`、`turboPump`、`rotate`、`power`。
- 不升级固件。官方 OTA 这条路降不回去，升级失败的案例也不少。细节在 [`docs/firmware.md`](docs/firmware.md)。
- 默认演练。`device_enabled` 仍是 `false` 时，所有上屏操作只打印，不打开设备。
- 神光同步不把模式存进设备。重启或断电后，各设备回到它自己存的样子。
- 屏是 OLED。固件会在媒体切换时插一段防烧屏短片，没有命令能关掉。片子越长，这次保护间隔得越久。

## 仓库里有什么

| 目录 | 做什么 |
| --- | --- |
| `driver/` | `ryuo` Python 包。USB HID 编解码、命令、按固件版本的能力表、心跳。核心零依赖；真要连设备才需要 `hidapi`。 |
| `docs/` | `protocol.md` 协议说明（每项标了在哪个固件上见过），`firmware.md` 固件版本调研。 |
| `themes/` | 主题包。`theme.json`、`theme.css`、三种尺寸的背景、`video-prompt.txt`。 |
| `render/panel.html` | 额度面板的渲染页：窄条 634×1080、用量整屏 2240×1080、分屏效果图。 |
| `tools/` | 读额度、无头浏览器出图、生成视频、常驻推送、神光同步、跟操作系统打交道的部分（Linux 和 Windows 各一套）。 |
| `app/` | 管理器。 |
| `overlay/` | 屏上原生面板。 |
| `desktop/` | 桌面项、用户服务、Windows 计划任务。 |

## 还没做的

- 管理器页面里的神光同步开关。现在只能改配置文件。
- 键盘。OpenRGB 里标了自动存盘的设备会跳过。
- 鼠标 ROG HARPE II ACE。OpenRGB 1.1 还不认。
- 龙王风扇每一把的真实灯数。查不到公开数据；接口上多设几颗灯只影响流光长短。
- 在一台真实的 Windows 上把安装脚本走一遍。

## 参与

欢迎开 issue。加主题是改动最小的一种贡献：复制 `themes/starship/` 即可，管理器会自动发现。

驱动的测试不碰硬件：

```bash
cd driver && python3 -m pytest
```

请不要往驱动里加会触发固件更新的命令。协议事实和「哪一项在哪版固件上见过」写在 `docs/protocol.md`，对不上的时候改能力表，不要把未验证的项标成 verified。

## 许可

代码 [MIT](LICENSE)，版权 Maple。字体见 `themes/_shared/fonts/FONTS.md`，都是 SIL OFL 1.1。四家 AI 的图标是各自公司的商标，不随仓库分发。

驱动是净室实现：协议事实来自公开笔记和抓包，代码没有从别的项目抄。出处和每一项的验证情况写在 [`docs/protocol.md`](docs/protocol.md)。

---

这块屏在你的机器上如果也好用，给仓库一颗星。碰到对不上的行为，开一个 issue，写上固件版本就行。
