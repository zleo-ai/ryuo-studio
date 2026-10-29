import json

import pytest

from ryuo import messages as m
from ryuo.frame import FrameTooLarge, decode_frame, encode_frame

# Golden payloads: Info Hub traffic as logged on the device, taken from
# FoxPodZ/ryuo-iv-screen captures/infohub-capture.log (POST config on connect,
# host CPU/GPU names already replaced by placeholders upstream) and the
# telemetry frame quoted in PROTOCOL.md section 10.
CAPTURED_CONFIG = (
    '{"temperature":"Celsius","waterBlockScreen":{"enable":true,"displayInSleep":true,'
    '"brightness":85,"id":{"id":"Customization","screenMode":"Full Screen",'
    '"playMode":"Single","media":["RYUO_IV_HW_Info_02.mp4","RYUO_IV_HW_Info_05.mp4",'
    '"RYUO_IV_HW_Info_01.mp4","RYUO_IV_HW_Info_03.mp4","RYUO_IV_HW_Info_04.mp4"],'
    '"settings":{"titleColor":"#E5252B","contentColor":"#FFFFFF",'
    '"filter":{"value":null,"opacity":100},"badges":[]},'
    '"sysinfoDisplay":["CPU Temperature","GPU Temperature","","","",""],'
    '"timeZone":"Europe/Berlin"}},'
    '"spec":{"cpu":"<host CPU model>","gpu":"<host GPU model>"}}'
)

CAPTURED_TELEMETRY = (
    '{"network":{"upload":0,"download":2},'
    '"memory":{"total":32768,"used":16384,"load":50,"temperature":0,"speed":3000},'
    '"cpu":{"load":7,"temperature":36,"temperaturePackage":46,"speedAverage":5325,'
    '"power":48,"voltage":1.147,"usage":6},'
    '"gpu":{"hasDedicated":true,"load":9,"temperature":49,"fan":0,"speed":2640,'
    '"power":87,"voltage":0.965},'
    '"disk":{"total":1000,"used":500,"load":50,"activity":0,"temperature":0,'
    '"readSpeed":0,"writeSpeed":0},'
    '"fans":[{"onBoard":true,"name":"CPU","value":2119},'
    '{"onBoard":true,"name":"AIO Pump","value":3229},'
    '{"onBoard":true,"name":"Chassis 5","value":546},'
    '{"onBoard":true,"name":"Chassis4","value":513}],'
    '"motherboard":{"temperature":31,"chipsetTemperature":52},'
    '"timestamp":1788799762208}'
)


def _captured_screen() -> m.ScreenConfig:
    return m.ScreenConfig(
        zones=[
            m.Zone(
                media=[
                    "RYUO_IV_HW_Info_02.mp4",
                    "RYUO_IV_HW_Info_05.mp4",
                    "RYUO_IV_HW_Info_01.mp4",
                    "RYUO_IV_HW_Info_03.mp4",
                    "RYUO_IV_HW_Info_04.mp4",
                ],
                play_mode="Single",
            )
        ],
        slots=["CPU Temperature", "GPU Temperature"],
        time_zone="Europe/Berlin",
    )


def test_golden_config_payload_matches_capture_byte_for_byte():
    req = m.config(
        _captured_screen(), brightness=85, cpu_name="<host CPU model>", gpu_name="<host GPU model>"
    )
    assert req.payload_bytes() == CAPTURED_CONFIG.encode()


def test_golden_config_frame_fits_and_round_trips():
    req = m.config(
        _captured_screen(), brightness=85, cpu_name="<host CPU model>", gpu_name="<host GPU model>"
    )
    frame = encode_frame(req.body(7))
    assert len(frame) <= 1024
    body = decode_frame(frame)
    head, _, payload = body.partition(b"\r\n\r\n")
    assert head.split(b"\r\n") == [
        b"POST config 1",
        b"ContentType=json",
        f"ContentLength={len(CAPTURED_CONFIG)}".encode(),
        b"SeqNumber=7",
    ]
    assert payload == CAPTURED_CONFIG.encode()


def test_golden_telemetry_payload_matches_capture():
    cap = json.loads(CAPTURED_TELEMETRY)
    fans = cap.pop("fans")
    ts = cap.pop("timestamp")
    req = m.telemetry(cap, readouts={f["name"]: f["value"] for f in fans}, timestamp_ms=ts)
    assert req.payload_bytes() == CAPTURED_TELEMETRY.encode()
    assert len(encode_frame(req.body(1))) <= 1024


