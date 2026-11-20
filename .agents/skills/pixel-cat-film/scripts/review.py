"""Look at a film before anyone else does.

    uv run python review.py story.json out_dir

Writes enlarged contact sheets for every shot and every moving part, and prints findings as JSON. Motion
hides mistakes that a single drawing shows at once: a prop that leaves a ghost behind, a part that starts
a cell off its painted place, a jump between two drawings. Open every sheet this writes. Exit code 1 means
a finding has to be fixed.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import render


def tile(image, box, scale, label):
    x0, y0, x1, y1 = box
    out = image.convert("RGB").crop(box).resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.Resampling.NEAREST)
    pen = ImageDraw.Draw(out)
    pen.rectangle([0, 0, 7 * len(label) + 6, 13], fill=(0, 0, 0))
    pen.text((3, 1), label, fill=(255, 255, 0))
    return out


def sheet(tiles, columns, path):
    w, h = tiles[0].size
    rows = -(-len(tiles) // columns)
    out = Image.new("RGB", (columns * (w + 4), rows * (h + 4)), (17, 17, 17))
    for i, t in enumerate(tiles):
        out.paste(t, ((i % columns) * (w + 4), (i // columns) * (h + 4)))
    out.save(path)
    return str(path)


def spread(count, most):
    """Up to `most` drawing numbers, evenly spread, first and last included."""
    return sorted({round(i * (count - 1) / (most - 1)) for i in range(most)}) if count > most else list(range(count))


def travel_box(art, cfg, count, fps, size, room=10):
    """The box a part is drawn in over the whole shot, with a little room around it."""
    boxes = []
    for i in range(count):
        placed, (x, y) = render.scale_layer((art, cfg), i / fps)
        seen = placed.getchannel("A").getbbox()                    # where the part has cells, not its whole tile
        if seen:
            boxes.append((x + seen[0], y + seen[1], x + seen[2], y + seen[3]))
    if not boxes:
        return 0, 0, *size
    w, h = size
    return (max(0, min(b[0] for b in boxes) - room), max(0, min(b[1] for b in boxes) - room),
            min(w, max(b[2] for b in boxes) + room), min(h, max(b[3] for b in boxes) + room))


def review(story_path, out):
    story = json.loads(story_path.read_text())
    size, fps = tuple(story["size"]), story["fps"]
    out.mkdir(parents=True, exist_ok=True)
    sheets, findings = [], []
    for number, shot in enumerate(story["shots"], 1):
        plain = {k: v for k, v in shot.items() if k not in ("effects", "camera")}
        try:
            frames = list(render.images(story_path.parent, shot, size, fps, story["seed"] + number - 1))
            bare = list(render.images(story_path.parent, plain, size, fps, story["seed"] + number - 1))
        except ValueError as error:
            findings.append({"shot": number, "error": str(error)})
            continue
        with Image.open(story_path.parent / shot["plate"]) as file:
            plate = file.convert("RGB")
        scale = max(1, 360 // max(size))
        picks = spread(len(frames), 12)
        sheets.append(sheet([tile(frames[i], (0, 0, *size), scale, f"#{i} {i / fps:.2f}s") for i in picks], 6, out / f"shot-{number}-drawings.png"))

        # Does the shot start as painted? Without effects, the first drawing should differ from the plate only where a part was placed on purpose.
        first = np.asarray(bare[0].convert("RGB"))
        changed = (first != np.asarray(plate)).any(axis=2)
        for index, layer in enumerate(shot.get("layers", []), 1):
            with_art = "cutout" in layer
            if with_art:
                lifted = plate.copy()
                cfg = render.painted_prop(lifted, layer, shot["plate"])
                art = cfg.pop("_art")
                polygon_cells = Image.new("L", size)
                ImageDraw.Draw(polygon_cells).polygon([tuple(p) for p in layer["cutout"]], fill=255)
                widened = int((np.asarray(art.getchannel("A")) > 0).sum() - (np.asarray(polygon_cells) > 0).sum())
            else:
                cfg = layer
                with Image.open(story_path.parent / layer["image"]) as file:
                    art = file.convert("RGBA")
            box = travel_box(art, cfg, len(frames), fps, size)
            close = max(1, min(6, 330 // max(box[2] - box[0], box[3] - box[1])))
            tiles = []
            if with_art:
                tiles += [tile(plate, box, close, "as painted"), tile(lifted, box, close, "prop lifted")]
                rest = int(changed[box[1]:box[3], box[0]:box[2]].sum())
                note = {"shot": number, "layer": index, "kind": "cutout", "outline widened by cells": widened,
                        "cells that differ from the painting in the first drawing": rest}
                if rest:
                    note["fix"] = "the first drawing should equal the painting: check the first pose and the pivot"
                findings.append(note)
            # the drawings where the part is most turned, most scaled and farthest from its start
            poses = [render.sample(layer["pose"], i / fps) for i in range(len(bare))]
            steps = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(poses, poses[1:])]
            turns = [abs(a[4] - b[4]) for a, b in zip(poses, poses[1:])]
            if steps and (max(steps) > max(size) / 6 or max(turns) > 25):
                findings.append({"shot": number, "layer": index, "kind": "fast move", "largest move between two drawings in cells": round(max(steps), 1),
                                 "largest turn between two drawings in degrees": round(max(turns), 1), "look at drawing": int(np.argmax(steps)) + 1,
                                 "note": "fine if it is meant to flash past; a mistake if it is meant to glide"})
            keys = {min(len(bare) - 1, round(p[0] * fps)) for p in layer["pose"]}
            moving = [i for i in range(len(bare)) if i in keys or (i and (steps[i - 1] > 0.01 or turns[i - 1] > 0.01))]
            chosen = [moving[i] for i in spread(len(moving), 16)] if moving else [0]
            tiles += [tile(bare[i], box, close, f"#{i} {i / fps:.2f}s {poses[i][4]:+.1f}deg") for i in chosen]
            sheets.append(sheet(tiles, 6, out / f"shot-{number}-part-{index}.png"))
        if "deform" in shot:
            cx, cy = shot["deform"]["center"]
            rx, ry = shot["deform"]["radius"]
            box = (max(0, int(cx - rx / 2)), max(0, int(cy - ry / 2)), min(size[0], int(cx + rx / 2)), min(size[1], int(cy + ry / 2)))
            close = max(1, min(4, 330 // max(box[2] - box[0], box[3] - box[1])))
            keys = sorted({min(len(bare) - 1, round(k[0] * fps)) for k in shot["deform"]["move"]})
            sheets.append(sheet([tile(plate, box, close, "as painted")] + [tile(bare[i], box, close, f"#{i} {i / fps:.2f}s") for i in keys], 6, out / f"shot-{number}-pull.png"))
            rest = int((np.asarray(bare[0].convert("RGB")) != np.asarray(plate)).any(axis=2).sum()) if shot["deform"]["move"][0][1:] == [0, 0] and not shot.get("layers") else 0
            if rest:
                findings.append({"shot": number, "kind": "pull", "cells that differ from the painting in the first drawing": rest})
    return {"story": str(story_path), "open every sheet": sheets, "findings": findings,
            "ask of every sheet": [
                "Does anything stay behind where a part used to be: a ghost, a rim, a loose crumb?",
                "Does the part stay attached where it should, and does it move the way the real thing would?",
                "Is the background behind a moved part believable?",
                "Details that can be counted (fingers, legs, wheels, windows): are they the right number, in every drawing?"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("story", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    report = review(args.story, args.out)
    print(json.dumps(report, indent=1))
    sys.exit(1 if any("error" in f or "fix" in f for f in report["findings"]) else 0)
