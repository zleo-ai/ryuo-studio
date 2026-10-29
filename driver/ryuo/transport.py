"""Transports: where 1024-byte reports go.

* :class:`FakeTransport` records every report and can replay canned replies.
* :class:`HidTransport` talks to the real screen through ``hidapi``. It is
  imported lazily and is the only code in this package that touches USB.
"""

from __future__ import annotations

import abc
from collections import deque
from dataclasses import dataclass, field

from .frame import REPORT_SIZE, decode_frame, encode_frame, to_report

PRODUCT_ID = 0x1C76
KNOWN_VENDOR_IDS = (0x0B05, 0x1C75)  # ASUSTek (1.0.3 / 1.0.10), BYDevice ODM (1.0.7)
INTERFACE = 0


class TransportError(RuntimeError):
    pass


class Transport(abc.ABC):
    @abc.abstractmethod
    def write_report(self, report: bytes) -> None:
        """Write one report: report-id byte + 1024 payload bytes."""

    @abc.abstractmethod
    def read_report(self, timeout_ms: int = 0) -> bytes:
        """Return one report payload (up to 1024 bytes), or b"" on timeout."""

    def close(self) -> None:  # pragma: no cover - trivial
        pass

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class FakeTransport(Transport):
    """Records writes; serves queued replies. Never touches hardware."""

    reports: list[bytes] = field(default_factory=list)
    replies: deque[bytes] = field(default_factory=deque)
    closed: bool = False

    def write_report(self, report: bytes) -> None:
        if self.closed:
            raise TransportError("transport closed")
        if len(report) != REPORT_SIZE + 1:
            raise TransportError(f"report must be {REPORT_SIZE + 1} bytes, got {len(report)}")
        self.reports.append(bytes(report))

    def read_report(self, timeout_ms: int = 0) -> bytes:
        return self.replies.popleft() if self.replies else b""

    def close(self) -> None:
        self.closed = True

    # helpers for tests --------------------------------------------------
    @property
    def frames(self) -> list[bytes]:
        """Written frames with report id and zero padding removed."""
        out = []
        for r in self.reports:
            payload = r[1:]
            end = payload.rfind(b"\x5a")
            out.append(payload[: end + 1])
        return out

    @property
    def bodies(self) -> list[bytes]:
        return [decode_frame(f) for f in self.frames]

    def queue_reply_body(self, body: bytes) -> None:
        """Queue a device reply, split across reports if needed (as the device does)."""
        frame = encode_frame(body, limit=None)
        for i in range(0, len(frame), REPORT_SIZE):
            chunk = frame[i : i + REPORT_SIZE]
            self.replies.append(chunk + bytes(REPORT_SIZE - len(chunk)))


class HidTransport(Transport):
    """hidapi-backed transport.

    Matches on the product id and interface 0, accepting only the vendor ids
    known for this product. It never matches on a vendor id alone: 0B05 is
    also used by other ASUS devices on the same machine (e.g. 0B05:19AF).
    """

    def __init__(self, path: bytes | None = None, *, report_id: int = 0x00) -> None:
        try:
            import hid  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise TransportError("hidapi is not installed: pip install 'ryuo[hid]'") from exc
        self._hid = hid
        self.report_id = report_id
        if path is None:
            found = self.find()
            if not found:
                raise TransportError("no RYUO IV screen found (product 1C76, interface 0)")
            if len(found) > 1:
                raise TransportError(f"{len(found)} candidate devices; pass a path explicitly")
            path = found[0]["path"]
        self._dev = hid.device()
        self._dev.open_path(path)

    @staticmethod
    def find() -> list[dict]:  # pragma: no cover - needs hardware
        import hid  # type: ignore[import-not-found]

        return [
            d
            for d in hid.enumerate(0, PRODUCT_ID)
            if d.get("product_id") == PRODUCT_ID
            and d.get("vendor_id") in KNOWN_VENDOR_IDS
            and d.get("interface_number", INTERFACE) == INTERFACE
        ]

    def write_report(self, report: bytes) -> None:  # pragma: no cover - needs hardware
        n = self._dev.write(report)
        if n < 0:
            raise TransportError("hid write failed")

    def read_report(self, timeout_ms: int = 0) -> bytes:  # pragma: no cover
        data = self._dev.read(REPORT_SIZE, timeout_ms)
        return bytes(data)

    def close(self) -> None:  # pragma: no cover
        self._dev.close()


class HidrawTransport(Transport):
    """Linux ``/dev/hidrawN`` transport with no third-party dependency.

    Preferred on Linux: the PyPI ``hidapi`` wheel uses the libusb backend,
    which claims the interface away from the kernel. hidraw only needs read/
    write permission on the node (see ``tools/70-ryuo-iv.rules``).
    """

    def __init__(self, path: str | None = None) -> None:
        import os

        if path is None:
            found = self.find()
            if not found:
                raise TransportError("no RYUO IV hidraw node found (product 1C76)")
            if len(found) > 1:
                raise TransportError(f"{len(found)} candidate hidraw nodes; pass a path explicitly")
            path = found[0]
        self.path = path
        try:
            self._fd = os.open(path, os.O_RDWR)
        except PermissionError as exc:  # pragma: no cover - depends on udev
            raise TransportError(f"no permission on {path}; install tools/70-ryuo-iv.rules") from exc

    @staticmethod
    def find() -> list[str]:  # pragma: no cover - needs hardware
        from pathlib import Path

        res = []
        for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
            try:
                uevent = (node / "device" / "uevent").read_text()
            except OSError:
                continue
            hid_id = next((line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("HID_ID=")), "")
            parts = hid_id.split(":")
            if len(parts) == 3 and int(parts[2], 16) == PRODUCT_ID and int(parts[1], 16) in KNOWN_VENDOR_IDS:
                res.append(f"/dev/{node.name}")
        return res

    def write_report(self, report: bytes) -> None:  # pragma: no cover - needs hardware
        import os

        if os.write(self._fd, report) != len(report):
            raise TransportError("short hidraw write")

    def read_report(self, timeout_ms: int = 0) -> bytes:  # pragma: no cover
        import os
        import select

        ready, _, _ = select.select([self._fd], [], [], max(0, timeout_ms) / 1000)
        if not ready:
            return b""
        return os.read(self._fd, REPORT_SIZE + 1)

    def close(self) -> None:  # pragma: no cover
        import os

        os.close(self._fd)


def open_default() -> Transport:  # pragma: no cover - needs hardware
    """hidraw on Linux, hidapi elsewhere."""
    import sys

    return HidrawTransport() if sys.platform.startswith("linux") else HidTransport()


def frame_to_report(frame: bytes, report_id: int = 0x00) -> bytes:
    return to_report(frame, report_id=report_id)
