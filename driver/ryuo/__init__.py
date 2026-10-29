"""Clean-room host driver for the ASUS ROG RYUO IV AIO cooler screen."""

from .device import CapabilityWarning, Heartbeat, RyuoDevice
from .firmware import PROFILES, FirmwareProfile, profile_for
from .frame import FrameError, FrameTooLarge, decode_frame, encode_frame
from .transport import FakeTransport, HidTransport, Transport

__all__ = [
    "CapabilityWarning",
    "FakeTransport",
    "FirmwareProfile",
    "FrameError",
    "FrameTooLarge",
    "Heartbeat",
    "HidTransport",
    "PROFILES",
    "RyuoDevice",
    "Transport",
    "decode_frame",
    "encode_frame",
    "profile_for",
]

__version__ = "0.1.0"
