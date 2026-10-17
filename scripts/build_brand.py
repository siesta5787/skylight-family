"""Render the integration's brand PNGs from the SVG sources in brand/.

Home Assistant serves a custom integration's images from
`custom_components/<domain>/brand/` (falling back to its CDN only when that
folder is absent), so these PNGs ship with the integration and HACS installs
them with everything else. No submission to home-assistant/brands is needed.

Needs `rsvg-convert` (librsvg2-bin). Run from the repo root:

    python3 scripts/build_brand.py

Sizes follow the brands spec: icon 256x256 (+ @2x 512x512); logo with a
shortest side of 128-256 (+ @2x, 256-512). The dark_ variants exist because
the wordmark is dark navy, which vanishes on a dark theme.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand"
OUT = ROOT / "custom_components" / "skylight_family" / "brand"

# (source svg, output name, pixel width, pixel height)
ICON = (256, 256)
LOGO = (760, 200)  # logo.svg's own viewBox; shortest side 200 is in range

# Wordmark colours on a light background -> readable ones for a dark one.
DARK_TEXT = {"#1F2A44": "#FFFFFF", "#5B8DEF": "#9DBBFF"}


def render(svg: Path, out: Path, width: int, height: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "rsvg-convert",
            "--width", str(width),
            "--height", str(height),
            "--keep-aspect-ratio",
            "--output", str(out),
            str(svg),
        ],
        check=True,
    )
    print(f"  {out.relative_to(ROOT)}  {width}x{height}")


def darkened(svg_text: str) -> str:
    """Recolour only the <text> fills, so the icon's gradient is untouched."""

    def swap(match: re.Match[str]) -> str:
        line = match.group(0)
        for light, dark in DARK_TEXT.items():
            line = line.replace(f'fill="{light}"', f'fill="{dark}"')
        return line

    return re.sub(r"<text\b[^>]*>", swap, svg_text)


def main() -> int:
    icon_svg, logo_svg = SRC / "icon.svg", SRC / "logo.svg"
    for path in (icon_svg, logo_svg):
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1

    print("Rendering to", OUT.relative_to(ROOT))
    render(icon_svg, OUT / "icon.png", *ICON)
    render(icon_svg, OUT / "icon@2x.png", ICON[0] * 2, ICON[1] * 2)
    render(logo_svg, OUT / "logo.png", *LOGO)
    render(logo_svg, OUT / "logo@2x.png", LOGO[0] * 2, LOGO[1] * 2)

    dark_text = darkened(logo_svg.read_text(encoding="utf-8"))
    if dark_text == logo_svg.read_text(encoding="utf-8"):
        print("dark variant came out identical to the light one", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        dark_svg = Path(tmp) / "logo-dark.svg"
        dark_svg.write_text(dark_text, encoding="utf-8")
        render(dark_svg, OUT / "dark_logo.png", *LOGO)
        render(dark_svg, OUT / "dark_logo@2x.png", LOGO[0] * 2, LOGO[1] * 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
