"""Typed builders for the requests the host sends to the screen.

A request body is HTTP-shaped text followed by a JSON payload::

    <METHOD> <resource> 1\r\n
    ContentType=json\r\n
    ContentLength=<n>\r\n
    SeqNumber=<seq>\r\n
    \r\n
    <json>

The start line must have all three parts and there must be at least two
header lines: a malformed request can crash the device-side service (and,
rarely, the device kernel). :class:`Request` always emits the full form.

Payload shapes follow what the official host software was observed to send.
JSON is serialised compactly with keys in insertion order, which matches the
captured traffic byte-for-byte for the shapes covered by the tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

SCREEN_FULL = "Full Screen"
SCREEN_SPLIT = "Screen Splitting"

PLAY_MODES = ("Single", "Random", "Cycle")  # case-sensitive; anything else = black screen

SLOT_COUNT = 6
SPLIT_ZONE_WIDTHS = (1606, 634)  # zone 1 (main face), zone 2 (strip past the bend)
PANEL_SIZE = (2240, 1080)

FAN_PREFIX = "Fan Speed "

SLOT_ITEMS = frozenset(
    {
        "CPU Load",
        "CPU Usage",
        "CPU Temperature",
        "CPU Speed Average",
        "CPU Voltage",
        "GPU Load",
        "GPU Usage",
        "GPU Temperature",
        "GPU Speed",
        "GPU Power",
        "GPU Voltage",
        "Memory Utilization",
        "Memory Frequency",
        "Hard Disk Temperature",
        "Motherboard Temperature",
        "Date&Time",
    }
)
"""Built-in slot items. ``""`` means an empty slot; ``"Fan Speed <name>"`` is dynamic."""

TEMPERATURE_UNITS = ("Celsius", "Fahrenheit")

BRIGHTNESS_MAX = 102  # 100 is ~98 % of the backlight; 102 saturates

# Resources this driver refuses to send, whatever the caller asks.
FORBIDDEN_RESOURCES = frozenset(
    {
        "upgrade",  # starts a firmware update
        "transport",  # upload stub; raw data after it crashes the service
        "transported",  # with type=firmware starts a local update check
        "turboPump",  # writes to a pump driver that is not on this board
        "rotate",  # writes a persistent property, does nothing visible
        "power",  # "shutdown" dims the panel; no known restore event
    }
)

_COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")

Method = Literal["GET", "POST", "DELETE", "STATE"]


class MessageError(ValueError):
    """A payload would be rejected or misrendered by the device."""


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Request:
    method: Method
    resource: str
    payload: Any = None  # None -> empty payload; str -> sent verbatim; else JSON

    def __post_init__(self) -> None:
        if self.resource in FORBIDDEN_RESOURCES:
            raise MessageError(f"resource {self.resource!r} is blocked by this driver")
        if not self.resource or any(c.isspace() for c in self.resource):
            raise MessageError("resource must be a single non-empty token")

    def payload_bytes(self) -> bytes:
        if self.payload is None:
            return b""
        if isinstance(self.payload, str):
            return self.payload.encode("utf-8")
        return _dumps(self.payload).encode("utf-8")

    def body(self, seq: int) -> bytes:
        payload = self.payload_bytes()
        head = (
            f"{self.method} {self.resource} 1\r\n"
            f"ContentType=json\r\n"
            f"ContentLength={len(payload)}\r\n"
            f"SeqNumber={seq}\r\n"
            "\r\n"
        )
        return head.encode("ascii") + payload


@dataclass
class Response:
    version: str
    status: int
    headers: dict[str, str]
    payload: str

    @property
    def ack(self) -> int | None:
        v = self.headers.get("AckNumber")
        return int(v) if v is not None and v.lstrip("-").isdigit() else None

    def json(self) -> Any:
        return json.loads(self.payload) if self.payload.strip() else None


def parse_response(body: bytes) -> Response:
    """Parse a reply body. Splits at the blank line; ``ContentLength`` is not
    trusted (the device counts characters, not bytes)."""
    text = body.decode("utf-8", errors="replace")
    head, sep, payload = text.partition("\r\n\r\n")
    if not sep:
        raise MessageError("reply has no header terminator")
    lines = head.split("\r\n")
    start = lines[0].split()
    if len(start) < 2 or not start[1].isdigit():
        raise MessageError(f"bad status line {lines[0]!r}")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        k, _, v = line.partition("=")
        headers[k] = v
    return Response(start[0], int(start[1]), headers, payload)


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def check_color(c: str) -> str:
    if not _COLOR_RE.match(c):
        raise MessageError(f"colour {c!r} must be #RRGGBB or #AARRGGBB")
    return c


def check_play_mode(m: str) -> str:
    if m not in PLAY_MODES:
        raise MessageError(f"playMode {m!r} must be one of {PLAY_MODES} (case-sensitive)")
    return m


def check_media_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise MessageError(f"media name {name!r} must be a bare file name")
    return name


def check_slot(item: str) -> str:
    if item == "" or item in SLOT_ITEMS:
        return item
    if item.startswith(FAN_PREFIX) and len(item) > len(FAN_PREFIX):
        return item
    raise MessageError(f"unknown slot item {item!r}")


def fan_item(label: str) -> str:
    """Slot item string for a custom labelled readout."""
    if not label or label != label.strip():
        raise MessageError("readout label must be non-empty without outer spaces")
    return FAN_PREFIX + label


def normalise_slots(slots: list[str] | tuple[str, ...]) -> list[str]:
    items = list(slots)
    if len(items) > SLOT_COUNT:
        raise MessageError(f"at most {SLOT_COUNT} slots, got {len(items)}")
    items += [""] * (SLOT_COUNT - len(items))
    return [check_slot(i) for i in items]


# --------------------------------------------------------------------------
# Payload pieces
# --------------------------------------------------------------------------


@dataclass
class ZoneSettings:
    title_color: str = "#E5252B"
    content_color: str = "#FFFFFF"
    overlay: bool = False  # filter.value: any non-empty string draws the one overlay
    opacity: int = 100

    def to_json(self) -> dict[str, Any]:
        return {
            "titleColor": check_color(self.title_color),
            "contentColor": check_color(self.content_color),
            "filter": {"value": "on" if self.overlay else None, "opacity": self.opacity},
            "badges": [],
        }


@dataclass
class Zone:
    media: list[str] = field(default_factory=list)
    play_mode: str = "Single"
    settings: ZoneSettings = field(default_factory=ZoneSettings)

    def validate(self) -> None:
        check_play_mode(self.play_mode)
        for m in self.media:
            check_media_name(m)
        if not self.media and self.play_mode != "Single":
            raise MessageError("an empty media list needs playMode 'Single'")


@dataclass
class ScreenConfig:
    """The ``ScreenConfig`` object shared by ``config`` (case 105) and ``preset``."""

    zones: list[Zone] = field(default_factory=lambda: [Zone()])
    slots: list[str] = field(default_factory=lambda: [""] * SLOT_COUNT)
    time_zone: str | None = "UTC"
    config_id: str = "Customization"

    @property
    def split(self) -> bool:
        return len(self.zones) == 2

    def validate(self) -> None:
        if len(self.zones) not in (1, 2):
            raise MessageError("one zone (full screen) or two zones (split)")
        for z in self.zones:
            z.validate()
        self.slots = normalise_slots(self.slots)

    def to_json(self) -> dict[str, Any]:
        self.validate()
        out: dict[str, Any] = {"id": self.config_id}
        if self.split:
            out["screenMode"] = SCREEN_SPLIT
            out["playMode"] = [z.play_mode for z in self.zones]
            out["media"] = [list(z.media) for z in self.zones]
            out["settings"] = [z.settings.to_json() for z in self.zones]
        else:
            z = self.zones[0]
            out["screenMode"] = SCREEN_FULL
            out["playMode"] = z.play_mode
            out["media"] = list(z.media)
            out["settings"] = z.settings.to_json()
        out["sysinfoDisplay"] = list(self.slots)  # stays flat in split mode
        if self.time_zone is not None:
            out["timeZone"] = self.time_zone
        return out

    def preset_json(self) -> dict[str, Any]:
        full = self.to_json()
        return {
            "id": full["id"],
            "sysinfoDisplay": full["sysinfoDisplay"],
            "settings": full["settings"],
        }


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def check_brightness(value: int, maximum: int = BRIGHTNESS_MAX) -> int:
    if not isinstance(value, int) or not 0 <= value <= maximum:
        raise MessageError(f"brightness must be an int in 0..{maximum}")
    return value


def config(
    screen: ScreenConfig,
    *,
    brightness: int = 85,
    temperature_unit: str = "Celsius",
    cpu_name: str = "",
    gpu_name: str = "",
    enable: bool = True,
    display_in_sleep: bool = True,
    brightness_max: int = BRIGHTNESS_MAX,
) -> Request:
    """``POST config``: full session setup. Restarts media; wait ~1.6 s after."""
    if temperature_unit not in TEMPERATURE_UNITS:
        raise MessageError(f"temperature unit must be one of {TEMPERATURE_UNITS}")
    payload = {
        "temperature": temperature_unit,
        "waterBlockScreen": {
            "enable": enable,
            "displayInSleep": display_in_sleep,
            "brightness": check_brightness(brightness, brightness_max),
            "id": screen.to_json(),
        },
        "spec": {"cpu": cpu_name, "gpu": gpu_name},
    }
    return Request("POST", "config", payload)


def preset(screen: ScreenConfig) -> Request:
    """``POST preset``: repaint slots and colours without restarting media."""
    return Request("POST", "preset", screen.preset_json())


def sysinfo_display(slots: list[str]) -> Request:
    return Request("POST", "sysinfoDisplay", {"items": normalise_slots(slots)})


def brightness(value: int, maximum: int = BRIGHTNESS_MAX) -> Request:
    return Request("POST", "brightness", {"value": check_brightness(value, maximum)})


def temperature_unit(unit: str) -> Request:
    if unit not in TEMPERATURE_UNITS:
        raise MessageError(f"temperature unit must be one of {TEMPERATURE_UNITS}")
    return Request("POST", "temperature", {"value": unit})


def screen_enable(on: bool) -> Request:
    """``POST waterBlockScreen``: panel on/off."""
    return Request("POST", "waterBlockScreen", {"enable": bool(on)})


def display_in_sleep(on: bool) -> Request:
    return Request("POST", "displayInSleep", {"enable": bool(on)})


def spec(cpu_name: str, gpu_name: str) -> Request:
    return Request("POST", "spec", {"cpu": cpu_name, "gpu": gpu_name})


def conn() -> Request:
    """``POST conn``: reply carries versions, serial and both media listings."""
    return Request("POST", "conn")


def get_spec() -> Request:
    """``GET spec``: harmless liveness probe, replies with an empty body."""
    return Request("GET", "spec")


def disconn() -> Request:
    return Request("POST", "disconn")


def media_delete(names: list[str]) -> Request:
    """Delete exactly ``names`` from ``pcMedia/``. The ``exclude`` form
    (delete everything else) is intentionally not exposed."""
    if not names:
        raise MessageError("refusing an empty delete list")
    return Request("POST", "mediaDelete", {"include": [check_media_name(n) for n in names]})


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------


def telemetry_skeleton() -> dict[str, Any]:
    """All keys the official host sends, zeroed. Units: memory MB, disk GB,
    clocks MHz, network KB/s, temperatures always Celsius, timestamp ms."""
    return {
        "network": {"upload": 0, "download": 0},
        "memory": {"total": 0, "used": 0, "load": 0, "temperature": 0, "speed": 0},
        "cpu": {
            "load": 0,
            "temperature": 0,
            "temperaturePackage": 0,
            "speedAverage": 0,
            "power": 0,
            "voltage": 0,
            "usage": 0,
        },
        "gpu": {
            "hasDedicated": True,
            "load": 0,
            "temperature": 0,
            "fan": 0,
            "speed": 0,
            "power": 0,
            "voltage": 0,
        },
        "disk": {
            "total": 0,
            "used": 0,
            "load": 0,
            "activity": 0,
            "temperature": 0,
            "readSpeed": 0,
            "writeSpeed": 0,
        },
        "fans": [],
        "motherboard": {"temperature": 0, "chipsetTemperature": 0},
        "timestamp": 0,
    }


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def telemetry(
    values: dict[str, Any] | None = None,
    *,
    readouts: dict[str, int] | None = None,
    timestamp_ms: int = 0,
) -> Request:
    """``POST all``: telemetry and session keepalive.

    ``readouts`` maps a free-form label to an integer; each becomes a
    ``fans[]`` entry, displayable via the slot item ``"Fan Speed <label>"``
    (unit always shown as RPM).
    """
    blob = telemetry_skeleton()
    if values:
        unknown = set(values) - set(blob)
        if unknown:
            raise MessageError(f"unknown telemetry groups: {sorted(unknown)}")
        _merge(blob, values)
    fans = list(blob["fans"])
    for label, value in (readouts or {}).items():
        fan_item(label)
        if not isinstance(value, int):
            raise MessageError(f"readout {label!r} must be an int")
        fans.append({"onBoard": True, "name": label, "value": value})
    blob["fans"] = fans
    blob["timestamp"] = int(timestamp_ms)
    return Request("POST", "all", blob)
