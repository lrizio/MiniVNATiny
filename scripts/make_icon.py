#!/usr/bin/env python3
"""Generates icon.ico -- a graphical mark for miniVNA Tiny.

Renders a Smith chart (the VNA's own signature visualization) with a
measurement trace and a glow-dot marker at high resolution and
downsamples for anti-aliasing, using this project's theme.py palette --
same construction technique (supersample + rounded-square background +
theme.py's glow-dot rendering) as the sibling Yaesu Radio Controller
app's scripts/make_icon.py, for family visual consistency, but with
VNA-appropriate imagery (a Smith chart + trace) instead of a broadcast
tower, since this app is not a radio controller.

Usage (run from this project's root):
    python scripts/make_icon.py

Requires: pip install pillow (build-time only, not a runtime dependency).
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "icon.ico")

# Supersampled render size -- downsampled to each .ico size at the end for
# anti-aliased edges (Pillow's ImageDraw has no native AA).
S = 1024

BG = (20, 24, 29, 255)          # theme.BG
PANEL_EDGE = (58, 65, 76, 255)  # theme.METAL_HI
GRID = (58, 65, 76, 255)        # theme.LINE (same hex as METAL_HI in this palette)
BOUNDARY = (167, 179, 192, 255)  # theme.FG_DIM
TRACE_BLUE = (108, 182, 255, 255)  # theme.LED_BLUE
GLOW_GREEN = (87, 227, 154)      # theme.LED_GREEN


def rounded_square_bg(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, outline=PANEL_EDGE, width=max(2, size // 128))
    return img


def draw_smith_grid(img: Image.Image, cx: int, cy: int, r: int) -> None:
    """A simplified Smith-chart grid: the boundary circle, one interior
    constant-resistance circle (r=1, the classic center reference), and
    the horizontal (x=0) diameter -- enough to read as "a Smith chart"
    at icon size without the clutter a full grid would become."""
    d = ImageDraw.Draw(img)
    w = max(3, S // 180)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BOUNDARY, width=max(4, S // 110))
    # constant-R=1 circle: center at half-radius toward the right edge,
    # radius = half the boundary radius (standard Smith chart geometry).
    r1 = r // 2
    d.ellipse([cx, cy - r1, cx + 2 * r1, cy + r1], outline=GRID, width=w)
    # a couple of smaller flanking circles for texture, same family as the
    # matplotlib R-circle set (r=0.2, r=0.5) used in the app's own chart.
    for frac in (0.15, 0.35):
        rc = int(r * frac / (1 + frac))
        cxc = cx - r + int(2 * r * frac / (1 + frac))
        d.ellipse([cxc - rc, cy - rc, cxc + rc, cy + rc], outline=GRID, width=max(2, w - 1))
    d.line([(cx - r, cy), (cx + r, cy)], fill=GRID, width=w)


def draw_trace(img: Image.Image, cx: int, cy: int, r: int) -> tuple[int, int]:
    """A measurement-trace arc curving through the chart, mimicking a
    real reflection sweep -- same visual language as this app's own
    Smith-chart plots. Returns the trace's endpoint (where the glow dot
    marker sits, like a "current point" indicator)."""
    points = []
    n = 48
    for i in range(n):
        t = i / (n - 1)
        angle = math.radians(200 - 140 * t)  # sweeps through the left/lower chart
        mag = 0.72 - 0.42 * t                # spirals inward, like a real damped trace
        x = cx + r * mag * math.cos(angle)
        y = cy + r * mag * math.sin(angle) * -1  # flip: image y grows downward
        points.append((x, y))
    d = ImageDraw.Draw(img)
    d.line(points, fill=TRACE_BLUE, width=max(6, S // 90), joint="curve")
    return int(points[-1][0]), int(points[-1][1])


def draw_glow_dot(img: Image.Image, cx: int, cy: int, core_r: int) -> None:
    """Mirrors theme.py's _paint_glow_dot(): a soft blurred halo, a solid
    core, and a small offset white highlight -- not a flat circle. Kept
    pixel-for-pixel equivalent to the sibling apps' icon technique."""
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(halo)
    halo_r = int(core_r * 2.1)
    d.ellipse([cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r], fill=(*GLOW_GREEN, 160))
    halo = halo.filter(ImageFilter.GaussianBlur(radius=core_r * 0.55))
    img.alpha_composite(halo)

    core = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(core)
    d.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=(*GLOW_GREEN, 255))
    img.alpha_composite(core)

    hl_r = core_r * 0.4
    hl_dx = -core_r * 0.32
    hl_dy = -core_r * 0.32
    highlight = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(highlight)
    d.ellipse([cx + hl_dx - hl_r, cy + hl_dy - hl_r, cx + hl_dx + hl_r, cy + hl_dy + hl_r],
              fill=(255, 255, 255, 130))
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius=hl_r * 0.3))
    img.alpha_composite(highlight)


def build() -> None:
    img = rounded_square_bg(S)
    cx, cy = int(S * 0.5), int(S * 0.52)
    r = int(S * 0.34)
    draw_smith_grid(img, cx, cy, r)
    tip_x, tip_y = draw_trace(img, cx, cy, r)
    draw_glow_dot(img, tip_x, tip_y, int(S * 0.052))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(
        OUT_PATH, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    print(f"Wrote {os.path.abspath(OUT_PATH)}")


if __name__ == "__main__":
    build()
