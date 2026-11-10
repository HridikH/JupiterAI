"""Render a synthetic 'part' JPEG with a given top-marking — for hardware-free demos.

Draws light marking text centered on a dark IC-like body at full capture
resolution, so the real preprocess + OCR + resolver path can run without a camera.

    python -m tools.make_sample_part --marking NE555P --out eval/datasets/samples/ne555p.jpg
    python -m tools.make_sample_part --marking "LM358P" --line2 "TI 8K"
    # a DIP chip on a bench (legs, notch, rotated) — exercises the AR pinout tracker
    python -m tools.make_sample_part --marking LM358P --scene --pins 8 --angle 20
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


def render_chip_scene(marking: str, line2: str | None = None, pins: int = 8,
                      angle: float = 0.0, offset: tuple[float, float] = (0, 0),
                      size: tuple[int, int] = (1280, 960), scale: float = 1.0) -> Image.Image:
    """Top-down DIP package on a light bench: dark body, notch at the pin-1 end
    (left when the marking is upright), silver legs, rotated by ``angle`` degrees."""
    W, Hh = size
    pitch = 46 * scale
    m = pins // 2
    body_l, body_w = pitch * m + 24 * scale, 150 * scale
    pad = int(60 * scale)
    tw, th = int(body_l + 2 * pad), int(body_w + 2 * (pad + 40 * scale))
    chip = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(chip)
    x0, y0 = pad, (th - body_w) / 2
    leg_w, leg_l = 16 * scale, 40 * scale
    for k in range(m):
        cx = x0 + 12 * scale + pitch * (k + 0.5)
        d.rectangle([cx - leg_w / 2, y0 + body_w - 2, cx + leg_w / 2, y0 + body_w + leg_l], fill=(200, 200, 196, 255))
        d.rectangle([cx - leg_w / 2, y0 - leg_l, cx + leg_w / 2, y0 + 2], fill=(200, 200, 196, 255))
    d.rounded_rectangle([x0, y0, x0 + body_l, y0 + body_w], radius=int(8 * scale), fill=(34, 34, 37, 255))
    r = 18 * scale                                     # pin-1 notch on the left end
    d.pieslice([x0 - r, y0 + body_w / 2 - r, x0 + r, y0 + body_w / 2 + r], -90, 90, fill=(225, 223, 215, 255))
    d.ellipse([x0 + 22 * scale, y0 + body_w - 40 * scale, x0 + 36 * scale, y0 + body_w - 26 * scale], fill=(52, 52, 56, 255))
    f = _font(int(40 * scale))
    lines = [marking] + ([line2] if line2 else [])
    for i, line in enumerate(lines):
        w = d.textlength(line, font=f)
        d.text((x0 + (body_l - w) / 2, y0 + body_w / 2 - 44 * scale * len(lines) / 2 + i * 48 * scale),
               line, fill=(214, 214, 210, 255), font=f)
    chip = chip.rotate(angle, resample=Image.BICUBIC, expand=True)
    bg = Image.new("RGB", (W, Hh), (225, 223, 215))
    bg.paste(chip, (int((W - chip.width) / 2 + offset[0]), int((Hh - chip.height) / 2 + offset[1])), chip)
    return bg


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a synthetic part image")
    ap.add_argument("--marking", required=True, help="top marking, e.g. NE555P")
    ap.add_argument("--line2", default=None, help="optional second line, e.g. date/lot code")
    ap.add_argument("--out", default=None)
    ap.add_argument("--size", type=int, default=230)
    ap.add_argument("--scene", action="store_true", help="render a DIP chip on a bench instead")
    ap.add_argument("--pins", type=int, default=8)
    ap.add_argument("--angle", type=float, default=0.0)
    args = ap.parse_args()
    if args.scene:
        out = Path(args.out) if args.out else Path(f"eval/datasets/samples/{args.marking.lower()}_scene.jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        render_chip_scene(args.marking, args.line2, args.pins, args.angle).save(out, quality=88)
        print(f"wrote {out}")
        return 0
    out = Path(args.out) if args.out else Path(f"eval/datasets/samples/{args.marking.lower()}.jpg")
    p = render(args.marking, args.line2, out, args.size)
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
