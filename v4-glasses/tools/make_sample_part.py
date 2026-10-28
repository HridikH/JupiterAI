"""Render a synthetic 'part' JPEG with a given top-marking — for hardware-free demos.

Draws light marking text centered on a dark IC-like body at full capture
resolution, so the real preprocess + OCR + resolver path can run without a camera.

    python -m tools.make_sample_part --marking NE555P --out eval/datasets/samples/ne555p.jpg
    python -m tools.make_sample_part --marking "LM358P" --line2 "TI 8K"
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 2592, 1944


def _font(size: int):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/SFNSMono.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render(marking: str, line2: str | None, out: Path, size_px: int = 230) -> Path:
    img = Image.new("RGB", (W, H), (28, 28, 30))
    d = ImageDraw.Draw(img)
    f = _font(size_px)
    lines = [marking] + ([line2] if line2 else [])
    for i, line in enumerate(lines):
        w = d.textlength(line, font=f)
        d.text(((W - w) / 2, H / 2 - size_px + i * (size_px + 40)),
               line, fill=(222, 222, 222), font=f)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="JPEG", quality=85)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a synthetic part image")
    ap.add_argument("--marking", required=True, help="top marking, e.g. NE555P")
    ap.add_argument("--line2", default=None, help="optional second line, e.g. date/lot code")
    ap.add_argument("--out", default=None)
    ap.add_argument("--size", type=int, default=230)
    args = ap.parse_args()
    out = Path(args.out) if args.out else Path(f"eval/datasets/samples/{args.marking.lower()}.jpg")
    p = render(args.marking, args.line2, out, args.size)
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
