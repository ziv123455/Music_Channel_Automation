"""Procedural looping transparent particle overlays for the local FFmpeg video editor.

Draws a short, seamless visual loop with Pillow; FFmpeg repeats the image sequence.
No network services, extra model downloads, or source-video changes required.
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

STYLES = ("none", "dust", "fireflies", "snow", "embers")
LEVELS = ("subtle", "normal", "strong")
FPS = 12
FRAME_COUNT = 72
WIDTH = 640
HEIGHT = 360

PALETTES = {
    "dust": (232, 213, 171),
    "fireflies": (188, 246, 111),
    "snow": (229, 241, 255),
    "embers": (255, 136, 59),
}


def build_particle_frames(folder, style="dust", intensity="normal"):
    """Return a frame_%03d.png pattern for a seamless six-second alpha loop."""
    if style not in STYLES or style == "none":
        raise ValueError("Select a supported particle effect.")
    if intensity not in LEVELS:
        raise ValueError("Select a supported particle strength.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(10821)
    density = {"subtle": 0.6, "normal": 1.0, "strong": 1.6}[intensity]
    count = int((68 if style == "dust" else 42 if style == "fireflies"
                 else 86 if style == "snow" else 56) * density)
    specs = []
    for _ in range(count):
        specs.append((
            rng.uniform(0, WIDTH),
            rng.uniform(0, HEIGHT),
            rng.uniform(0.2, 2.0),
            rng.uniform(0, 2 * math.pi),
            rng.uniform(9, 58),
            rng.uniform(0.65, 1.65),
            rng.uniform(0.35, 1),
        ))

    rgb = PALETTES[style]
    for frame_index in range(FRAME_COUNT):
        theta = 2 * math.pi * frame_index / FRAME_COUNT
        image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        for x0, y0, radius, phase, travel, rate, brightness in specs:
            # Cyclic motion ensures the last frame connects naturally to frame one.
            if style == "snow":
                x = x0 + 9 * math.sin(theta * rate + phase)
                y = y0 + travel * math.sin(theta + phase)
            elif style == "embers":
                x = x0 + travel * 0.3 * math.sin(theta + phase)
                y = y0 - travel * math.sin(theta + phase)
            elif style == "fireflies":
                x = x0 + travel * math.sin(theta + phase)
                y = y0 + travel * 0.55 * math.cos(theta + phase)
            else:
                x = x0 + travel * math.sin(theta + phase)
                y = y0 + travel * 0.45 * math.cos(theta + phase)
            pulse = 0.65 + 0.35 * math.sin(2 * theta + phase)
            alpha = int((150 if style == "snow" else 115) * brightness * pulse)
            if style == "fireflies":
                alpha = min(230, int(alpha * 1.6))
            if style == "embers":
                alpha = min(230, int(alpha * 1.3))
            r = radius * (1.5 if style == "fireflies" else 1.0)
            if style in ("dust", "fireflies", "embers"):
                outer = r * (4 if style == "fireflies" else 2.6)
                draw.ellipse((x - outer, y - outer, x + outer, y + outer),
                             fill=(*rgb, max(1, alpha // 10)))
                draw.ellipse((x - r * 1.8, y - r * 1.8,
                              x + r * 1.8, y + r * 1.8),
                             fill=(*rgb, max(1, alpha // 4)))
            draw.ellipse((x - r, y - r, x + r, y + r),
                         fill=(*rgb, alpha))
        image.save(folder / ("frame_%03d.png" % frame_index), optimize=True)
    return str(folder / "frame_%03d.png")