def test_split_config_shape():
    screen = m.ScreenConfig(
        zones=[m.Zone(media=["a.png"]), m.Zone(media=["b.png"], play_mode="Cycle")],
        slots=["CPU Temperature", "GPU Temperature"],
    )
    j = screen.to_json()
    assert j["screenMode"] == "Screen Splitting"
    assert j["playMode"] == ["Single", "Cycle"]
    assert j["media"] == [["a.png"], ["b.png"]]
    assert isinstance(j["settings"], list) and len(j["settings"]) == 2
    assert j["sysinfoDisplay"] == ["CPU Temperature", "GPU Temperature", "", "", "", ""]


def test_preset_payload_shape():
    screen = m.ScreenConfig(slots=["Date&Time"])
    screen.zones[0].settings.title_color = "#00FF9C"
    j = json.loads(m.preset(screen).payload_bytes())
    assert list(j) == ["id", "sysinfoDisplay", "settings"]
    assert j["settings"]["titleColor"] == "#00FF9C"


@pytest.mark.parametrize("bad", ["single", "loop", "CYCLE", ""])
def test_play_mode_is_case_sensitive(bad):
    with pytest.raises(m.MessageError):
        m.check_play_mode(bad)


def test_empty_media_needs_single():
    with pytest.raises(m.MessageError):
        m.ScreenConfig(zones=[m.Zone(media=[], play_mode="Cycle")]).to_json()


@pytest.mark.parametrize("bad", ["../x.mp4", "a/b.mp4", "", "..", "c:\\x.mp4"])
def test_media_names_must_be_bare(bad):
    with pytest.raises(m.MessageError):
        m.check_media_name(bad)


def test_slots():
    assert m.normalise_slots(["Fan Speed Claude"]) == ["Fan Speed Claude", "", "", "", "", ""]
    with pytest.raises(m.MessageError):
        m.normalise_slots(["Fan CPU"])  # not an item string
    with pytest.raises(m.MessageError):
        m.normalise_slots(["Fan Speed "])
    with pytest.raises(m.MessageError):
        m.normalise_slots(["Date&Time"] * 7)


def test_colors():
    m.check_color("#E5252B")
    m.check_color("#80E5252B")
    for bad in ["E5252B", "#FFF", "#GGGGGG", "red"]:
        with pytest.raises(m.MessageError):
            m.check_color(bad)


def test_brightness_range():
    assert json.loads(m.brightness(102).payload_bytes()) == {"value": 102}
    with pytest.raises(m.MessageError):
        m.brightness(103)
    with pytest.raises(m.MessageError):
        m.brightness(101, maximum=100)


def test_dangerous_resources_are_blocked():
    for res in ["upgrade", "transport", "transported", "turboPump", "rotate", "power"]:
        with pytest.raises(m.MessageError):
            m.Request("POST", res, {})


def test_media_delete_only_include():
    j = json.loads(m.media_delete(["a.mp4"]).payload_bytes())
    assert j == {"include": ["a.mp4"]}
    with pytest.raises(m.MessageError):
        m.media_delete([])


def test_request_always_has_full_start_line_and_two_headers():
    body = m.disconn().body(3)
    lines = body.split(b"\r\n")
    assert lines[0] == b"POST disconn 1"
    assert lines[1:4] == [b"ContentType=json", b"ContentLength=0", b"SeqNumber=3"]


def test_content_length_counts_utf8_bytes():
    req = m.spec("Ryzen ü", "")
    body = req.body(1).decode()
    assert f"ContentLength={len(req.payload_bytes())}" in body


def test_long_playlist_is_rejected_before_sending():
    names = [f"a_very_long_clip_name_for_testing_{i:03d}.mp4" for i in range(30)]
    req = m.config(m.ScreenConfig(zones=[m.Zone(media=names, play_mode="Cycle")]))
    with pytest.raises(FrameTooLarge):
        encode_frame(req.body(1))


def test_parse_response():
    r = m.parse_response(b'1 200\r\nAckNumber=8\r\nContentLength=2\r\n\r\n{"status":{"fanLCD":""}}')
    assert r.status == 200 and r.ack == 8
    assert r.json() == {"status": {"fanLCD": ""}}


def test_unknown_telemetry_group_rejected():
    with pytest.raises(m.MessageError):
        m.telemetry({"cpuu": {}})
