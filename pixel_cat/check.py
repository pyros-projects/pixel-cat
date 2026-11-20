"""Look for trouble in an animation: flicker, stray fragments, holes, colours outside the palette.

Every finding is a hint for a person to look at, never a verdict. A star may be a one-cell fragment, and a
lamp may be meant to blink. The check says where to look; `hold`, `clean` or a pencil do the fixing.
"""

import numpy as np

from . import frames as media
from . import recommend, stills

KINDS = ("flicker", "fragments", "holes", "palette")
TINT = {"flicker": (255, 0, 220), "fragments": (255, 220, 0), "holes": (0, 230, 255), "palette": (255, 40, 40)}


def regions(mask):
    """Groups of touching marked cells (diagonals count), largest first, as (box, cell count)."""
    h, w = mask.shape
    seen, out = np.zeros_like(mask), []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        stack, cells = [(int(y), int(x))], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny in range(max(0, cy - 1), min(h, cy + 2)):
                for nx in range(max(0, cx - 1), min(w, cx + 2)):
                    if mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        ys, xs = [c[0] for c in cells], [c[1] for c in cells]
        out.append(([min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1], len(cells)))
    return sorted(out, key=lambda item: -item[1])


def inspect(images, palette=None, fragment=3):
    """Mark suspicious cells in every frame. Returns {kind: array of shape (frames, height, width)}.

    - flicker: a cell that differs from the frame before and the frame after, while those two agree. The
      animation is read as a loop. It needs at least three frames.
    - fragments: groups of at most `fragment` touching cells of one colour.
    - holes: an empty cell with colour on all four sides.
    - palette: a colour that is not in the given palette.
    """
    count, (h, w) = len(images), images[0].shape[:2]
    codes = np.stack([np.where(image[..., 3] >= 128, stills.codes(image[..., :3]), -1) for image in images])
    marks = {kind: np.zeros((count, h, w), dtype=bool) for kind in KINDS}
    if count >= 3:
        before, after = np.roll(codes, 1, axis=0), np.roll(codes, -1, axis=0)
        marks["flicker"] = (codes != before) & (codes != after) & (before == after)
    allowed = None if not palette else np.array([int(c[1:3], 16) << 16 | int(c[3:5], 16) << 8 | int(c[5:7], 16) for c in palette], dtype=np.int64)
    for f in range(count):
        solid = codes[f] >= 0
        for group in recommend.islands(codes[f], solid, fragment):
            marks["fragments"][f].ravel()[group] = True
        ringed = np.ones((h, w), dtype=bool)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ringed &= recommend._shift(solid, dy, dx, False)
        marks["holes"][f] = ~solid & ringed
        if allowed is not None:
            marks["palette"][f] = solid & ~np.isin(codes[f], allowed)
    return marks


def report(marks, most=12):
    """Counts per frame and in total, and for each kind the areas where it gathers, largest first. An area
    comes as a box [x, y, width, height] that `hold` and the studio's selection take as it is."""
    count = len(next(iter(marks.values())))
    per_frame = [{"frame": f, **{kind: int(marks[kind][f].sum()) for kind in KINDS}} for f in range(count)]
    areas = []
    for kind in KINDS:
        anywhere = marks[kind].any(axis=0)
        for box, cells in regions(anywhere)[:most]:
            x, y, w, h = box
            where = [f for f in range(count) if marks[kind][f, y:y + h, x:x + w].any()]
            areas.append({"kind": kind, "rect": box, "cells": cells, "frames": where})
    areas.sort(key=lambda a: -a["cells"] * len(a["frames"]))
    return {"total": {kind: int(marks[kind].sum()) for kind in KINDS}, "frames": per_frame, "areas": areas[:most * 2]}


def marked(images, marks, scale):
    """The frames, enlarged, with every finding tinted in the colour of its kind."""
    out = []
    for f, image in enumerate(images):
        shown = image.copy()
        shown[shown[..., 3] < 128] = (24, 26, 34, 255)
        shown[..., :3] = (shown[..., :3] * 0.45).astype(np.uint8)        # dim the picture so that the marks stand out
        for kind in KINDS:
            shown[marks[kind][f]] = (*TINT[kind], 255)
        out.append(media.enlarge(shown, scale))
    return out
