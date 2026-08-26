#!/usr/bin/env python3
"""Install Heuriva branding icons from a source image (Tauri + Session UI)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "desktop" / "branding" / "icon-source.jpg"
TAURI_ICONS = ROOT / "desktop" / "src-tauri" / "icons"
WEB_STATIC = ROOT / "src" / "heuriva" / "web" / "static"


def _square_crop(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def _write_png(img: Image.Image, size: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    resized = img.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(path, format="PNG", optimize=True)


def _write_icns(source_png: Path, dest: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "heuriva.iconset"
        iconset.mkdir()
        pairs = [
            (16, "icon_16x16.png"),
            (32, "icon_32x32.png"),
            (64, "icon_32x32@2x.png"),
            (128, "icon_128x128.png"),
            (256, "icon_128x128@2x.png"),
            (512, "icon_512x512.png"),
            (1024, "icon_512x512@2x.png"),
        ]
        for size, name in pairs:
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(source_png), "--out", str(iconset / name)],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(dest)],
            check=True,
            capture_output=True,
        )


def install_icons(source: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"icon source not found: {source}")
    img = _square_crop(Image.open(source).convert("RGBA"))

    branding = ROOT / "desktop" / "branding"
    branding.mkdir(parents=True, exist_ok=True)
    if source.resolve() != (branding / "icon-source.jpg").resolve():
        img.convert("RGB").save(branding / "icon-source.jpg", format="JPEG", quality=95)

    _write_png(img, 1024, TAURI_ICONS / "icon.png")
    _write_png(img, 256, TAURI_ICONS / "128x128@2x.png")
    _write_png(img, 128, TAURI_ICONS / "128x128.png")
    _write_png(img, 32, TAURI_ICONS / "32x32.png")

    _write_png(img, 64, WEB_STATIC / "icon.png")
    _write_png(img, 32, WEB_STATIC / "favicon.png")
    _write_png(img, 180, WEB_STATIC / "apple-touch-icon.png")

    try:
        _write_icns(TAURI_ICONS / "icon.png", TAURI_ICONS / "icon.icns")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"icns skipped: {exc}", file=sys.stderr)

    print(f"Installed icons from {source}")
    print(f"  Tauri: {TAURI_ICONS}")
    print(f"  Web:   {WEB_STATIC}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Heuriva icons from source artwork")
    parser.add_argument(
        "source",
        nargs="?",
        default=str(DEFAULT_SOURCE),
        help=f"Source square image (default: {DEFAULT_SOURCE})",
    )
    args = parser.parse_args()
    install_icons(Path(args.source).expanduser())


if __name__ == "__main__":
    main()
