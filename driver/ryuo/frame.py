"""Wire framing for the RYUO IV screen.

Layout of one frame (all multi-byte integers big-endian)::

    0x5A | stuff(LEN_hi LEN_lo BODY... CKSUM) | 0x5A

* ``LEN`` is ``len(BODY) + 5`` - the unstuffed size of the whole frame
  (two delimiters, two length bytes, one checksum byte).
* ``CKSUM`` is the low byte of the plain sum of the length bytes and body.
* Stuffing replaces ``0x5A`` with ``5B 01`` and ``0x5B`` with ``5B 02``.
  The two delimiters are never stuffed.

A frame sent to the device must fit in a single 1024-byte HID report; the
device silently discards anything longer. :func:`encode_frame` therefore
raises :class:`FrameTooLarge` instead of producing such a frame.
"""

from __future__ import annotations

from collections.abc import Iterator

DELIM = 0x5A
ESC = 0x5B
_ESC_MAP = {0x5A: b"\x5b\x01", 0x5B: b"\x5b\x02"}
_UNESC_MAP = {0x01: 0x5A, 0x02: 0x5B}

REPORT_SIZE = 1024
"""Payload size of one HID report (without the report-id byte)."""

MAX_FRAME_SIZE = REPORT_SIZE
"""Largest stuffed frame, delimiters included, the device will accept."""

_OVERHEAD = 5  # 2 delimiters + 2 length bytes + 1 checksum


class FrameError(ValueError):
    """A byte sequence is not a valid frame."""


class FrameTooLarge(FrameError):
    """The encoded frame would not fit in one HID report."""

    def __init__(self, size: int, limit: int = MAX_FRAME_SIZE) -> None:
        super().__init__(
            f"encoded frame is {size} bytes, limit is {limit}; "
            "the device would drop it silently (shorten the playlist or names)"
        )
        self.size = size
        self.limit = limit


def checksum(data: bytes) -> int:
    """Additive 8-bit checksum over ``LEN + BODY``."""
    return sum(data) & 0xFF


def stuff(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        esc = _ESC_MAP.get(b)
        if esc is None:
            out.append(b)
        else:
            out += esc
    return bytes(out)


def unstuff(data: bytes) -> bytes:
    out = bytearray()
    it = iter(data)
    for b in it:
        if b == DELIM:
            raise FrameError("unescaped 0x5A inside frame")
        if b != ESC:
            out.append(b)
            continue
        try:
            nxt = next(it)
        except StopIteration:
            raise FrameError("frame ends in the middle of an escape") from None
        try:
            out.append(_UNESC_MAP[nxt])
        except KeyError:
            raise FrameError(f"invalid escape 5B {nxt:02X}") from None
    return bytes(out)


def encode_frame(body: bytes, *, limit: int | None = MAX_FRAME_SIZE) -> bytes:
    """Wrap ``body`` into a delimited, stuffed frame.

    Raises :class:`FrameTooLarge` if the result exceeds ``limit`` bytes.
    Pass ``limit=None`` only for tests that need an oversize frame.
    """
    length = len(body) + _OVERHEAD
    if length > 0xFFFF:
        raise FrameTooLarge(length, 0xFFFF)
    inner = length.to_bytes(2, "big") + body
    inner += bytes([checksum(inner)])
    frame = bytes([DELIM]) + stuff(inner) + bytes([DELIM])
    if limit is not None and len(frame) > limit:
        raise FrameTooLarge(len(frame), limit)
    return frame


def decode_frame(frame: bytes) -> bytes:
    """Validate one delimited frame and return its body."""
    if len(frame) < 2 or frame[0] != DELIM or frame[-1] != DELIM:
        raise FrameError("frame must start and end with 0x5A")
    inner = unstuff(frame[1:-1])
    if len(inner) < 3:
        raise FrameError("frame too short")
    length = int.from_bytes(inner[:2], "big")
    body, cks = inner[2:-1], inner[-1]
    if length != len(body) + _OVERHEAD:
        raise FrameError(f"length field {length} != body {len(body)} + 5")
    if checksum(inner[:-1]) != cks:
        raise FrameError(
            f"checksum mismatch: got {cks:02X}, want {checksum(inner[:-1]):02X}"
        )
    return body


def to_report(frame: bytes, *, report_id: int = 0x00) -> bytes:
    """Zero-pad ``frame`` to one report and prepend the report-id byte.

    The returned buffer is what goes to ``hid_write`` (1 + 1024 bytes).
    """
    if len(frame) > REPORT_SIZE:
        raise FrameTooLarge(len(frame), REPORT_SIZE)
    return bytes([report_id]) + frame + bytes(REPORT_SIZE - len(frame))


class FrameReader:
    """Reassemble device replies, which may span several reports.

    Feed raw report payloads with :meth:`feed`; iterate :meth:`frames` to get
    complete, checksum-valid bodies. Zero padding between frames is skipped.
    A candidate that fails to decode is dropped up to (not including) its
    closing ``0x5A``, because that byte may open the next frame.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.errors: list[str] = []

    def feed(self, data: bytes) -> None:
        self._buf += data

    def frames(self) -> Iterator[bytes]:
        while True:
            # skip padding / garbage before a start delimiter
            start = self._buf.find(bytes([DELIM]))
            if start < 0:
                self._buf.clear()
                return
            del self._buf[:start]
            end = self._buf.find(bytes([DELIM]), 1)
            if end < 0:
                return  # incomplete, wait for more data
            candidate = bytes(self._buf[: end + 1])
            try:
                body = decode_frame(candidate)
            except FrameError as exc:
                self.errors.append(str(exc))
                del self._buf[:end]  # keep the 0x5A, it may start the next frame
                continue
            del self._buf[: end + 1]
            yield body
