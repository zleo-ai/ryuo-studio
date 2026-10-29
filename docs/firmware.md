# RYUO IV 屏幕固件调研

调研日期：2026-10-29（北京时间）。只读调研，没有碰设备。

写法约定：【查到】有出处可点；【推测】是判断，没有直接证据；【本机】来自 2026-10-28 那次只读调研，本次没有重新测。

## 一、结论先说

1. 官网上能下到的最新版是 **V1.0.10**。【查到】ROG 全球站 RYUO IV SLC 360 ARGB 的「BIOS & Firmware」页只列了一个文件：「ROG RYUO IV Series Firmware File，Version 1.0.10，488.45 MB，2026/01/19」，SHA-256 `6F2A2BD9…AA370D1E`。[ROG 支持页](https://rog.asus.com/cooling/cpu-liquid-coolers/rog-ryuo/rog-ryuo-iv-slc-360-argb/helpdesk_bios/)
   - 【查到】初音版另有一个包，「ROG RYUO IV MIKU Firmware File，Version 1.0.10，490.97 MB，2026/06/24」，标注配 InfoHub v0.8.0。[初音版支持页](https://rog.asus.com/cooling/cpu-liquid-coolers/rog-ryuo/rog-ryuo-iv-360-argb-hatsune-miku-edition/helpdesk_bios/)。说明 1.0.10 到 2026 年 6 月还在当现行版发，没有更新的公开号。
   - 「1.1.0」这个号的情况见第三节：论坛里客服说过 1.1.0，但同一帖的解决方案刷完后设备显示的是 V1.0.10。【推测】1.1.0 很可能是客服口误或内部叫法，不是另一个公开版本。证据等级：中。
   - 中国站同型号的固件页没抓到内容（页面是动态的，抓回来是空），国行有没有不同版本号，**没核实**。
2. 我们这块屏是 **V1.0.7**【本机】。按现有证据，一份驱动可以同时支持 1.0.7 和 1.0.10：报文格式、命令表、组件列表在两版上都一样，差别在 USB 厂商号和几张媒体素材。需要按版本分开的只有「默认用什么视频编码」「自定义标签读数要不要提醒未验证」这类能力开关，驱动里已经做成按版本查表。
3. **不建议现在升级**。升级的好处（原生 2240×1080 HEVC 在 1.0.10 上验证过）在 1.0.7 上可能本来就有，只是没人测过；坏处是升级失败案例不少，而且降不回来（见第四节）。要不要升，Maple 定。

## 二、各版本协议验证情况

| 版本 | USB ID | 协议验证情况 | 出处 |
|---|---|---|---|
| 1.0.3 | `0B05:1C76`，产品串 `HID Interface` | 协议是从这个版本的官方固件包里拆 APK 读出来的；后来在 1.0.7/1.0.10 实机核对。1.0.3 本身有没有逐项实测，原文没写清 | 【查到】[FoxPodZ PROTOCOL.md §1、§10b](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md) |
| 1.0.7 | `1C75:1C76`（ODM 百一的厂商号），`HID Interface` | 实机验证过：亮度换算表、`power`、`displayInSleep`、`filter` 叠加层、`ratio`（证据弱）、组件列表；`Fan Speed Coolant` 只看到标签变了、数值没核实 | 【查到】同上 §4、§5、§9、§10；【本机】我们的屏 V1.0.7、`1c75:1c76` |
| 1.0.9 | 未知 | 只知道有用户装过（升级出过问题），没人在上面测协议 | 【查到】[ROG 论坛 1119175](https://rog-forum.asus.com/t5/all-in-one-cooling/rog-ryuo-iv-slc-360-firmware-1-0-9-the-selected-firmware-file-is/td-p/1119175) |
| 1.0.10 | `0B05:1C76`，`ROG RYUO IV Standard` | 验证最全：报文大小上限、16 个组件全部能显示、自定义标签读数、HEVC 原生分辨率、`conn`/`mediaDelete`、分屏几何、降级被拒 | 【查到】FoxPodZ PROTOCOL.md §2、§4、§7、§8、§10、§10b；[shorti1996/ryuo-display](https://github.com/shorti1996/ryuo-display) 也只在 1.0.10 上测过 |
| 1.1.0 | — | 无。见第三节 | — |

【查到】FoxPodZ 说三版（1.0.3/1.0.7/1.0.10）屏上桌面程序的版本串完全一样，所以命令分发表也一样；组件列表、自定义读数、两个渲染 bug、亮度传递函数都是先在 1.0.7 上得出、再在 1.0.10 上复测一致。[PROTOCOL.md §10b](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md)

## 三、版本之间的差别

已知差别【查到】（[PROTOCOL.md §1、§10b](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md)）：

- **USB 厂商号会变**：1.0.3 和 1.0.10 是 `0B05`，只有 1.0.7 是 `1C75`；产品号一直是 `1C76`。所以找设备只能按产品号 + 接口 0，厂商号只当白名单，不能只按 `0B05` 匹配（主板的 Aura 灯控也是 `0B05`）。驱动已经这样做。
- **产品串**：1.0.10 变成 `ROG RYUO IV Standard`。
- **媒体素材换过**：1.0.10 的 `standby.mp4` 内容和预设 `RYUO_IV_HW_Info_05.mp4` 一样，1.0.7 上是独立片子；1.0.7 的 `Screensaver.mp4` 内容和预设 02 一样。别拿「画面长什么样」判断屏在哪个状态。
- **没发现**：新命令、字段改名、删掉的后门。`Fan Speed <标签>` 读数在 1.0.10 上明确还能用；1.0.7 上看到了标签路径生效。

没核实、需要真机确认的：

- **HEVC 原生分辨率在 1.0.7 上能不能播**。1.0.10 上 2240×1080 HEVC 能播、同尺寸 H.264 黑屏【查到】；Jacksongio 在 1.0.7 上用的是 H.264 1280×624【查到】（[Jacksongio README](https://github.com/Jacksongio/asus_ryuo_iv_linux)）。【推测】解码器上限来自芯片和系统里的编解码配置，桌面程序版本又相同，1.0.7 大概率也能播 HEVC，但没测过。驱动在 1.0.7 档默认推荐 1920×926 H.264（ASUS 自带素材的规格），实测通过后再把这一档改成 `verified`。
- **待机超时**：FoxPodZ 说约 10 秒（1.0.10 环境下写的）【查到】；dragocz95 的 README 写 5 秒，没写版本【查到】（[dragocz95 README](https://github.com/dragocz95/asus-ryuo-iv-linux)）。驱动每 1 秒发一次心跳，并拒绝 ≥ 超时一半的心跳间隔，两种说法都兜得住。

关于「1.1.0」：

- 【查到】2026-01-03 有用户说 InfoHub 升到 v0.7.6 后想刷「1.1.0」，刷到最后报错、仍显示 1.0.9，预设视频也没了；客服回复让彻底卸载 InfoHub 后用官网「ROG RYUO IV Series Firmware File」更新到「1.1.0」。被采纳的解决方案是用 Revo Uninstaller 清掉 InfoHub、重装 v0.7.6 再刷，**刷完显示 V1.0.10**。[ROG 论坛 1133033](https://rog-forum.asus.com/t5/all-in-one-cooling/rog-ryuo-iv-slc-360-firmware-issue-1-1-0/td-p/1133033)
- 【查到】Reddit 有帖标题说 InfoHub 0.7.6 后检测不到屏、「没法升到 1.1.0」（只看到搜索摘要，正文被 Reddit 拦了）。[Reddit](https://www.reddit.com/r/ASUSROG/comments/1q2u1cz/asus_rog_ryuo_iv_lcd_not_detected_after_infohub/)
- 【推测】官网文件写 1.0.10，用户刷完显示 V1.0.10，所以「1.1.0」最可能就是 1.0.10 的误称。驱动对任何不在表里的版本（包括真出现的 1.1.x）都走「保守档」。

## 四、升级失败与降级

升级失败【查到】：

- 1.0.9 官网文件名不对，InfoHub 认不出，要改名成 `ROG RYUO IV Series Firmware File.zip` 才能刷。[ROG 论坛 1119175](https://rog-forum.asus.com/t5/all-in-one-cooling/rog-ryuo-iv-slc-360-firmware-1-0-9-the-selected-firmware-file-is/td-p/1119175)
- 刷到最后报错、版本没变、预设视频丢失（上面 1133033 那帖）。
- 初音版「InfoHub 未连接 + 固件更新失败（检测到 ADB）」的帖子，作者说最后解决了，正文被 Cloudflare 拦住没读到。[ROG 论坛 1147677](https://rog-forum.asus.com/t5/all-in-one-cooling/rog-ryuo-iv-miku-infohub-quot-not-connected-quot-firmware-update/td-p/1147677)
- 2026-10-28 调研里记过的：1.0.9 后屏幕不被识别要重装系统、升级后卡住 InfoHub 不再识别等。[论坛 1122414](https://rog-forum.asus.com/t5/all-in-one-cooling/rog-ryuo-iv-slc-360-argb-problem-lcd-not-connected/td-p/1122414)、[论坛 1137482](https://rog-forum.asus.com/t5/all-in-one-cooling/critical-detection-and-firmware-issue-asus-ryuo-iv/td-p/1137482)

降级【查到】：走官方 OTA 这条路降不回去。InfoHub 本身不拦，会把旧包交给屏；屏的 recovery 校验签名通过，但拿包里的构建时间和当前系统比，旧了就报 E3003 拒装，然后停在一个需要按键的 recovery 菜单（屏上没有按键），只能 `adb reboot` 出来，InfoHub 那边会一直卡住。1.0.3 构建于 2025-09-09，1.0.10 构建于 2025-12-29。[PROTOCOL.md §10b](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md)、[recovery-downgrade.log](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/captures/recovery-downgrade.log)

【推测】所以 1.0.7 一旦升到 1.0.10，就回不到 1.0.7。引导加载器虽然没锁，但没有公开镜像，不算可用的回退路径。

## 五、一份驱动能不能兼容两版

能。具体做法（已在 `driver/ryuo/firmware.py` 实现）：

- 找设备：产品号 `1C76` + 接口 0，厂商号在 `{0B05, 1C75}` 白名单里。
- 报文、命令、组件字符串：两版共用一套。
- 按版本查表的能力：原生 HEVC、自定义标签读数、亮度能不能给到 102、分屏、预设实时刷新、媒体列出/删除。每项标「verified / partial / inferred / unknown」。不是 verified 的功能照样能用，但会发 `CapabilityWarning` 提醒去屏上看一眼。
- 连上后可以发 `POST conn` 读回 `version.firmware`（如 `V1.0.10`）自动换档；不认识的版本走保守档：亮度最高 100、视频推荐 1920 宽 H.264。
- 默认档是 1.0.7。

## 六、只有真机能确认的事（按优先级）

1. 在 1.0.7 上，`POST conn` 的回复格式是不是和 1.0.10 一样（尤其 `version.firmware` 字段），驱动的自动识别靠它。
2. 1.0.7 上 `Fan Speed <标签>`：标签和数值都跟着变（数值每秒变才算活的，旧数会残留）。
3. 1.0.7 上 2240×1080 HEVC（`hvc1`）能不能播；不能就固定用 1920×926 H.264。
4. 1.0.7 上停发心跳后多少秒回待机（5 秒还是 10 秒）。
5. 1.0.7 上分屏 1606+634 的几何和 `Screen Splitting` 字符串（只在 1.0.10 上量过）。
6. `mediaDelete` 在 1.0.7 上能删（1.0.10 验证过）。
7. 1.0.7 实际的报文大小上限是否同为 1024（只在 1.0.10 上测过）。

## 七、本次没查到 / 失败的来源

- ROG 论坛 1147677、1159033 两帖：抓取时被 Cloudflare 人机验证拦住（jina 返回 403 / 验证页），只看到搜索摘要。
- Reddit 帖：被 Reddit 拦截（403），只看到标题和摘要。
- 中国站固件页（rog.asus.com.cn）：firecrawl 返回空内容。
- InfoHub 的版本发布说明：没找到公开的 release notes 页面，没有逐版改动记录。
- 「1.0.11 / 1.0.12 / 1.1.x」定向搜索：tavily、brave 都没有相关命中（不等于不存在）。
- 第三方项目许可证：FoxPodZ/ryuo-iv-screen 与 shorti1996/ryuo-display 是 GPL-3.0；Jacksongio/asus_ryuo_iv_linux 是 MIT；dragocz95/asus-ryuo-iv-linux 的 README 写 MIT，但仓库里没有 LICENSE 文件（GitHub 识别为无许可证），按「许可不明」对待。
