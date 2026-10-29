"""High-level API for the RYUO IV screen.

The device holds the session only while telemetry keeps arriving (about 10 s
without ``POST all`` and it falls back to the stock standby video). So the
usual shape is::

    dev = RyuoDevice(transport)
    dev.set_layout("full")
    dev.set_media(["clip.mp4"])
    dev.set_slots(["CPU Temperature", "Date&Time", "Fan Speed Claude"])
    dev.apply()                     # one POST config
    with dev.heartbeat(lambda: ({"cpu": {"temperature": 48}}, {"Claude": 42})):
        ...                         # POST all once a second in the background
    dev.close()                     # POST disconn

``set_colors`` / ``set_slots`` after :meth:`apply` go out as ``POST preset``,
which repaints without restarting media.
"""

from __future__ import annotations

import threading
import time
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from . import messages as m
from .firmware import FirmwareProfile, normalise_version, profile_for
from .frame import FrameReader, encode_frame, to_report
from .transport import Transport

Layout = Literal["full", "split"]
TelemetryProvider = Callable[[], "tuple[dict[str, Any], dict[str, int]] | dict[str, Any]"]


class CapabilityWarning(UserWarning):
    """A feature is used on firmware where it has not been verified."""


@dataclass
class SentFrame:
    resource: str
    seq: int
    frame: bytes


