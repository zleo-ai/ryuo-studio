"""Per-firmware capability table.

Each capability carries an evidence level so behaviour can be conservative
where nothing has been checked on hardware:

* ``"verified"``  - observed on a real panel running that firmware
* ``"partial"``   - part of the behaviour observed, the rest inferred
* ``"inferred"``  - expected from the same launcher build, not observed
* ``"unknown"``   - no evidence either way

Sources are summarised in ``docs/firmware.md``. The default profile is
1.0.7, the firmware on the maintainer's unit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Evidence = Literal["verified", "partial", "inferred", "unknown"]


@dataclass(frozen=True)
class FirmwareProfile:
    version: str
    vendor_id: int | None
    # capabilities -----------------------------------------------------
    native_hevc: Evidence  # 2240x1080 HEVC (hvc1) plays full-screen
    fan_label_readout: Evidence  # "Fan Speed <label>" custom readouts
    brightness_102: Evidence  # brightness 101-102 reaches 100 % backlight
    split_mode: Evidence
    preset_live_update: Evidence
    media_list_delete: Evidence  # conn listing + mediaDelete include
    # timings / limits -------------------------------------------------
    standby_timeout_s: float = 10.0
    heartbeat_interval_s: float = 1.0
    config_settle_s: float = 1.6
    max_requests_per_s: float = 8.0
    still_image_seconds: float = 7.0
    h264_max_width: int = 1920
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def brightness_max(self) -> int:
        return 102 if self.brightness_102 == "verified" else 100

    @property
    def known(self) -> bool:
        return self.version != "unknown"

    def recommended_video(self, split_zone: int | None = None) -> dict[str, object]:
        """Encoding target that is safe on this firmware."""
        if split_zone is not None:
            w = (1606, 634)[split_zone]
            return {"codec": "h264", "width": w, "height": 1080}
        if self.native_hevc == "verified":
            return {"codec": "hevc", "tag": "hvc1", "width": 2240, "height": 1080}
        # conservative: what ASUS itself ships, inside the H.264 decoder limit
        return {"codec": "h264", "width": 1920, "height": 926}


PROFILES: dict[str, FirmwareProfile] = {
    "1.0.3": FirmwareProfile(
        version="1.0.3",
        vendor_id=0x0B05,
        native_hevc="unknown",
        fan_label_readout="inferred",
        brightness_102="inferred",
        split_mode="inferred",
        preset_live_update="inferred",
        media_list_delete="inferred",
        notes=("Protocol derived from this package's APKs; launcher reported identical to 1.0.7/1.0.10.",),
    ),
    "1.0.7": FirmwareProfile(
        version="1.0.7",
        vendor_id=0x1C75,
        native_hevc="unknown",
        fan_label_readout="partial",
        brightness_102="verified",
        split_mode="inferred",
        preset_live_update="verified",
        media_list_delete="inferred",
        notes=(
            "Shipped under the ODM vendor id 1C75.",
            "Fan label path seen (label drawn) but value path not cleanly confirmed.",
            "Native HEVC never tested on 1.0.7; default to H.264 <=1920 wide until checked.",
        ),
    ),
    "1.0.9": FirmwareProfile(
        version="1.0.9",
        vendor_id=None,
        native_hevc="unknown",
        fan_label_readout="inferred",
        brightness_102="inferred",
        split_mode="inferred",
        preset_live_update="inferred",
        media_list_delete="inferred",
        notes=("Exists per user reports; nobody has tested the protocol on it.",),
    ),
    "1.0.10": FirmwareProfile(
        version="1.0.10",
        vendor_id=0x0B05,
        native_hevc="verified",
        fan_label_readout="verified",
        brightness_102="verified",
        split_mode="verified",
        preset_live_update="verified",
        media_list_delete="verified",
        notes=("Product string 'ROG RYUO IV Standard'.",),
    ),
}

UNKNOWN = FirmwareProfile(
    version="unknown",
    vendor_id=None,
    native_hevc="unknown",
    fan_label_readout="unknown",
    brightness_102="unknown",
    split_mode="unknown",
    preset_live_update="unknown",
    media_list_delete="unknown",
    notes=("Unrecognised firmware: conservative defaults.",),
)

DEFAULT_VERSION = "1.0.7"

_VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def normalise_version(text: str) -> str | None:
    """'V1.0.10' -> '1.0.10'. Returns None when no version is found."""
    m = _VER_RE.search(text or "")
    if not m:
        return None
    return ".".join(str(int(p)) for p in m.groups())


def profile_for(version: str | None) -> FirmwareProfile:
    if version is None:
        return PROFILES[DEFAULT_VERSION]
    v = normalise_version(version)
    return PROFILES.get(v or "", UNKNOWN)
