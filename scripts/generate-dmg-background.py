#!/usr/bin/env python3
"""Generate Heuriva DMG background + optional Windows NSIS assets."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "desktop" / "src-tauri" / "images"
TEAL = (15, 118, 110)  # Heuriva brand teal
TEAL_SOFT = (45, 148, 140)
INK = (30, 41, 51)
MUTED = (100, 116, 139)


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _gradient(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    top = (248, 250, 252)
    bottom = (226, 239, 237)
    accent = (210, 235, 232)
    for y in range(h):
        ty = y / max(h - 1, 1)
        base = _lerp(top, bottom, ty)
        for x in range(w):
            tx = x / max(w - 1, 1)
            # Soft vignette toward brand teal at the edges
            edge = min(tx, 1 - tx, ty, 1 - ty) * 4
            edge = max(0.0, min(1.0, edge))
            c = _lerp(accent, base, edge)
            px[x, y] = c
    return img


def _draw_arrow(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int) -> None:
    # Smooth upward arc between app icon and Applications folder.
    steps = 64
    points: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t - math.sin(math.pi * t) * 26
        points.append((x, y))

    # Continuous stroke with soft outer glow
    draw.line(points, fill=TEAL_SOFT + (70,), width=8, joint="curve")
    draw.line(points, fill=TEAL + (230,), width=4, joint="curve")

    tip = points[-1]
    # Arrowhead oriented along the last segment
    dx = points[-1][0] - points[-4][0]
    dy = points[-1][1] - points[-4][1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    left = (tip[0] - ux * 16 + px * 9, tip[1] - uy * 16 + py * 9)
    right = (tip[0] - ux * 16 - px * 9, tip[1] - uy * 16 - py * 9)
    draw.polygon([tip, left, right], fill=TEAL)


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSRounded.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def write_dmg_background() -> Path:
    w, h = 660, 400
    img = _gradient((w, h)).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # Soft cards under icon drop targets (app left, Applications right)
    for cx in (180, 480):
        box = (cx - 70, 100, cx + 70, 240)
        draw.rounded_rectangle(box, radius=28, fill=(255, 255, 255, 170), outline=TEAL + (40,), width=2)

    _draw_arrow(draw, 250, 170, 410, 170)

    title = "Heuriva"
    subtitle = "Drag to Applications to install"
    title_font = _font(28)
    sub_font = _font(15)
    # Centered brand line near bottom
    tw = draw.textlength(title, font=title_font)
    sw = draw.textlength(subtitle, font=sub_font)
    draw.text(((w - tw) / 2, 300), title, fill=INK + (255,), font=title_font)
    draw.text(((w - sw) / 2, 338), subtitle, fill=MUTED + (255,), font=sub_font)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "dmg-background.png"
    img.convert("RGB").save(path, format="PNG", optimize=True)
    return path


def write_nsis_assets() -> tuple[Path, Path]:
    """Simple branded sidebar + header for the Windows NSIS wizard."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Classic NSIS sidebar is 164x314
    side = _gradient((164, 314)).convert("RGBA")
    sdraw = ImageDraw.Draw(side, "RGBA")
    sdraw.rounded_rectangle((22, 36, 142, 156), radius=24, fill=(255, 255, 255, 200))
    # Mini H mark
    sdraw.rounded_rectangle((52, 60, 68, 132), radius=8, fill=TEAL)
    sdraw.rounded_rectangle((96, 60, 112, 132), radius=8, fill=TEAL)
    sdraw.arc((62, 82, 102, 118), start=200, end=340, fill=TEAL, width=10)
    for cx, cy in ((60, 96), (82, 86), (104, 96)):
        sdraw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=(255, 255, 255), width=2)
    label_font = _font(18)
    lw = sdraw.textlength("Heuriva", font=label_font)
    sdraw.text(((164 - lw) / 2, 190), "Heuriva", fill=INK + (255,), font=label_font)
    side_path = OUT_DIR / "nsis-sidebar.bmp"
    side.convert("RGB").save(side_path, format="BMP")

    # Header strip 150x57
    header = _gradient((150, 57)).convert("RGBA")
    hdraw = ImageDraw.Draw(header, "RGBA")
    hdraw.rounded_rectangle((10, 10, 47, 47), radius=8, fill=TEAL)
    hdraw.rounded_rectangle((18, 16, 24, 40), radius=3, fill=(255, 255, 255))
    hdraw.rounded_rectangle((33, 16, 39, 40), radius=3, fill=(255, 255, 255))
    hfont = _font(14)
    hdraw.text((56, 20), "Heuriva", fill=INK + (255,), font=hfont)
    header_path = OUT_DIR / "nsis-header.bmp"
    header.convert("RGB").save(header_path, format="BMP")
    return side_path, header_path


def main() -> None:
    dmg = write_dmg_background()
    side, header = write_nsis_assets()
    print(f"Wrote {dmg}")
    print(f"Wrote {side}")
    print(f"Wrote {header}")


if __name__ == "__main__":
    main()
