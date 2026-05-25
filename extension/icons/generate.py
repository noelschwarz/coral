#!/usr/bin/env python3
"""Rasterize ``coral.svg`` into the four PNG sizes Chrome expects.

Chrome uses 16/32 in the toolbar (the latter for Retina), 48 in
``chrome://extensions``, and 128 on the Chrome Web Store listing.

Run after editing ``coral.svg``::

    uv run --with cairosvg python extension/icons/generate.py

Outputs land in ``extension/public/icons/`` (alongside the manifest's
``icons/`` references), so the Vite build (``publicDir: "public"``)
copies them verbatim into ``dist/icons/`` without needing cairosvg at
build time. The SVG source and this script deliberately live outside
``public/`` so they don't bloat the shipped extension zip.
"""

from __future__ import annotations

from pathlib import Path

import cairosvg  # type: ignore[import-not-found]

SIZES = (16, 32, 48, 128)
HERE = Path(__file__).parent
SVG = HERE / "coral.svg"
OUT_DIR = HERE.parent / "public" / "icons"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_bytes = SVG.read_bytes()
    for size in SIZES:
        out = OUT_DIR / f"icon-{size}.png"
        cairosvg.svg2png(
            bytestring=svg_bytes,
            write_to=str(out),
            output_width=size,
            output_height=size,
        )
        print(f"wrote {out.relative_to(HERE.parent.parent)} ({size}×{size})")


if __name__ == "__main__":
    main()
