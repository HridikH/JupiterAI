"""Live-feed simulator — push JPEG frames to the server so the UI shows a feed
without hardware (option B).

Streams a single image (optionally animated with a moving marker, or a folder of
frames) to POST /frame at a target FPS. Pair with tools/fake_device.py to also
drive an accepted part so the pinout overlay has data.

    python -m tools.stream_sim --token dev --image eval/datasets/samples/ne555p.jpg
    python -m tools.stream_sim --token dev --dir eval/datasets/frames --fps 12
    # a DIP chip that slowly rotates/drifts (exercises the AR pinout tracker)
    python -m tools.stream_sim --token dev --chip LM358P --pins 8
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import protocol  # noqa: E402


def _animated(base: bytes, phase: int) -> bytes:
    """Draw a small moving dot so the feed visibly updates (proves it's live)."""
    try:
        from PIL import Image, ImageDraw
        im = Image.open(io.BytesIO(base)).convert("RGB")
        d = ImageDraw.Draw(im)
        w, h = im.size
        x = int((0.5 + 0.35 * __import__("math").sin(phase / 6.0)) * w)
        d.ellipse([x - 14, h - 60, x + 14, h - 32], fill=(90, 227, 161))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=70)
        return out.getvalue()
    except Exception:
        return base


def main() -> int:
    ap = argparse.ArgumentParser(description="MJPEG live-feed simulator")
    ap.add_argument("--url", default="http://127.0.0.1:8777")
    ap.add_argument("--token", default="CHANGE_ME_dev_token")
    ap.add_argument("--image", help="single JPEG to stream (animated)")
    ap.add_argument("--dir", help="folder of JPEG frames to loop")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--seconds", type=float, default=0, help="0 = run until Ctrl-C")
    ap.add_argument("--chip", help="render a moving DIP chip with this marking")
    ap.add_argument("--line2", default="TI 7M4")
    ap.add_argument("--pins", type=int, default=8)
    args = ap.parse_args()

    if args.chip:
        import math
        from tools.make_sample_part import render_chip_scene

        def chip_frame(i: int) -> bytes:
            t = i / max(1, args.fps)
            im = render_chip_scene(args.chip, args.line2, args.pins,
                                   angle=25 * math.sin(t * 0.6),
                                   offset=(120 * math.sin(t * 0.4), 60 * math.cos(t * 0.5)))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=80)
            return out.getvalue()

    if args.chip:
        frames = []
    elif args.dir:
        files = sorted(Path(args.dir).glob("*.jpg"))
        frames = [f.read_bytes() for f in files]
        if not frames:
            print("no .jpg frames in", args.dir); return 2
    elif args.image:
        frames = [Path(args.image).read_bytes()]
    else:
        print("give --image, --dir or --chip"); return 2

    url = args.url.rstrip("/") + "/frame"
    headers = {protocol.HDR_TOKEN: args.token, "Content-Type": "image/jpeg"}
    period = 1.0 / max(1, args.fps)
    t_end = time.monotonic() + args.seconds if args.seconds else None
    print(f"[stream-sim] -> {url} @ {args.fps} fps ({'chip '+args.chip if args.chip else 'loop '+str(len(frames)) if args.dir else 'animated'}) Ctrl-C to stop")
    i = 0
    try:
        while True:
            if args.chip:
                data = chip_frame(i)
            else:
                data = frames[i % len(frames)] if args.dir else _animated(frames[0], i)
            try:
                requests.post(url, headers=headers, data=data, timeout=2)
            except requests.RequestException as e:
                print("  post failed:", e)
            i += 1
            if t_end and time.monotonic() > t_end:
                break
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    print(f"[stream-sim] sent {i} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
