# ryuo：ROG 龙王 4 代（RYUO IV）水冷屏主机驱动

一个小而干净的 Python 包，从电脑这边通过 USB HID 驱动 RYUO IV 冷头上的 2240×1080 曲面屏：设定布局、媒体、颜色、6 个数据位，持续发送遥测（兼作心跳），以及自定义标签读数。

- Python 3.11+，核心零依赖；真要连设备才需要 `hidapi`。
- 默认 **dry-run**：命令行只打印十六进制帧，不打开任何设备。
- 按固件版本查能力表，默认 1.0.7 档；不认识的版本走保守档。
- 协议说明见 [`../docs/protocol.md`](../docs/protocol.md)，固件调研见 [`../docs/firmware.md`](../docs/firmware.md)。

## 用法

```bash
cd driver
python3 -m ryuo profiles                         # 看各固件档的能力
python3 -m ryuo --body config --media clip.mp4 --slots "CPU Temperature" "Date&Time" "Fan Speed Claude"
python3 -m ryuo telemetry --cpu-temp 45 --readout Claude=42
python3 -m ryuo brightness 85
python3 -m ryuo --send info                      # 真正发送；需要 hidapi 和 hidraw 权限
```

```python
from ryuo import RyuoDevice, HidTransport

dev = RyuoDevice(HidTransport(), firmware="1.0.7")
dev.set_layout("full")
dev.set_media(["clip.mp4"])
dev.set_slots(["CPU Temperature", "Date&Time", "Fan Speed Claude"])
dev.apply()                                    # POST config
with dev.heartbeat(lambda: ({"cpu": {"temperature": 48}}, {"Claude": 42})):
    dev.set_colors("#00FF9C", "#FFFFFF")       # POST preset，不重启媒体
    ...
dev.close()                                    # POST disconn
```

## 测试

```bash
python3 -m pytest
```

测试只用 `FakeTransport`，不碰硬件。金样本：协议文档里 `GET spec` 的完整字节；InfoHub 抓包里的 `POST config` 和 `POST all` 负载（我们的构造器逐字节复现）。

## 安全边界

- 驱动拒绝发送 `upgrade`、`transport`、`transported`、`turboPump`、`rotate`、`power`。
- 超过 1024 字节的帧直接报错，不会被屏悄悄丢掉。
- 找设备只按产品号 `1C76` + 接口 0，厂商号只认 `0B05` / `1C75`。
- 上传媒体不走 HID（屏上没有这个功能），请用 `adb push` 到 `/sdcard/pcMedia/`。

## 许可证

[MIT](../LICENSE)。本包是净室实现：只参考了公开协议笔记和抓包里的协议事实，代码和结构是自己写的。协议说明见 [`../docs/protocol.md`](../docs/protocol.md)。

---

## English

`ryuo` is a clean-room Python host driver for the curved 2240×1080 LCD on the ASUS ROG RYUO IV AIO cooler. It frames and validates requests (0x5A delimiters, byte stuffing, additive checksum, hard 1024-byte limit), builds typed payloads for `config`, `preset`, `all` telemetry, brightness, screen on/off, media list/delete and split mode, and keeps the session alive with a background heartbeat. Dry-run is the default in the CLI. Firmware differences are handled through a capability table (default profile 1.0.7; unknown versions are conservative). Protocol facts come from public reverse-engineering notes; no third-party code was copied. Licence: MIT.
