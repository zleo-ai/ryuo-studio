# RYUO IV 屏幕主机协议（我们自己的整理）

这是我们驱动实现所依据的协议事实，按自己的理解重新写，不是翻译。事实来源主要是 FoxPodZ 的逆向文档和抓包（[PROTOCOL.md](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md)、[captures/](https://github.com/FoxPodZ/ryuo-iv-screen/tree/main/captures)）。该文档自己声明：协议本身不受版权保护，可以用任何语言、任何许可证重新实现；我们没有抄它的代码。

每条后面标「验证于」：
- **1.0.10** / **1.0.7**：有人在该版本真机上看到过；
- **源码**：只从 APK 反编译读到，没有真机证据；
- **我们**：本项目自己在真机上确认过（目前一项都没有，我们没碰过设备）。

## 1. 传输

- 屏是 USB HID 设备，主机侧找产品号 `1C76`、接口 0。厂商号随固件变：1.0.3/1.0.10 是 `0B05`，1.0.7 是 `1C75`。只按产品号找，厂商号当白名单。（验证于 1.0.7、1.0.10）
- 每次写一个报告：1 字节报告 ID（`0x00`）+ 1024 字节数据，数据不足用 `0x00` 补齐。（1.0.10）
- **一帧必须装进一个报告**（含首尾分隔符共 ≤ 1024 字节）。超过的帧会被屏直接丢弃，没有回复、没有报错。我们的 `encode_frame` 在超限时抛 `FrameTooLarge`，不会发出去。（1.0.10 实测 1024 能收、1025 不收）
- 屏回给主机的帧可以跨多个报告，主机要拼接。（1.0.10）
- 节奏：屏大约每秒收 11 帧，回复大约每秒 8.5 条。持续超过每秒 8 条，回复会越积越多。驱动默认限速每秒 8 条。（1.0.10）
- 所有打开该设备的程序都会收到每一条回复，所以按 `AckNumber` 认领自己的回复。（1.0.10，Windows）

## 2. 帧格式

```
0x5A | 转义( LEN[2] BODY[n] CKSUM[1] ) | 0x5A
```

- `LEN`：大端两字节，等于 `n + 5`，也就是未转义时整帧的长度。
- `CKSUM`：`LEN` 两字节和 `BODY` 所有字节相加，取低 8 位。不是 CRC。
- 转义只作用于中间部分：`5A → 5B 01`，`5B → 5B 02`。首尾两个 `5A` 不转义。长度字节、校验字节本身也可能需要转义（例如 `n = 85` 时 `LEN` 低字节正好是 `0x5A`）。
- 1024 的上限按**转义之后**的长度算。
- 官方文档里的 `GET spec`（序号 1）完整字节在我们的测试里当作金样本，校验和是 `D0`。（1.0.10）
- 同一报告里放两帧屏能都处理，放三帧就都不处理。驱动只一报告一帧。（1.0.10）

## 3. 报文封装

请求体是仿 HTTP 的文本，`\r\n` 换行：

```
POST config 1
ContentType=json
ContentLength=<负载字节数>
SeqNumber=<序号>

<JSON 负载>
```

- 方法有 `GET` / `POST` / `DELETE` / `STATE`，我们只用 `GET` 和 `POST`。
- 起始行必须三段（`方法 资源 1`），头至少两行。少了会让屏上的串口服务崩溃、USB 掉线，偶尔还会让屏内核崩溃重启。驱动永远发完整的三段起始行 + `ContentType` + `ContentLength` + `SeqNumber`。（1.0.10）
- 屏只读 `SeqNumber`，回复里 `AckNumber = SeqNumber + 1`。
- 回复格式 `1 200\r\n键=值...\r\n\r\n负载`。回复里的 `ContentLength` 是字符数不是字节数，解析时按空行切，不信这个数。（1.0.10）
- 屏对所有格式正确的帧都回 `1 200`，包括它根本不处理的资源。**200 只说明帧解析成功，不说明功能生效**，要看屏幕本身。（1.0.10）
- JSON 用紧凑格式、按官方软件的字段顺序输出；测试里我们生成的 `config` 负载和抓包里 InfoHub 的那条逐字节一致。

## 4. 会话与心跳

- 连上后先发一次 `POST config`（第 5 节），然后**每秒发一次 `POST all`**（第 8 节）。`POST all` 既是数据也是心跳。（1.0.10；1.0.7 上的现象也一致）
- 大约 10 秒收不到 `POST all`，屏回到待机：组件消失、变暗、放 `standby.mp4`，像从没连过。FoxPodZ 写 10 秒，dragocz95 写 5 秒。驱动用 1 秒间隔，并拒绝 ≥ 超时一半的间隔。（10 秒说法出自 1.0.10 环境；**1.0.7 待真机确认**）
- 心跳要放在后台线程，不要和其他操作串行。读取传感器出错时继续发上一次的值，别让会话断掉。驱动的 `Heartbeat` 这样做。
- 心跳不要等回复（回复会排队），只把已经到的回复收走。
- 退出时发 `POST disconn`，屏立刻回待机而不是停在旧画面 10 秒。（1.0.10）
- InfoHub 在 `config` 之后会先发一帧全零的遥测，所以第一帧看着是空的不算异常。（抓包）

## 5. `POST config`（屏端第 105 号处理分支）

一次性设定整个会话。会关掉主界面、拆掉播放器、重建一切，所以**每发一次媒体就从头播**。发完给它约 1.6 秒稳定再发别的，驱动自动等。（1.0.10，源码）

```jsonc
{
  "temperature": "Celsius",           // 或 "Fahrenheit"，只影响显示
  "waterBlockScreen": {
    "enable": true,
    "displayInSleep": true,
    "brightness": 85,                 // 与独立亮度命令同一标尺 0–102
    "id": { /* ScreenConfig，见下 */ }
  },
  "spec": {"cpu": "CPU 型号", "gpu": "GPU 型号"}
}
```

ScreenConfig（全屏）：

```jsonc
{
  "id": "Customization",
  "screenMode": "Full Screen",
  "playMode": "Single",               // 只认 Single / Random / Cycle，区分大小写
  "media": ["clip.mp4"],              // 只写文件名
  "settings": {"titleColor": "#E5252B", "contentColor": "#FFFFFF",
               "filter": {"value": null, "opacity": 100}, "badges": []},
  "sysinfoDisplay": ["CPU Temperature", "GPU Temperature", "", "", "", ""],
  "timeZone": "Europe/Berlin"
}
```

- `playMode` 写错（比如 `single`、`loop`）会黑屏且无任何报错；`media` 为空时只能用 `Single`。驱动在构造时就拒绝。（源码 + 1.0.10/1.0.7 现象）
- 颜色 `#RRGGBB` 或 `#AARRGGBB`。`settings` 里真正生效的只有 `titleColor`、`contentColor`、`filter`；`badges` 等是死字段。（源码 + 1.0.7）
- `filter.value` 任意非空字符串都会叠加同一种动画，`null` 或空串不叠加。（1.0.7）
- `ratio` 字段存在，效果证据很弱，驱动不暴露。（1.0.7，证据弱）
- 帧大小最容易在这里超限：播放列表十几个长文件名就会超 1024。驱动在发送前就报错。

## 6. `POST preset`（第 114 号处理分支）

只重画组件和颜色，**不重启媒体**。改颜色、换组件都用它，可以反复发（注意总速率每秒 8 条以内）。

```jsonc
{"id": "Customization", "sysinfoDisplay": [...6 项...], "settings": {...}}
```

分屏时 `settings` 是两项列表。（1.0.7 用过，1.0.10 验证）驱动里 `apply()` 之后再调 `set_colors` / `set_slots` 自动走 preset。

## 7. 组件位（sysinfo slots）

- 固定 6 个位，数组下标就是屏上位置，`""` 表示空位。没有坐标可设。（抓包 + 1.0.10）
- 可用的 16 个内置项（1.0.10 全部看到能显示）：
  `CPU Load`、`CPU Usage`、`CPU Temperature`、`CPU Speed Average`、`CPU Voltage`、`GPU Load`、`GPU Usage`、`GPU Temperature`、`GPU Speed`、`GPU Power`、`GPU Voltage`、`Memory Utilization`、`Memory Frequency`、`Hard Disk Temperature`、`Motherboard Temperature`、`Date&Time`。
- 已知怪处：`GPU Usage` 其实显示 `gpu.load`；`GPU Power` 的单位被画成 °C。（1.0.10）
- 屏上标签是缩写，`CPU Temperature` 和 `CPU Voltage` 都显示「CPU」，靠单位区分。（1.0.10）
- **数值不会被清空**：某个位的数据源这次没发，屏就一直显示上一次的数，看着像正常。判断一个位是不是活的，只能看数在变。（1.0.7）
- 也可以单独发 `POST sysinfoDisplay {"items": [...]}`。（1.0.10）

## 8. `POST all` 遥测与自定义标签读数

- 字段全量照发，没有值就填 0，不要省略键。驱动的 `telemetry_skeleton()` 给出完整结构。（抓包）
- 单位：内存 MB、硬盘 GB、频率 MHz、网速 KB/s、时间戳毫秒、温度永远摄氏（屏按 `temperature` 设置换算显示）。
- 回复是 `1 200` 加 `{"status": {"fanLCD": ""}}`。（1.0.10）
- **自定义标签读数**：`fans[]` 里每个 `{"onBoard": true, "name": "<标签>", "value": <整数>}`，对应组件项 `"Fan Speed <标签>"`。屏上标签显示为 `<标签>`，数值后单位固定是 `RPM`。标签没有任何校验，随便起。（1.0.10 实测 `Coolant`、`NAS Bay 3`、`Unread`；1.0.7 只看到标签生效，**数值待真机确认**）
- 风扇名大小写、空格都要和组件项一字不差（官方自己就有 `Chassis 5` 和 `Chassis4` 两种写法）。
- 一帧遥测全量约 560 字节，每加一个读数约 45 字节，所以大概能放 9 个读数再加满其他字段，驱动超限会报错。

## 9. 媒体

- `media[]` 只写文件名。名字以 `RYUO` 开头的先去 `pcMediaPreset/`（ASUS 预设）找，其余先去 `pcMedia/` 找，找不到再去另一个。查找用的是「包含」匹配，名字互为前缀可能匹配错。**自己的文件别用 `RYUO` 开头**，驱动会警告。（源码）
- HID 上**没有**上传。`transport` 是假成功的桩，后面跟原始数据会让服务崩溃。放文件只能 `adb push` 到 `/sdcard/pcMedia/`。驱动禁止发 `transport` / `transported`。（1.0.10）
- 列出：`POST conn` 的回复里 `media.preset` 和 `media.custom` 两个列表，另带 `version.firmware`、`version.hardware`、`product`、`sn`（序列号，驱动的 CLI 会隐藏）。`conn` 回得比别的慢（约 200 毫秒）。（1.0.10）
- 删除：`POST mediaDelete {"include": ["a.mp4"]}` 只删 `pcMedia/` 里这些文件；文件不存在也回 200。另有 `exclude` 形式（删掉除列表外的全部），太危险，驱动不提供。（include 验证于 1.0.10；exclude 仅源码）
- 按扩展名分派：`.mp4` 走视频播放器、播完换下一个；`.gif` 播一遍换下一个；**其他一律当静态图，固定 7 秒换下一个**。缺失的文件直接跳过。（源码 + 1.0.10）
- 静态图在全屏和分屏区里都是 **FIT_XY**：拉伸铺满、不保持比例、不留黑边。比例不对就会变形，不会提示。动画 WebP 视图是居中裁剪。（布局资源 + 1.0.10）
- 视频编码：H.264 解码器上限约 1920 宽，2240×1080 H.264 会黑屏不报错；2240×1080 用 HEVC（`hvc1` 标签）能播。（1.0.10；**1.0.7 未验证**，驱动的 1.0.7 档默认推荐 1920×926 H.264）

## 10. 亮度、单位、开关

- `POST brightness {"value": n}`，n 取 0–102。100 只有约 98%，102 才到满亮，再大没用。InfoHub 默认 85、最多给到 100。（1.0.7 与 1.0.10 都测过，数值一致）驱动的 1.0.7/1.0.10 档上限 102，未知固件上限 100。
- `POST temperature {"value": "Celsius"|"Fahrenheit"}`。（1.0.10）
- 屏幕开关：`POST waterBlockScreen {"enable": true|false}`。（真机测过，原文未写版本）
- `POST displayInSleep {"enable": true}` 会让屏放 `Screensaver.mp4` 并停止显示数值，直到关掉。（1.0.7）
- `power` 的 `shutdown` 会把屏调暗，但恢复方法不明，驱动禁止发送。（1.0.7）

## 11. 分屏

- `screenMode` 写 `"Screen Splitting"`（实际上只要不是 `"Full Screen"` 都走分屏）。（抓包）
- 分屏时 `playMode`、`media`、`settings` 变成两项列表（每区一项）；`sysinfoDisplay` **仍是 6 项平铺**，每区 3 个。（抓包）【推测】前 3 个属于区 1、后 3 个属于区 2，没找到明确证据。
- 几何：区 1（主面）**1606×1080**，区 2（弯过去的窄条）**634×1080**，区 2 是写死的 634 像素，区 1 是剩下的。（1.0.10 截屏量过）InfoHub 裁剪工具按 1600/640 裁，差 6 像素，因为 FIT_XY 拉伸所以看不出来。
- 两区素材都在 H.264 能力内，用哪种编码都行。
- 待机视频只有区 1 有；进待机时区 2 隐藏。

## 12. 防烧屏

- 屏是 OLED。每个区有一个 5 分钟计时器；计时到了之后，**下一次媒体切换**时，该区组件隐藏 2 秒，播放防烧屏短片（全屏 `Screensaver.mp4`；分屏左 `Screensaver_left.mp4`、右 `Screensaver_right.mp4`），2 秒后恢复。没有命令能关掉。（源码 + 现象）
- 检查只在媒体切换时做，所以片子越长保护越稀：静态图 7 秒切一次，约 5 分钟保护一次；30 分钟长片约 30 分钟才保护一次。想护屏就用短片或图片轮播。
- 分屏两区各自计时，会慢慢错开。

## 13. 驱动禁止发送的资源

`upgrade`（触发固件更新）、`transport` / `transported`（上传桩，带 `firmware` 类型会触发本地更新检查）、`turboPump`（板上没有对应驱动）、`rotate`（写持久属性但不转屏）、`power`（调暗后恢复方法不明）。`Request` 构造时直接抛错。
