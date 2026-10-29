import json
import warnings

import pytest

from ryuo import messages as m
from ryuo.device import CapabilityWarning, RyuoDevice
from ryuo.firmware import PROFILES, UNKNOWN, normalise_version, profile_for
from ryuo.frame import FrameTooLarge
from ryuo.transport import FakeTransport


def _payloads(t: FakeTransport):
    out = []
    for body in t.bodies:
        head, _, payload = body.partition(b"\r\n\r\n")
        start = head.split(b"\r\n")[0].decode()
        out.append((start, json.loads(payload) if payload else None))
    return out


def make(fw="1.0.7"):
    t = FakeTransport()
    return t, RyuoDevice(t, firmware=fw, rate_limit=False)


def test_default_profile_is_107():
    t = FakeTransport()
    assert RyuoDevice(t).profile.version == "1.0.7"


def test_version_parsing_and_unknown_is_conservative():
    assert normalise_version("V1.0.10") == "1.0.10"
    assert profile_for("V1.0.10") is PROFILES["1.0.10"]
    p = profile_for("V1.1.0")
    assert p is UNKNOWN
    assert p.brightness_max == 100
    assert p.recommended_video()["codec"] == "h264"
    assert PROFILES["1.0.10"].recommended_video()["codec"] == "hevc"
    assert PROFILES["1.0.7"].recommended_video()["width"] == 1920


def test_full_session_flow():
    t, dev = make()
    dev.set_layout("full")
    dev.set_media(["clip.mp4"])
    dev.set_colors("#00FF9C", "#FFFFFF")
    dev.set_slots(["CPU Temperature", "Date&Time"])
    assert t.reports == []  # nothing sent before apply
    dev.apply()
    dev.set_colors("#112233", "#FFFFFF")  # live update -> preset
    dev.send_telemetry({"cpu": {"temperature": 40}})
    p = _payloads(t)
    assert [s for s, _ in p] == ["POST config 1", "POST preset 1", "POST all 1"]
    cfg = p[0][1]["waterBlockScreen"]["id"]
    assert cfg["media"] == ["clip.mp4"] and cfg["screenMode"] == "Full Screen"
    assert p[1][1]["settings"]["titleColor"] == "#112233"
    assert p[2][1]["cpu"]["temperature"] == 40
    seqs = [s.seq for s in dev.sent]
    assert seqs == [1, 2, 3]


def test_split_layout():
    t, dev = make("1.0.10")
    dev.set_layout("split")
    dev.set_media(["main.mp4"], zone=0)
    dev.set_media(["strip.png"], zone=1, play_mode="Cycle")
    dev.apply()
    cfg = _payloads(t)[0][1]["waterBlockScreen"]["id"]
    assert cfg["screenMode"] == "Screen Splitting"
    assert cfg["media"] == [["main.mp4"], ["strip.png"]]
    dev.set_layout("full")
    dev.apply()
    assert _payloads(t)[1][1]["waterBlockScreen"]["id"]["screenMode"] == "Full Screen"


def test_fan_label_readout_warns_on_107_but_not_1010():
    _, dev = make("1.0.7")
    with pytest.warns(CapabilityWarning):
        dev.set_slots(["Fan Speed Claude"])
    _, dev = make("1.0.10")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dev.set_slots(["Fan Speed Claude"])


def test_readouts_in_telemetry():
    t, dev = make()
    dev.send_telemetry(None, {"Claude": 42, "Codex": 7})
    fans = _payloads(t)[0][1]["fans"]
    assert fans == [
        {"onBoard": True, "name": "Claude", "value": 42},
        {"onBoard": True, "name": "Codex", "value": 7},
    ]


def test_brightness_clamped_by_profile():
    _, dev = make("1.0.7")
    dev.set_brightness(102)
    _, dev = make("9.9.9")
    with pytest.raises(m.MessageError):
        dev.set_brightness(102)


def test_oversize_config_raises_and_sends_nothing():
    t, dev = make()
    dev.set_media([f"long_name_number_{i:04d}_padding_padding.mp4" for i in range(40)], play_mode="Cycle")
    with pytest.raises(FrameTooLarge):
        dev.apply()
    assert t.reports == []


def test_rate_limit_and_config_settle():
    now = [0.0]
    slept = []

    def clock():
        return now[0]

    def sleep(s):
        slept.append(round(s, 3))
        now[0] += s

    t = FakeTransport()
    dev = RyuoDevice(t, rate_limit=True, clock=clock, sleep=sleep)
    dev.apply()
    dev.send_telemetry()
    dev.send_telemetry()
    assert slept == [1.6, 0.125]


def test_request_matches_ack_and_skips_foreign_replies():
    t, dev = make()
    t.queue_reply_body(b"1 200\r\nAckNumber=99\r\n\r\n{}")  # someone else's reply
    t.queue_reply_body(
        b'1 200\r\nAckNumber=2\r\n\r\n{"version":{"firmware":"V1.0.10"},"sn":"x"}'
    )
    prof = dev.detect_firmware()
    assert prof.version == "1.0.10"


def test_heartbeat_sends_and_survives_provider_errors():
    t, dev = make()
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("sensor read failed")
        return ({"cpu": {"temperature": 30 + calls["n"]}}, {"Claude": calls["n"]})

    import time

    with dev.heartbeat(provider, interval_s=0.01) as hb:
        deadline = time.monotonic() + 2
        while hb.ticks < 4 and time.monotonic() < deadline:
            time.sleep(0.005)
    assert hb.ticks >= 4
    assert any(isinstance(e, RuntimeError) for e in hb.errors)
    assert all(s.startswith("POST all") for s, _ in _payloads(t))


def test_heartbeat_interval_must_be_well_below_timeout():
    _, dev = make()
    with pytest.raises(ValueError):
        with dev.heartbeat(None, interval_s=6):
            pass


def test_close_sends_disconn():
    t, dev = make()
    dev.close()
    assert _payloads(t)[-1][0] == "POST disconn 1"
    assert t.closed
