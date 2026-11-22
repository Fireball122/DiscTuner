"""The disc, drawn from what the run knows.

A DVD records from the hub outward in one spiral, so the burned part of the track is a
solid inner stretch of that spiral. Sector count grows with radius squared, so burn progress
maps to radius by area, not linearly. The 22 dots along one radius are the measurement
points the geometry fit reads back.
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import theme

# Millimetres, DVD
DISC_R = 60.0
HOLE_R = 7.5
CLAMP_R = 22.0
DATA_IN = 24.0
DATA_OUT = 58.0

TURNS = 40  # drawn turns; the real pitch is 0.74 um, far finer than any screen
SUPERSAMPLE = 2
RUNG_ANGLE = math.radians(-40)

GROOVE = "#31343a"
GROOVE_DIM = "#25272b"
CLAMP = "#23262b"
RUNG_PENDING = "#4a4e56"


def burn_radius(fraction: float) -> float:
    """Radius the burn has reached; the area written grows with r squared."""
    fraction = min(max(fraction, 0.0), 1.0)
    return math.sqrt(DATA_IN**2 + fraction * (DATA_OUT**2 - DATA_IN**2))


def _spiral() -> list[tuple[float, float]]:
    """(radius mm, angle) along the track, hub to rim."""
    steps = TURNS * 72
    return [
        (DATA_IN + (DATA_OUT - DATA_IN) * i / steps, 2 * math.pi * TURNS * i / steps)
        for i in range(steps + 1)
    ]


_SPIRAL = _spiral()


def render(size: int, burn: float = 0.0, rungs_done: int = 0, rungs_total: int = 22,
           active_rung: bool = False, present: bool = True) -> Image.Image:
    """Square image `size` px wide, on the panel colour."""
    big = size * SUPERSAMPLE
    px = (big / 2 - 4 * SUPERSAMPLE) / DISC_R  # pixels per mm
    c = big / 2

    def at(radius_mm: float, angle: float) -> tuple[float, float]:
        return c + radius_mm * px * math.cos(angle), c + radius_mm * px * math.sin(angle)

    def circle(draw: ImageDraw.ImageDraw, radius_mm: float, **kw) -> None:
        r = radius_mm * px
        draw.ellipse((c - r, c - r, c + r, c + r), **kw)

    img = Image.new("RGB", (big, big), theme.PANEL)
    d = ImageDraw.Draw(img)

    # Body, clamp zone, hub hole
    circle(d, DISC_R, fill=theme.RAISED, outline=theme.BORDER, width=SUPERSAMPLE)
    circle(d, CLAMP_R, fill=CLAMP)
    circle(d, HOLE_R, fill=theme.PANEL, outline=theme.BORDER, width=SUPERSAMPLE)

    # Track: the whole spiral, then the burned stretch over it
    points = [at(r, a) for r, a in _SPIRAL]
    groove_w = round(1.4 * SUPERSAMPLE)
    d.line(points, fill=GROOVE if present else GROOVE_DIM, width=groove_w, joint="curve")
    if burn > 0:
        limit = burn_radius(burn)
        burned = [p for (r, _), p in zip(_SPIRAL, points, strict=True) if r <= limit]
        if len(burned) > 1:
            d.line(burned, fill=theme.ACCENT, width=groove_w, joint="curve")

    # Two soft neutral reflections, like light across a real disc
    wedges = Image.new("L", (big, big), 0)
    wd = ImageDraw.Draw(wedges)
    r = DISC_R * px
    for centre in (35, 215):
        wd.pieslice((c - r, c - r, c + r, c + r), centre - 18, centre + 18, fill=255)
    wedges = wedges.filter(ImageFilter.GaussianBlur(9 * SUPERSAMPLE))
    ring = Image.new("L", (big, big), 0)
    rd = ImageDraw.Draw(ring)
    rd.ellipse((c - r, c - r, c + r, c + r), fill=255)
    hub = CLAMP_R * px
    rd.ellipse((c - hub, c - hub, c + hub, c + hub), fill=0)
    sheen = ImageChops.multiply(wedges, ring).point(lambda v: int(v * 0.045))
    img.paste((255, 255, 255), mask=sheen)

    # Measurement points along one radius, in a slot cut through the track. The dots shrink as
    # their number grows so 36 of them (a CD) stay apart like 22 (a DVD).
    d = ImageDraw.Draw(img)
    unit = SUPERSAMPLE * min(1.0, 22 / max(rungs_total, 1))
    slot = 3.2 * unit
    centres = [
        at(DATA_IN + (i + 0.5) * (DATA_OUT - DATA_IN) / rungs_total, RUNG_ANGLE) for i in range(rungs_total)
    ]
    for x, y in centres:
        d.ellipse((x - slot, y - slot, x + slot, y + slot), fill=theme.RAISED)
    for i, (x, y) in enumerate(centres):
        if i < rungs_done:
            r, colour = 1.7 * unit, theme.TEXT
        elif i == rungs_done and active_rung:
            r, colour = 2.4 * unit, theme.ACCENT
        else:
            r, colour = 1.3 * unit, RUNG_PENDING
        d.ellipse((x - r, y - r, x + r, y + r), fill=colour)

    return img.resize((size, size), Image.LANCZOS)
