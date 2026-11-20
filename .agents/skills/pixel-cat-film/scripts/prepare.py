"""Turn a four-panel storyboard and a transparent hero into five native pixel-cat assets."""

import argparse
import json
import subprocess
import tempfile
from math import gcd
from pathlib import Path

from PIL import Image


def convert(source, destination, edge, colors):
    command = [
        "uv",
        "run",
        "pixel-cat",
        "convert",
        str(source),
        "--mode",
        "grid",
        "--sampling",
        "crisp",
        "--colors",
        str(colors),
        "--grid",
        str(edge),
        "--scale",
        "1",
        "-o",
        str(destination),
    ]
    subprocess.run(command, check=True)


def prepare(plates, sprite, output, width, height, colors, sprite_edge):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work, Image.open(plates) as page:
        for i in range(4):
            x, y = i % 2, i // 2
            panel = page.crop(
                (
                    x * page.width // 2 + 3,
                    y * page.height // 2 + 3,
                    (x + 1) * page.width // 2 - 3,
                    (y + 1) * page.height // 2 - 3,
                )
            )
            # Crop to an integer number of equal aspect units so the CLI preserves the exact grid.
            unit = gcd(width, height)
            a, b = width // unit, height // unit
            units = min(panel.width // a, panel.height // b)
            w, h = a * units, b * units
            dx, dy = (panel.width - w) // 2, (panel.height - h) // 2
            panel = panel.crop((dx, dy, dx + w, dy + h))
            if panel.width < width:
                panel = panel.resize((width, height), Image.Resampling.NEAREST)
            source = Path(work) / f"panel-{i}.png"
            panel.save(source)
            convert(source, output / f"shot-{i + 1}.png", max(width, height), colors)
        with Image.open(sprite) as image:
            image = image.convert("RGBA")
            alpha = image.getchannel("A")
            if alpha.getextrema()[0] == 255:
                raise ValueError("The hero needs a transparent background.")
            bounds = alpha.point(lambda a: 255 if a >= 96 else 0).getbbox()
            if bounds is None:
                raise ValueError("The hero is empty.")
            image.crop(bounds).save(Path(work) / "hero.png")
        convert(Path(work) / "hero.png", output / "hero.png", sprite_edge, colors)
    return {"directory": str(output), "native_size": [width, height]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plates", type=Path)
    parser.add_argument("sprite", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--size", nargs=2, type=int, default=(240, 360), metavar=("WIDTH", "HEIGHT")
    )
    parser.add_argument("--colors", type=int, default=64)
    parser.add_argument("--sprite-edge", type=int, default=320)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.plates,
                args.sprite,
                args.output,
                *args.size,
                args.colors,
                args.sprite_edge,
            )
        )
    )