class RyuoDevice:
    def __init__(
        self,
        transport: Transport,
        *,
        firmware: str | None = None,
        report_id: int = 0x00,
        rate_limit: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.profile: FirmwareProfile = profile_for(firmware)
        self.report_id = report_id
        self.rate_limit = rate_limit
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._seq = 0
        self._last_send = float("-inf")
        self._settle_until = float("-inf")
        self._reader = FrameReader()
        self.sent: list[SentFrame] = []
        self.screen = m.ScreenConfig()
        self.brightness_value = 85
        self.temperature_unit = "Celsius"
        self.cpu_name = ""
        self.gpu_name = ""
        self._applied = False

    # ------------------------------------------------------------------
    # low level
    # ------------------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def encode(self, req: m.Request, seq: int) -> bytes:
        """Build the stuffed frame; raises FrameTooLarge before any I/O."""
        return encode_frame(req.body(seq))

    def send(self, req: m.Request) -> SentFrame:
        with self._lock:
            seq = self._next_seq()
            frame = self.encode(req, seq)
            if self.rate_limit:
                now = self._clock()
                gap = 1.0 / self.profile.max_requests_per_s
                wait = max(self._last_send + gap, self._settle_until) - now
                if wait > 0:
                    self._sleep(wait)
            self.transport.write_report(to_report(frame, report_id=self.report_id))
            self._last_send = self._clock()
            if req.resource == "config":
                self._settle_until = self._last_send + self.profile.config_settle_s
            sent = SentFrame(req.resource, seq, frame)
            self.sent.append(sent)
            return sent

    def drain_replies(self, timeout_ms: int = 0, max_reports: int = 64) -> list[m.Response]:
        """Read whatever replies are already buffered, without blocking long."""
        out: list[m.Response] = []
        for _ in range(max_reports):
            data = self.transport.read_report(timeout_ms)
            if not data:
                break
            self._reader.feed(data)
        for body in self._reader.frames():
            try:
                out.append(m.parse_response(body))
            except m.MessageError:
                continue
        return out

    def request(self, req: m.Request, timeout_s: float = 2.0) -> m.Response | None:
        """Send and wait for the reply whose AckNumber is seq + 1."""
        sent = self.send(req)
        deadline = self._clock() + timeout_s
        while True:
            for resp in self.drain_replies(timeout_ms=50):
                if resp.ack == sent.seq + 1:
                    return resp
            if self._clock() >= deadline:
                return None

    # ------------------------------------------------------------------
    # firmware detection
    # ------------------------------------------------------------------
    def detect_firmware(self) -> FirmwareProfile:
        """Ask the device (POST conn) and switch to the matching profile."""
        resp = self.request(m.conn(), timeout_s=3.0)
        if resp is not None:
            info = resp.json() or {}
            fw = (info.get("version") or {}).get("firmware")
            if fw:
                self.profile = profile_for(normalise_version(fw) or fw)
        return self.profile

    def _require(self, cap: str, what: str) -> None:
        level = getattr(self.profile, cap)
        if level != "verified":
            warnings.warn(
                f"{what} is '{level}' on firmware {self.profile.version}; check it on the panel",
                CapabilityWarning,
                stacklevel=3,
            )

    # ------------------------------------------------------------------
    # state setters (local until apply / preset)
    # ------------------------------------------------------------------
    def set_layout(self, layout: Layout) -> None:
        if layout == "full":
            self.screen.zones = self.screen.zones[:1]
        elif layout == "split":
            self._require("split_mode", "split screen")
            if len(self.screen.zones) == 1:
                self.screen.zones.append(m.Zone(settings=m.ZoneSettings(
                    self.screen.zones[0].settings.title_color,
                    self.screen.zones[0].settings.content_color,
                )))
        else:
            raise m.MessageError("layout must be 'full' or 'split'")
        self._applied = False

    def set_media(self, media: list[str], *, zone: int = 0, play_mode: str = "Single") -> None:
        z = self.screen.zones[zone]
        for name in media:
            m.check_media_name(name)
            if name.upper().startswith("RYUO") and not name.startswith("RYUO_IV_HW_Info_"):
                warnings.warn(
                    f"{name!r} starts with RYUO: the device looks in the preset folder first",
                    UserWarning,
                    stacklevel=2,
                )
        z.media = list(media)
        z.play_mode = m.check_play_mode(play_mode)
        self._applied = False

    def set_colors(self, title: str, content: str, *, zone: int | None = None) -> None:
        m.check_color(title)
        m.check_color(content)
        zones = self.screen.zones if zone is None else [self.screen.zones[zone]]
        for z in zones:
            z.settings.title_color = title
            z.settings.content_color = content
        self._live_update()

    def set_slots(self, slots: list[str]) -> None:
        normalised = m.normalise_slots(slots)
        if any(s.startswith(m.FAN_PREFIX) for s in normalised):
            self._require("fan_label_readout", "custom 'Fan Speed <label>' readouts")
        self.screen.slots = normalised
        self._live_update()

    def set_brightness(self, value: int) -> SentFrame:
        self.brightness_value = m.check_brightness(value, self.profile.brightness_max)
        return self.send(m.brightness(value, self.profile.brightness_max))

    def set_screen(self, on: bool) -> SentFrame:
        return self.send(m.screen_enable(on))

    def delete_media(self, names: list[str]) -> SentFrame:
        return self.send(m.media_delete(names))

    def _live_update(self) -> None:
        if self._applied:
            self.send(m.preset(self.screen))

    # ------------------------------------------------------------------
    # session
    # ------------------------------------------------------------------
    def config_request(self) -> m.Request:
        return m.config(
            self.screen,
            brightness=self.brightness_value,
            temperature_unit=self.temperature_unit,
            cpu_name=self.cpu_name,
            gpu_name=self.gpu_name,
            brightness_max=self.profile.brightness_max,
        )

    def apply(self) -> SentFrame:
        """Push the whole session (POST config). Restarts media playback."""
        sent = self.send(self.config_request())
        self._applied = True
        return sent

    def send_telemetry(
        self,
        values: dict[str, Any] | None = None,
        readouts: dict[str, int] | None = None,
    ) -> SentFrame:
        ts = int(time.time() * 1000)
        return self.send(m.telemetry(values, readouts=readouts, timestamp_ms=ts))

    @contextmanager
    def heartbeat(
        self,
        provider: TelemetryProvider | None = None,
        *,
        interval_s: float | None = None,
        disconnect_on_exit: bool = False,
    ) -> Iterator["Heartbeat"]:
        hb = Heartbeat(self, provider, interval_s or self.profile.heartbeat_interval_s)
        hb.start()
        try:
            yield hb
        finally:
            hb.stop()
            if disconnect_on_exit:
                self.send(m.disconn())

    def close(self) -> None:
        try:
            self.send(m.disconn())
        finally:
            self.transport.close()


class Heartbeat(threading.Thread):
    """Background ``POST all`` loop. Never waits for replies; drains them."""

    def __init__(self, dev: RyuoDevice, provider: TelemetryProvider | None, interval_s: float) -> None:
        super().__init__(name="ryuo-heartbeat", daemon=True)
        if interval_s >= dev.profile.standby_timeout_s / 2:
            raise ValueError("heartbeat interval too close to the standby timeout")
        self.dev = dev
        self.provider = provider
        self.interval_s = interval_s
        self.ticks = 0
        self.errors: list[BaseException] = []
        self._stop_evt = threading.Event()

    def run(self) -> None:
        values: dict[str, Any] | None = None
        readouts: dict[str, int] | None = None
        while not self._stop_evt.is_set():
            if self.provider is not None:
                try:
                    got = self.provider()
                    if isinstance(got, tuple):
                        values, readouts = got
                    else:
                        values, readouts = got, None
                except Exception as exc:  # keep last values: the session must not lapse
                    self._record(exc)
            try:
                self.dev.send_telemetry(values, readouts)
                self.dev.drain_replies(timeout_ms=0)
                self.ticks += 1
            except Exception as exc:
                self._record(exc)
            self._stop_evt.wait(self.interval_s)

    def _record(self, exc: BaseException) -> None:
        self.errors.append(exc)
        if len(self.errors) > 100:
            del self.errors[:50]

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        self.join(timeout)
