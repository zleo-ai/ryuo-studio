#!/usr/bin/env python3
"""OpenRGB SDK 的最小客户端（只用标准库）。

只做灯效同步用得到的几件事：列设备、切到实时推色（Direct）模式、推颜色、切硬件模式、
给 ARGB 接口设灯数（只改 OpenRGB 自己记的长度，不写硬件）。
**故意不实现** SAVEMODE（1102，把模式写进设备自己的存储）：这一步在一些华硕内存、
显卡的 ENE 灯控上出过写坏的事。这里所有操作断电或重启后都会回到设备自己存的模式。

报文格式：16 字节头（"ORGB"、设备号、报文号、正文长度，都是小端 u32）+ 正文。
协议版本跟服务端协商，最高只用到 4（0.9 到 1.1 都认），控制器数据按 4 版的格式解析。
"""
import select
import socket
import struct
from dataclasses import dataclass, field

MAGIC = b"ORGB"
HEADER = struct.Struct("<4sIII")
PROTOCOL = 4

REQUEST_CONTROLLER_COUNT = 0
REQUEST_CONTROLLER_DATA = 1
REQUEST_PROTOCOL_VERSION = 40
SET_CLIENT_NAME = 50
DEVICE_LIST_UPDATED = 100
RESIZEZONE = 1000
UPDATELEDS = 1050
UPDATEZONELEDS = 1051
SETCUSTOMMODE = 1100
UPDATEMODE = 1101

# OpenRGB 的设备类型编号（RGBController.h 里的 device_type）
TYPES = {0: "motherboard", 1: "dram", 2: "gpu", 3: "cooler", 4: "ledstrip", 5: "keyboard", 6: "mouse",
         7: "mousemat", 8: "headset", 9: "headset_stand", 10: "gamepad", 11: "light", 12: "speaker",
         13: "virtual", 14: "storage", 15: "case", 16: "microphone", 17: "accessory", 18: "keypad"}


class SDKError(OSError):
    pass


@dataclass
class Mode:
    name: str
    value: int
    flags: int
    speed_min: int
    speed_max: int
    brightness_min: int
    brightness_max: int
    colors_min: int
    colors_max: int
    speed: int
    brightness: int
    direction: int
    color_mode: int
    colors: list = field(default_factory=list)


@dataclass
class Zone:
    name: str
    type: int
    leds_min: int
    leds_max: int
    leds_count: int


@dataclass
class Controller:
    index: int
    type: int
    name: str
    vendor: str
    description: str
    version: str
    serial: str
    location: str
    active_mode: int
    modes: list
    zones: list
    leds: list
    colors: list

    @property
    def kind(self):
        return TYPES.get(self.type, f"type{self.type}")

    def mode_index(self, *names):
        """按名字找模式（不分大小写），找不到返回 None。"""
        want = [n.lower() for n in names]
        for i, m in enumerate(self.modes):
            if m.name.lower() in want:
                return i
        return None


class _Reader:
    def __init__(self, buf):
        self.b, self.i = buf, 0

    def take(self, fmt):
        s = struct.Struct("<" + fmt)
        v = s.unpack_from(self.b, self.i)
        self.i += s.size
        return v if len(v) > 1 else v[0]

    def string(self):
        n = self.take("H")
        raw = self.b[self.i:self.i + n]
        self.i += n
        return raw.rstrip(b"\0").decode("utf-8", "replace")

    def colors(self):
        n = self.take("H")
        out = [tuple(self.b[self.i + 4 * k:self.i + 4 * k + 3]) for k in range(n)]
        self.i += 4 * n
        return out


def parse_controller(index, body, proto=PROTOCOL):
    r = _Reader(body)
    r.take("I")  # 整段长度
    ctype = r.take("i")
    name = r.string()
    vendor = r.string() if proto >= 1 else ""
    description, version, serial, location = r.string(), r.string(), r.string(), r.string()
    num_modes = r.take("H")
    active = r.take("i")
    modes = []
    for _ in range(num_modes):
        mname = r.string()
        value, flags, smin, smax = r.take("iIII")
        bmin, bmax = r.take("II") if proto >= 3 else (0, 0)
        cmin, cmax, speed = r.take("III")
        bright = r.take("I") if proto >= 3 else 0
        direction, color_mode = r.take("II")
        modes.append(Mode(mname, value, flags, smin, smax, bmin, bmax, cmin, cmax, speed, bright, direction,
                          color_mode, r.colors()))
    zones = []
    for _ in range(r.take("H")):
        zname = r.string()
        ztype, lmin, lmax, lcount = r.take("iIII")
        mlen = r.take("H")
        r.i += mlen  # 键盘之类的矩阵排布，这里用不到
        if proto >= 4:
            for _ in range(r.take("H")):
                r.string()
                r.take("iII")
        zones.append(Zone(zname, ztype, lmin, lmax, lcount))
    leds = []
    for _ in range(r.take("H")):
        leds.append(r.string())
        r.take("I")
    colors = r.colors()
    return Controller(index, ctype, name, vendor, description, version, serial, location, active, modes, zones,
                      leds, colors)


