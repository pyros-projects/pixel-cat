"""Build pixel_cat/palettes.json. Run: uv run python tools/palettes.py [swatches.png]

The classic palettes are listed as their authors published them. The themed ones are built here from colour
ramps in OKLCH: each ramp runs from dark to light, shifts its hue on the way and is most saturated in the
middle, the way pixel artists build ramps by hand.
"""

import json
import math
import sys
from pathlib import Path

CLASSIC = {
    "gameboy": "0f380f 306230 8bac0f 9bbc0f",                                                   # the DMG greens
    "pico8": "000000 1d2b53 7e2553 008751 ab5236 5f574f c2c3c7 fff1e8 ff004d ffa300 ffec27 00e436 29adff 83769c ff77a8 ffccaa",
    "dawn16": "140c1c 442434 30346d 4e4a4e 854c30 346524 d04648 757161 597dce d27d2c 8595a1 6daa2c d2aa99 6dc2ca dad45e deeed6",  # DawnBringer
    "sweetie16": "1a1c2c 5d275d b13e53 ef7d57 ffcd75 a7f070 38b764 257179 29366f 3b5dc9 41a6f6 73eff7 f4f4f4 94b0c2 566c86 333c57",  # GrafxKid
    "cga": "000000 55ffff ff55ff ffffff",
    "mono": "0d0d12 55555f a9a9b3 ffffff",
}

# (hue from, hue to, peak chroma, lightness from, lightness to, colours)
THEMES = {
    "forest": [(165, 115, 0.13, 0.20, 0.88, 7), (50, 80, 0.08, 0.26, 0.72, 3), (230, 215, 0.05, 0.55, 0.93, 2)],
    "sunset": [(290, 455, 0.17, 0.18, 0.93, 8), (215, 195, 0.08, 0.30, 0.62, 2), (265, 265, 0.05, 0.12, 0.12, 1), (90, 90, 0.03, 0.97, 0.97, 1)],
    "ocean": [(268, 195, 0.13, 0.16, 0.92, 8), (80, 90, 0.07, 0.68, 0.90, 2), (25, 35, 0.15, 0.55, 0.74, 2)],
    "pastel": [(h, h, 0.075, 0.86, 0.86, 1) for h in range(15, 360, 36)] + [(90, 90, 0.01, 0.98, 0.98, 1), (280, 280, 0.03, 0.62, 0.62, 1)],
    "ember": [(15, 100, 0.19, 0.10, 0.97, 10)],
    "ice": [(265, 200, 0.10, 0.18, 0.98, 9)],
    "cyber": [(285, 275, 0.06, 0.10, 0.24, 2), (345, 335, 0.24, 0.38, 0.74, 3), (205, 190, 0.15, 0.42, 0.88, 3), (105, 105, 0.18, 0.92, 0.92, 1), (250, 250, 0.01, 0.98, 0.98, 1)],
    "earth": [(40, 88, 0.09, 0.18, 0.88, 6), (128, 118, 0.08, 0.38, 0.62, 2), (255, 245, 0.03, 0.34, 0.66, 2)],
    "candy": [(5, -15, 0.17, 0.48, 0.90, 4), (165, 160, 0.10, 0.70, 0.90, 2), (100, 100, 0.13, 0.93, 0.93, 1), (305, 295, 0.11, 0.55, 0.80, 2), (235, 235, 0.09, 0.80, 0.80, 1), (60, 60, 0.01, 0.99, 0.99, 1)],
    "sepia": [(60, 88, 0.055, 0.14, 0.96, 10)],
    "dusk16": [(280, 250, 0.09, 0.14, 0.60, 5), (330, 400, 0.14, 0.34, 0.90, 6), (200, 180, 0.08, 0.40, 0.82, 3), (95, 95, 0.05, 0.96, 0.96, 1), (270, 270, 0.02, 0.06, 0.06, 1)],
    "storybook24": [(h, h + 25, 0.13, 0.32, 0.86, 3) for h in (20, 65, 135, 195, 255, 320)] + [(60, 80, 0.03, 0.12, 0.97, 6)],
    # the large ones: enough steps per hue for shading, and neutrals from near black to near white
    "lantern32": [(278, 252, 0.10, 0.10, 0.72, 8), (22, 98, 0.17, 0.24, 0.95, 10), (152, 128, 0.09, 0.26, 0.74, 5), (352, 368, 0.13, 0.36, 0.80, 4),
                  (275, 70, 0.015, 0.05, 0.98, 5)],
    "painter48": [(h, h + 22, 0.15, 0.28, 0.90, 5) for h in (18, 55, 92, 138, 188, 242, 292, 340)] + [(262, 80, 0.015, 0.07, 0.98, 8)],
    "meadow48": [(158, 122, 0.13, 0.18, 0.90, 6), (138, 104, 0.15, 0.30, 0.93, 6), (182, 160, 0.09, 0.22, 0.84, 6), (42, 86, 0.09, 0.20, 0.86, 6),
                 (252, 214, 0.10, 0.34, 0.95, 8), (350, 362, 0.17, 0.52, 0.80, 2), (305, 318, 0.14, 0.48, 0.78, 2), (28, 44, 0.17, 0.58, 0.82, 2),
                 (88, 100, 0.15, 0.70, 0.96, 4), (250, 95, 0.018, 0.10, 0.97, 6)],
    "harbor48": [(262, 212, 0.12, 0.12, 0.92, 10), (202, 176, 0.09, 0.24, 0.86, 6), (304, 280, 0.10, 0.22, 0.78, 6), (16, 72, 0.16, 0.30, 0.92, 8),
                 (76, 98, 0.14, 0.66, 0.97, 4), (44, 72, 0.07, 0.18, 0.74, 6), (245, 225, 0.02, 0.08, 0.98, 8)],
    "spectrum64": [(h, h + 16, 0.17, 0.30, 0.88, 4) for h in range(10, 370, 30)] + [(262, 88, 0.012, 0.04, 0.99, 16)],
}


