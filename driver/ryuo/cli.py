"""Command line front end.

Dry run is the default: frames are printed as hex and nothing is opened.
Pass ``--send`` to talk to the real device (needs hidapi and permission on
the hidraw node).
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Sequence

from . import messages as m
from .device import RyuoDevice
from .firmware import PROFILES, profile_for
from .frame import FrameTooLarge, decode_frame
from .transport import FakeTransport, Transport


def _hex(data: bytes, width: int = 32) -> str:
    return "\n".join(data[i : i + width].hex(" ") for i in range(0, len(data), width))


def _print_frames(dev: RyuoDevice, show_body: bool) -> None:
    for s in dev.sent:
        print(f"# {s.resource} seq={s.seq} frame={len(s.frame)} bytes (limit 1024)")
        if show_body:
            print(decode_frame(s.frame).decode("utf-8", errors="replace"))
        print(_hex(s.frame))
        print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ryuo", description="RYUO IV screen driver")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="print hex frames instead of sending (default)")
    g.add_argument("--send", dest="dry_run", action="store_false",
                   help="really open the device and send")
    p.add_argument("--firmware", default=None,
                   help=f"firmware profile ({', '.join(PROFILES)}; default 1.0.7)")
    p.add_argument("--body", action="store_true", help="also print the decoded text body")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("config", help="push a full session (POST config)")
    c.add_argument("--layout", choices=["full", "split"], default="full")
    c.add_argument("--media", nargs="*", default=[], help="zone 1 media file names")
    c.add_argument("--media2", nargs="*", default=[], help="zone 2 media (split)")
    c.add_argument("--play-mode", default="Single", choices=m.PLAY_MODES)
    c.add_argument("--slots", nargs="*", default=["CPU Temperature", "GPU Temperature", "Date&Time"])
    c.add_argument("--title-color", default="#E5252B")
    c.add_argument("--content-color", default="#FFFFFF")
    c.add_argument("--brightness", type=int, default=85)
    c.add_argument("--time-zone", default="UTC")

    pr = sub.add_parser("preset", help="repaint slots/colours (POST preset)")
    pr.add_argument("--slots", nargs="*", default=["CPU Temperature", "GPU Temperature", "Date&Time"])
    pr.add_argument("--title-color", default="#E5252B")
    pr.add_argument("--content-color", default="#FFFFFF")

    t = sub.add_parser("telemetry", help="one POST all")
    t.add_argument("--cpu-temp", type=int, default=0)
    t.add_argument("--gpu-temp", type=int, default=0)
    t.add_argument("--readout", action="append", default=[], metavar="LABEL=INT")

    hb = sub.add_parser("heartbeat", help="POST all every second")
    hb.add_argument("--count", type=int, default=3, help="ticks (0 = forever; dry run caps at 3)")
    hb.add_argument("--readout", action="append", default=[], metavar="LABEL=INT")

    b = sub.add_parser("brightness")
    b.add_argument("value", type=int)

    s = sub.add_parser("screen", help="panel on/off")
    s.add_argument("state", choices=["on", "off"])

    r = sub.add_parser("media-rm", help="delete named files from pcMedia/")
    r.add_argument("names", nargs="+")

    sub.add_parser("info", help="POST conn (versions, media listing)")
    sub.add_parser("disconn", help="end the session")
    sub.add_parser("profiles", help="show the firmware capability table")
    return p


def _readouts(pairs: list[str]) -> dict[str, int]:
    out = {}
    for pair in pairs:
        label, sep, val = pair.rpartition("=")
        if not sep:
            raise SystemExit(f"--readout wants LABEL=INT, got {pair!r}")
        out[label] = int(val)
    return out


def _open(dry_run: bool) -> Transport:
    if dry_run:
        return FakeTransport()
    from .transport import open_default

    return open_default()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "profiles":
        for v, prof in PROFILES.items():
            print(f"{v}: vendor={prof.vendor_id and hex(prof.vendor_id)} hevc={prof.native_hevc} "
                  f"fan_label={prof.fan_label_readout} brightness_max={prof.brightness_max} "
                  f"video={prof.recommended_video()}")
        return 0

    transport = _open(args.dry_run)
    dev = RyuoDevice(transport, firmware=args.firmware, rate_limit=not args.dry_run)
    if args.dry_run:
        print(f"# dry run, firmware profile {dev.profile.version}; nothing is sent\n")
    try:
        if args.cmd == "config":
            dev.set_layout(args.layout)
            dev.set_media(args.media, zone=0, play_mode=args.play_mode)
            if args.layout == "split":
                dev.set_media(args.media2, zone=1, play_mode="Single")
            dev.set_colors(args.title_color, args.content_color)
            dev.set_slots(args.slots)
            dev.brightness_value = m.check_brightness(args.brightness, dev.profile.brightness_max)
            dev.screen.time_zone = args.time_zone
            dev.apply()
        elif args.cmd == "preset":
            dev._applied = True  # send as live update
            dev.screen.slots = m.normalise_slots(args.slots)
            dev.set_colors(args.title_color, args.content_color)
        elif args.cmd == "telemetry":
            dev.send_telemetry(
                {"cpu": {"temperature": args.cpu_temp}, "gpu": {"temperature": args.gpu_temp}},
                _readouts(args.readout),
            )
        elif args.cmd == "heartbeat":
            ro = _readouts(args.readout)
            n = min(args.count or 3, 3) if args.dry_run else args.count
            i = 0
            while n == 0 or i < n:
                dev.send_telemetry(None, ro)
                i += 1
                if not args.dry_run:
                    dev.drain_replies()
                    time.sleep(dev.profile.heartbeat_interval_s)
        elif args.cmd == "brightness":
            dev.set_brightness(args.value)
        elif args.cmd == "screen":
            dev.set_screen(args.state == "on")
        elif args.cmd == "media-rm":
            dev.delete_media(args.names)
        elif args.cmd == "info":
            if args.dry_run:
                dev.send(m.conn())
            else:
                resp = dev.request(m.conn(), timeout_s=3.0)
                info = resp.json() if resp else None
                if isinstance(info, dict) and "sn" in info:
                    info["sn"] = "<hidden>"
                print(info)
                fw = ((info or {}).get("version") or {}).get("firmware")
                print("profile:", profile_for(fw).version)
        elif args.cmd == "disconn":
            dev.send(m.disconn())
    except FrameTooLarge as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except m.MessageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.dry_run:
            _print_frames(dev, args.body)
        transport.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