def _color_bytes(colors):
    return b"".join(bytes((int(r) & 255, int(g) & 255, int(b) & 255, 0)) for r, g, b in colors)


def pack_updateleds(colors):
    data = struct.pack("<H", len(colors)) + _color_bytes(colors)
    return struct.pack("<I", 4 + len(data)) + data


def pack_mode(idx, m, proto=PROTOCOL):
    name = m.name.encode() + b"\0"
    d = struct.pack("<i", idx) + struct.pack("<H", len(name)) + name
    d += struct.pack("<iIII", m.value, m.flags, m.speed_min, m.speed_max)
    if proto >= 3:
        d += struct.pack("<II", m.brightness_min, m.brightness_max)
    d += struct.pack("<III", m.colors_min, m.colors_max, m.speed)
    if proto >= 3:
        d += struct.pack("<I", m.brightness)
    d += struct.pack("<II", m.direction, m.color_mode)
    d += struct.pack("<H", len(m.colors)) + _color_bytes(m.colors)
    return struct.pack("<I", 4 + len(d)) + d


class Client:
    def __init__(self, host="127.0.0.1", port=6742, name="ryuo-studio lights", timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.list_changed = False
        self._send(0, SET_CLIENT_NAME, name.encode() + b"\0")
        self._send(0, REQUEST_PROTOCOL_VERSION, struct.pack("<I", PROTOCOL))
        self.proto = min(PROTOCOL, struct.unpack("<I", self._wait(REQUEST_PROTOCOL_VERSION))[0])

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, dev, pid, body=b""):
        self.sock.sendall(HEADER.pack(MAGIC, dev, pid, len(body)) + body)

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise SDKError("OpenRGB 断开了连接")
            buf += chunk
        return buf

    def _packet(self):
        magic, dev, pid, size = HEADER.unpack(self._exact(HEADER.size))
        if magic != MAGIC:
            raise SDKError(f"报文头不对：{magic!r}")
        return dev, pid, self._exact(size) if size else b""

    def _wait(self, pid):
        while True:
            _, got, body = self._packet()
            if got == pid:
                return body
            if got == DEVICE_LIST_UPDATED:
                self.list_changed = True

    def drain(self):
        """把服务端主动发来的通知读掉（设备增减时会发），不阻塞。"""
        while select.select([self.sock], [], [], 0)[0]:
            _, pid, _ = self._packet()
            if pid == DEVICE_LIST_UPDATED:
                self.list_changed = True

    def controllers(self):
        self._send(0, REQUEST_CONTROLLER_COUNT)
        count = struct.unpack("<I", self._wait(REQUEST_CONTROLLER_COUNT))[0]
        out = []
        for i in range(count):
            self._send(i, REQUEST_CONTROLLER_DATA, struct.pack("<I", self.proto))
            out.append(parse_controller(i, self._wait(REQUEST_CONTROLLER_DATA), self.proto))
        self.list_changed = False
        return out

    def controller(self, index):
        self._send(index, REQUEST_CONTROLLER_DATA, struct.pack("<I", self.proto))
        return parse_controller(index, self._wait(REQUEST_CONTROLLER_DATA), self.proto)

    def set_custom_mode(self, index):
        """切到实时推色模式（OpenRGB 会替每种设备挑 Direct / Custom）。不写进设备存储。"""
        self._send(index, SETCUSTOMMODE)

    def update_mode(self, index, mode_idx, mode):
        """切到某个硬件模式（比如 Off、Static）。只是当下生效，不写进设备存储。"""
        self._send(index, UPDATEMODE, pack_mode(mode_idx, mode, self.proto))

    def resize_zone(self, index, zone_idx, leds):
        """给可变长的区（主板 ARGB 接口）设灯数。OpenRGB 只把它记在自己的配置里。"""
        self._send(index, RESIZEZONE, struct.pack("<ii", zone_idx, leds))

    def update_leds(self, index, colors):
        self._send(index, UPDATELEDS, pack_updateleds(colors))