def hex_from_oklch(light, chroma, hue):
    """OKLCH to an sRGB hex colour (Bjorn Ottosson's Oklab). Chroma shrinks until the colour fits the gamut."""
    while True:
        a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
        l, m, s = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3, (light - 0.1055613458 * a - 0.0638541728 * b) ** 3, (light - 0.0894841775 * a - 1.2914855480 * b) ** 3
        linear = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
        if all(-0.0005 <= v <= 1.0005 for v in linear) or chroma < 0.002:
            break
        chroma *= 0.96
    encode = lambda v: 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055   # noqa: E731
    return "#" + "".join(f"{round(255 * min(1, max(0, encode(min(1, max(0, v)))))):02x}" for v in linear)


def ramp(hue_from, hue_to, chroma, light_from, light_to, count):
    out = []
    for i in range(count):
        t = i / (count - 1) if count > 1 else 0.5
        arc = math.sin(math.pi * (0.18 + 0.64 * t)) if count > 1 else 1
        out.append(hex_from_oklch(light_from + (light_to - light_from) * t, chroma * arc, hue_from + (hue_to - hue_from) * t))
    return out


def build():
    palettes = {name: ["#" + c for c in colours.split()] for name, colours in CLASSIC.items()}
    for name, ramps in THEMES.items():
        colours = [c for r in ramps for c in ramp(*r)]
        assert len(set(colours)) == len(colours), name
        palettes[name] = colours
    return palettes


if __name__ == "__main__":
    palettes = build()
    target = Path(__file__).resolve().parents[1] / "pixel_cat" / "palettes.json"
    target.write_text(json.dumps(palettes, indent=1) + "\n")
    print({name: len(colours) for name, colours in palettes.items()})
    if len(sys.argv) > 1:
        from PIL import Image, ImageDraw
        sheet = Image.new("RGB", (64 * 20 + 120, len(palettes) * 34 + 6), "#202028")
        draw = ImageDraw.Draw(sheet)
        for row, (name, colours) in enumerate(palettes.items()):
            draw.text((6, row * 34 + 14), name, fill="#ffffff")
            for i, colour in enumerate(colours):
                draw.rectangle([120 + i * 20, row * 34 + 6, 120 + i * 20 + 17, row * 34 + 31], fill=colour)
        sheet.save(sys.argv[1])
