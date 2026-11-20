"""Put the same detail of several pictures side by side, enlarged, to compare candidates and count.

    uv run python closeup.py --box 0.2,0.3,0.8,0.8 -o detail.png candidate-1.png candidate-2.png candidate-3.png

The box is left, top, right, bottom in fractions of each picture, so it holds for pictures of any size.
Use it before choosing between paintings of the same shot: details that look right at thumbnail size often
are not, and counting only works on an enlarged view.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def closeup(paths, box, out, height=720):
    tiles = []
    for path in paths:
        with Image.open(path) as file:
            image = file.convert("RGB")
        w, h = image.size
        crop = image.crop((round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h)))
        scale = height / crop.height
        crop = crop.resize((round(crop.width * scale), height), Image.Resampling.NEAREST if scale >= 2 else Image.Resampling.LANCZOS)
        pen = ImageDraw.Draw(crop)
        pen.rectangle([0, 0, 7 * len(Path(path).name) + 6, 13], fill=(0, 0, 0))
        pen.text((3, 1), Path(path).name, fill=(255, 255, 0))
        tiles.append(crop)
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 6 * (len(tiles) - 1), height), (17, 17, 17))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + 6
    sheet.save(out)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pictures", nargs="+", type=Path)
    parser.add_argument("--box", required=True, help="left,top,right,bottom in fractions of the picture")
    parser.add_argument("-o", "--out", required=True, type=Path)
    args = parser.parse_args()
    box = [float(v) for v in args.box.split(",")]
    if len(box) != 4 or not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1:
        parser.error("--box takes left,top,right,bottom as fractions between 0 and 1")
    print(closeup(args.pictures, box, args.out))
