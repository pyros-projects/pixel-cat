"""Repair drawn lines: thin them to one cell, close small gaps, remove the halo beside them.

A "line" is a thin run of cells in the line colour: dark cells by default, or cells near a colour you name.
Broad dark areas are left alone; only what is at most `width` cells thick counts as a line.
"""

import numpy as np
from PIL import Image, ImageFilter

from . import stills

RING = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]      # clockwise from above: (dy, dx)


def _shift(a, dy, dx, fill=False):
    h, w = a.shape[:2]
    out = np.full_like(a, fill)
    out[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)] = a[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)]
    return out


def find(image, colour=None, tolerance=28, width=4, dark=70):
    """The cells that belong to lines: in the line colour, and part of something at most `width` cells thick."""
    solid = image[..., 3] >= 128
    if colour is None:
        inked = solid & (stills.luma(image[..., :3].astype(np.float64)) <= dark)
    else:
        inked = solid & (np.abs(image[..., :3].astype(np.int32) - np.array(colour)).max(axis=2) <= tolerance)
    size = width + 1 if width % 2 == 0 else width + 2                     # the smallest odd window wider than a line
    mask = Image.fromarray(inked.astype(np.uint8) * 255)
    broad = np.asarray(mask.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))) > 0
    return inked & ~broad


def skeleton(mask):
    """Peel a shape down to lines one cell wide without breaking it (Zhang and Suen's thinning, 1984)."""
    m = mask.copy()
    while True:
        peeled = False
        for second in (False, True):
            p = [_shift(m, dy, dx) for dy, dx in RING]                   # p[0] above, then clockwise
            around = sum(x.astype(np.int8) for x in p)
            turns = sum((~p[i] & p[(i + 1) % 8]).astype(np.int8) for i in range(8))
            if second:
                open_side = ~(p[0] & p[2] & p[6]) & ~(p[0] & p[4] & p[6])
            else:
                open_side = ~(p[0] & p[2] & p[4]) & ~(p[2] & p[4] & p[6])
            remove = m & (around >= 2) & (around <= 6) & (turns == 1) & open_side
            if remove.any():
                m &= ~remove
                peeled = True
        if not peeled:
            return m


def _fill_from_around(image, holes, forbidden):
    """Give each cell of `holes` the colour most common among its settled neighbours, from the rim inwards.
    Cells in `forbidden` (the lines themselves) never lend their colour."""
    out = image.copy()
    h, w = holes.shape
    code = stills.codes(out[..., :3])
    settled = ~holes & ~forbidden & (image[..., 3] >= 128)
    todo = holes.copy()
    while todo.any():
        near = np.zeros_like(todo)
        for dy, dx in RING:
            near |= _shift(settled, dy, dx)
        rim = todo & near
        if not rim.any():
            break
        chosen = []
        for y, x in zip(*np.nonzero(rim)):
            votes = {}
            for dy, dx in RING:
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and settled[ny, nx]:
                    votes[code[ny, nx]] = votes.get(code[ny, nx], 0) + (1 if dy and dx else 2)
            chosen.append((y, x, max(votes, key=lambda c: (votes[c], -c))))
        for y, x, winner in chosen:
            out[y, x] = (winner >> 16, winner >> 8 & 255, winner & 255, 255)
            code[y, x] = winner
        settled |= rim
        todo &= ~rim
    return out


def blobs(mask, limit):
    """The marked groups that are no longer than `limit` cells in any direction: dots and eyes, not lines."""
    h, w = mask.shape
    seen, small = np.zeros_like(mask), np.zeros_like(mask)
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
        if max(max(ys) - min(ys), max(xs) - min(xs)) < limit:
            small[ys, xs] = True
    return small


def thin(image, colour=None, tolerance=28, width=4, gap=0):
    """Thin every line to one cell and, with `gap`, close breaks of up to that many cells. Small dark shapes
    such as an eye or a dot are not lines and keep their shape."""
    lines = find(image, colour, tolerance, width)
    lines &= ~blobs(lines, max(6, 2 * width))
    if not lines.any():
        return image
    spine = skeleton(lines)
    out = _fill_from_around(image, lines & ~spine, lines)
    if gap:
        out = close_gaps(out, spine, gap)
    return out


def close_gaps(image, lines, gap):
    """Where a line ends and continues within `gap` cells straight ahead, draw the missing cells."""
    out = image.copy()
    h, w = lines.shape
    count = sum(_shift(lines, dy, dx).astype(np.int8) for dy, dx in RING)
    for y, x in zip(*np.nonzero(lines & (count == 1))):
        (ny, nx), = [(y + dy, x + dx) for dy, dx in RING if 0 <= y + dy < h and 0 <= x + dx < w and lines[y + dy, x + dx]]
        dy, dx = y - ny, x - nx                                           # the way the line was heading
        for reach in range(2, gap + 2):
            ty, tx = y + dy * reach, x + dx * reach
            if not (0 <= ty < h and 0 <= tx < w):
                break
            if lines[ty, tx]:
                for step in range(1, reach):
                    out[y + dy * step, x + dx * step] = image[y, x]
                break
    return out


def halo(image, strength="gentle", width=4):
    """Remove the thin rim of in-between colour that smooth scaling leaves beside a dark line.

    A cell next to a line goes when its colour lies between the line's colour and the area behind it, and
    when hardly any neighbour shares it. It takes the colour of the area behind.
    """
    slack = {"gentle": 26, "stronger": 44}.get(strength)
    if slack is None:
        raise ValueError("strength is gentle or stronger")
    lines = find(image, None, 28, width)
    if not lines.any():
        return image
    out = image.copy()
    h, w = lines.shape
    rgb, solid = image[..., :3].astype(np.float64), image[..., 3] >= 128
    code = stills.codes(image[..., :3])
    beside = solid & ~lines & (_shift(lines, 1, 0) | _shift(lines, -1, 0) | _shift(lines, 0, 1) | _shift(lines, 0, -1))
    for y, x in zip(*np.nonzero(beside)):
        ink, behind, same = [], {}, 0
        for dy, dx in RING:
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or not solid[ny, nx]:
                continue
            if lines[ny, nx]:
                ink.append(rgb[ny, nx])
            elif code[ny, nx] == code[y, x]:
                same += 1
            elif not beside[ny, nx]:
                behind[code[ny, nx]] = behind.get(code[ny, nx], 0) + 1
        if same > 2 or not behind or not ink:
            continue
        winner = max(behind, key=lambda c: (behind[c], -c))
        fill = np.array([winner >> 16, winner >> 8 & 255, winner & 255], dtype=np.float64)
        line = np.mean(ink, axis=0)
        span = line - fill
        if (span ** 2).sum() < 40 ** 2:
            continue
        share = float((rgb[y, x] - fill) @ span / (span @ span))
        off = np.abs(rgb[y, x] - (fill + share * span)).max()
        if 0.12 < share < 0.9 and off <= slack:
            out[y, x, :3] = fill
    return out
