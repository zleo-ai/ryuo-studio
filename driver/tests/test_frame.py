import pytest

from ryuo.frame import (
    FrameError,
    FrameReader,
    FrameTooLarge,
    checksum,
    decode_frame,
    encode_frame,
    stuff,
    to_report,
    unstuff,
)

# Worked example from FoxPodZ/ryuo-iv-screen PROTOCOL.md section 2
# ("GET spec" with SeqNumber=1). Byte values are a protocol fact, re-typed here.
GET_SPEC_SEQ1 = bytes.fromhex(
    "5A 00 43 47 45 54 20 73 70 65 63 20 31 0D 0A 43"
    "6F 6E 74 65 6E 74 54 79 70 65 3D 6A 73 6F 6E 0D"
    "0A 43 6F 6E 74 65 6E 74 4C 65 6E 67 74 68 3D 30"
    "0D 0A 53 65 71 4E 75 6D 62 65 72 3D 31 0D 0A 0D"
    "0A D0 5A"
)


def test_documented_get_spec_frame_decodes():
    body = decode_frame(GET_SPEC_SEQ1)
    assert body == b"GET spec 1\r\nContentType=json\r\nContentLength=0\r\nSeqNumber=1\r\n\r\n"
    assert len(body) == 62


def test_documented_get_spec_frame_reencodes_byte_identical():
    assert encode_frame(decode_frame(GET_SPEC_SEQ1)) == GET_SPEC_SEQ1


def test_checksum_is_additive_low_byte():
    assert checksum(b"") == 0
    assert checksum(b"\xff\x02") == 0x01
    inner = bytes.fromhex("0043") + decode_frame(GET_SPEC_SEQ1)
    assert checksum(inner) == 0xD0


@pytest.mark.parametrize(
    "raw,stuffed",
    [
        (b"\x5a", b"\x5b\x01"),
        (b"\x5b", b"\x5b\x02"),
        (b"\x5a\x5b\x5a", b"\x5b\x01\x5b\x02\x5b\x01"),
        (b"\x00\x01\x5c", b"\x00\x01\x5c"),
    ],
)
def test_stuffing(raw, stuffed):
    assert stuff(raw) == stuffed
    assert unstuff(stuffed) == raw


def test_unstuff_rejects_bad_escape_and_bare_delimiter():
    with pytest.raises(FrameError):
        unstuff(b"\x5b\x03")
    with pytest.raises(FrameError):
        unstuff(b"\x5b")
    with pytest.raises(FrameError):
        unstuff(b"\x00\x5a\x00")


@pytest.mark.parametrize(
    "body",
    [b"", b"hello", bytes(range(256)), b"\x5a" * 40, b"\x5b\x5a" * 33, "中文 ü".encode()],
)
def test_round_trip(body):
    frame = encode_frame(body)
    assert frame[0] == 0x5A and frame[-1] == 0x5A
    assert 0x5A not in frame[1:-1]
    assert decode_frame(frame) == body


def test_escaping_in_length_and_checksum_bytes():
    # body length 85 -> LEN = 90 = 0x5A, the length byte itself must be escaped
    body = b"a" * 85
    frame = encode_frame(body)
    assert frame[1:4] == b"\x00\x5b\x01"
    assert decode_frame(frame) == body
    # find a body whose checksum is 0x5A or 0x5B
    for n in range(256):
        b = bytes([n])
        if checksum((6).to_bytes(2, "big") + b) in (0x5A, 0x5B):
            f = encode_frame(b)
            assert f[-3] == 0x5B
            assert decode_frame(f) == b
            break
    else:  # pragma: no cover
        pytest.fail("no checksum collision found")


def test_corrupted_checksum_is_rejected():
    frame = bytearray(GET_SPEC_SEQ1)
    frame[-2] ^= 0x01
    with pytest.raises(FrameError, match="checksum"):
        decode_frame(bytes(frame))


def test_wrong_length_is_rejected():
    frame = bytearray(GET_SPEC_SEQ1)
    frame[2] = 0x44
    with pytest.raises(FrameError, match="length"):
        decode_frame(bytes(frame))


def test_missing_delimiters_rejected():
    with pytest.raises(FrameError):
        decode_frame(GET_SPEC_SEQ1[:-1])
    with pytest.raises(FrameError):
        decode_frame(GET_SPEC_SEQ1[1:])


def test_exactly_1024_bytes_is_accepted():
    body = b"x" * (1024 - 5)
    assert len(encode_frame(body)) == 1024


def test_oversize_raises_instead_of_dropping():
    with pytest.raises(FrameTooLarge) as ei:
        encode_frame(b"x" * (1024 - 4))
    assert ei.value.size == 1025


def test_stuffing_counts_towards_size_limit():
    # 600 raw bytes of 0x5A stuff to 1200 bytes
    with pytest.raises(FrameTooLarge):
        encode_frame(b"\x5a" * 600)


def test_to_report_pads_and_prefixes():
    rep = to_report(GET_SPEC_SEQ1, report_id=0)
    assert len(rep) == 1025
    assert rep[0] == 0
    assert rep[1 : 1 + len(GET_SPEC_SEQ1)] == GET_SPEC_SEQ1
    assert set(rep[1 + len(GET_SPEC_SEQ1) :]) == {0}
    with pytest.raises(FrameTooLarge):
        to_report(b"\x5a" + b"x" * 1024 + b"\x5a")


def test_reader_reassembles_multi_report_reply():
    body = b"1 200\r\nAckNumber=2\r\n\r\n" + b'{"x":"' + b"y" * 1500 + b'"}'
    frame = encode_frame(body, limit=None)
    r = FrameReader()
    for i in range(0, len(frame), 1024):
        chunk = frame[i : i + 1024]
        r.feed(chunk + bytes(1024 - len(chunk)))
    assert list(r.frames()) == [body]


def test_reader_resyncs_after_garbage():
    good = encode_frame(b"ok")
    r = FrameReader()
    r.feed(b"\x00\x00\x5a\x01\x02\x5a" + good + b"\x00" * 10)
    assert list(r.frames()) == [b"ok"]
    assert r.errors


def test_reader_waits_for_incomplete_frame():
    good = encode_frame(b"hello")
    r = FrameReader()
    r.feed(good[:4])
    assert list(r.frames()) == []
    r.feed(good[4:])
    assert list(r.frames()) == [b"hello"]
