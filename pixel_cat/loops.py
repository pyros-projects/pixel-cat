"""Bring a still picture to life as a seamless loop: drifting clouds, rippling water, twinkling lights,
swaying leaves, rising smoke, and small things that were not in the picture: fireflies, falling leaves, birds.

The first five only move cells that are already there, so the palette stays as it is. The last three draw
a few cells in colours of their own. A loop closes exactly:
motions that come back on their own (ripples, sway, twinkle) run a whole number of cycles, and motions that
keep going (drift, rise) fade into their own beginning.
"""

import math

import numpy as np

from . import stills
from .edits import rgb
from .options import keys

KINDS = ("clouds", "water", "twinkle", "sway", "rise", "fireflies", "leaves", "birds")
PARTICLES = {"fireflies": ["#ffe680"], "leaves": ["#c9682c", "#e0a13a", "#8a4a22"], "birds": ["#1b1e27"]}


def waterline(image, low, high):
    """The row where the picture changes most from one row to the next, between two heights (as fractions)."""
    h = image.shape[0]
    light = stills.luma(image[..., :3].astype(np.float64))
    jump = (np.abs(light[1:] - light[:-1]) > 24).mean(axis=1)
    rows = np.arange(1, h)
    inside = (rows >= low * h) & (rows <= high * h)
    return int(rows[inside][jump[inside].argmax()]) if inside.any() else int(h * (low + high) / 2)


def sky(image):
    """A guess at the sky: in every column, from the top edge down to the first cell that is clearly darker
    than the sky is at the top. That is where a ridge, a roof or a tree begins."""
    h = image.shape[0]
    light = stills.luma(image[..., :3].astype(np.float64))
    usual = np.median(light[:max(1, h // 10)])
    darker = light < usual - 40
    skyline = np.where(darker.any(axis=0), darker.argmax(axis=0), h)
    return np.arange(h)[:, None] < np.minimum(skyline, waterline(image, 0.3, 0.9))[None, :]


def area_mask(area, image, kind):
    """A region as a mask of cells. `area` is "auto", a box [left, top, right, bottom] or a polygon of
    [x, y] points; numbers are fractions of the picture, so they hold for any grid size. A region painted in
    the studio comes as {"cells": [[x, y], ...]} instead, in cells of this very picture."""
    h, w = image.shape[:2]
    if isinstance(area, dict):
        mask = np.zeros((h, w), dtype=bool)
        for x, y in area.get("cells", []):
            if 0 <= x < w and 0 <= y < h:
                mask[int(y), int(x)] = True
        return mask
    if area == "auto":
        mask = np.zeros((h, w), dtype=bool)
        if kind in ("clouds", "twinkle"):
            mask = sky(image)
        elif kind == "water":
            mask[waterline(image, 0.3, 0.9):] = True
        elif kind == "birds":
            mask = sky(image)
        else:
            mask[:] = True
        return mask
    points = area if isinstance(area[0], (list, tuple)) else [(area[0], area[1]), (area[2], area[1]), (area[2], area[3]), (area[0], area[3])]
    points = [(x * w, y * h) for x, y in points]
    xs, ys = (np.arange(w) + 0.5)[None, :], (np.arange(h) + 0.5)[:, None]
    mask = np.zeros((h, w), dtype=bool)                           # a cell is inside when its centre is
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if y1 != y2:
            mask ^= ((y1 > ys) != (y2 > ys)) & (xs < (x2 - x1) * (ys - y1) / (y2 - y1) + x1)
    return mask


def _runs(mask):
    """For every marked cell: its row, its column, and the start and length of the unbroken run it lies in."""
    ys, xs = np.nonzero(mask)
    first = np.ones(len(xs), dtype=bool)
    first[1:] = (ys[1:] != ys[:-1]) | (xs[1:] != xs[:-1] + 1)
    run = np.cumsum(first) - 1
    return ys, xs, xs[first][run], np.bincount(run)[run]


def _shift_rows(image, mask, shift):
    """Slide the masked cells sideways by `shift` (one number per row). What slides in from beyond the end
    of a run is its mirror image, which joins on without a seam. Each unbroken run of a row moves on its
    own, so nothing jumps across a gap in a painted area."""
    out = image.copy()
    ys, xs, start, n = _runs(mask)
    at = (xs - start - np.asarray(shift)[ys].astype(np.int64)) % (2 * n)
    out[ys, xs] = image[ys, start + np.where(at < n, at, 2 * n - 1 - at)]
    return out


def _shift_columns(image, mask, shift):
    return _shift_rows(image.transpose(1, 0, 2), mask.T, shift).transpose(1, 0, 2)


def _drift(image, mask, frames, per_frame, mover):
    """A motion that never turns back, closed into a loop: over the last third, the take that started one
    loop earlier fades in. The mix is snapped back to the picture's own colours."""
    h, w = image.shape[:2]
    span = h if mover is _shift_columns else w
    palette = np.unique(image[..., :3][mask], axis=0)
    out = []
    for i in range(frames):
        now = mover(image, mask, np.full(span, round(per_frame * i)))
        share = (i / frames - 0.67) / 0.33
        if share > 0:
            earlier = mover(image, mask, np.full(span, round(per_frame * (i - frames))))
            mixed = np.rint(now[..., :3][mask] * (1 - share) + earlier[..., :3][mask] * share)
            now[..., :3][mask] = palette[stills.nearest(mixed, palette)]
        out.append(now)
    return out


def validate(regions):
    """Reject a region that cannot be carried out, with a plain reason."""
    for region in regions:
        keys(region, {"kind", "area", "amount", "color", "colors", "wind", "size"}, "region")
        if region.get("kind") not in KINDS:
            raise ValueError("A region's kind is one of " + ", ".join(KINDS))
        area = region.get("area", "auto")
        if isinstance(area, dict):
            if not isinstance(area.get("cells"), list):
                raise ValueError("A painted area is {\"cells\": [[x, y], ...]}")
        else:
            flat = [v for point in area for v in (point if isinstance(point, list) else [point])] if isinstance(area, list) else None
            if area != "auto" and (flat is None or len(flat) < 4 or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in flat)):
                raise ValueError('A region\'s area is "auto", a box [left, top, right, bottom] or a polygon of [x, y] points, in fractions of the picture')
        if not 0 < region.get("amount", 1) <= 8:
            raise ValueError("A region's amount is a number up to 8")
        for colour in (region.get("colors") or []) + ([region["color"]] if region.get("color") else []):
            rgb(colour)
        if region.get("wind", "right") not in ("left", "right", "none"):
            raise ValueError("wind is left, right or none")
        if region.get("size", 1) not in (1, 2, 3):
            raise ValueError("size is 1, 2 or 3")


def particles(kind, image, mask, count, region, rng):
    """Frames in which small things move through a region: one list of (x, y, colour) per frame.

    Each thing runs a whole number of rounds per loop (a path, a fall through the region, a crossing), so
    the last frame leads back into the first. Only cells inside the region are drawn.
    """
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    top, bottom, left, right = ys.min(), ys.max(), xs.min(), xs.max()
    tall, wide = bottom - top + 1, right - left + 1
    colours = [[int(c[i:i + 2], 16) for i in (1, 3, 5)] + [255] for c in (region.get("colors") or ([region["color"]] if region.get("color") else PARTICLES[kind]))]
    amount = region.get("amount", 1.0)
    out = [[] for _ in range(count)]
    if kind == "fireflies":
        flies = max(1, min(60, round(len(xs) / 600 * amount)))
        glow = {1: [(0, 0)], 2: [(0, 0), (1, 0), (0, 1), (1, 1)], 3: [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]}[region.get("size", 1)]
        for n in range(flies):
            home = rng.integers(len(xs))
            reach, rounds, phase = rng.uniform(1, 4, 2), rng.integers(1, 3, 2), rng.random(2)
            blinks, lit = int(rng.integers(1, 4)), (n + rng.random()) / flies      # spread out, so they do not blink as one
            for i in range(count):
                t = i / count
                if (blinks * t + lit) % 1 < 0.6:
                    x = round(xs[home] + reach[0] * math.sin(math.tau * (rounds[0] * t + phase[0])))
                    y = round(ys[home] + reach[1] * math.sin(math.tau * (rounds[1] * t + phase[1])))
                    out[i].extend((x + dx, y + dy, colours[n % len(colours)]) for dx, dy in glow)
    elif kind == "leaves":
        wind = {"left": -1, "right": 1}.get(region.get("wind", "right"), 0)
        for n in range(max(1, min(40, round(len(xs) / 900 * amount)))):
            start, falls, sway, phase = (rng.uniform(0, wide), rng.uniform(0, tall)), int(rng.integers(1, 3)), rng.uniform(0.5, 2), rng.random()
            for i in range(count):
                t = i / count
                x = left + (start[0] + wind * wide * t + sway * math.sin(math.tau * (2 * t + phase))) % wide
                y = top + (start[1] + falls * tall * t) % tall
                tumbled = (4 * t + phase) % 1 < 0.5
                for dx, dy in ((0, 0), (1, 1) if tumbled else (1, 0)):
                    out[i].append((int(x) + dx, int(y) + dy, colours[n % len(colours)]))
    else:                                                          # birds: small wings that cross the region
        for n in range(max(1, min(6, round(2 * amount)))):
            start, row, phase = rng.uniform(0, wide + 8), top + rng.uniform(0.1, 0.6) * tall, rng.random()
            for i in range(count):
                t = i / count
                x = left - 4 + (start + (wide + 8) * t) % (wide + 8)
                up = (4 * t + phase) % 1 < 0.5
                bob = round(math.sin(math.tau * (t + phase)))
                for dx, dy in ((-1, -1 if up else 0), (0, 0), (1, -1 if up else 0)):
                    out[i].append((int(x) + dx, int(row) + dy + bob, colours[n % len(colours)]))
    return [[(x, y, c) for x, y, c in frame if 0 <= x < w and 0 <= y < h and mask[y, x]] for frame in out]


def animate(image, spec):
    """Return the frames of the loop and their durations in milliseconds."""
    seconds, fps = spec.get("seconds", 4), spec.get("fps", 8)
    count = max(2, round(seconds * fps))
    h, w = image.shape[:2]
    frames = [image.copy() for _ in range(count)]
    rng = np.random.default_rng(spec.get("seed", 1))
    light = stills.luma(image[..., :3].astype(np.float64))
    for region in spec["regions"]:
        kind, amount = region["kind"], region.get("amount", 1.0)
        mask = area_mask(region.get("area", "auto"), image, kind) & (image[..., 3] >= 128)
        if not mask.any():
            continue
        if kind in PARTICLES:                                      # drawn on top of whatever the frame shows by then
            for frame, things in zip(frames, particles(kind, image, mask, count, region, rng)):
                for x, y, colour in things:
                    frame[y, x] = colour
            continue
        rows = np.flatnonzero(mask.any(axis=1))
        top, bottom = rows[0], rows[-1]
        depth = np.clip((np.arange(h) - top) / max(1, bottom - top), 0, 1)          # 0 at the top of the region
        if kind == "clouds":                                       # `amount` cells per second, to the right
            moved = _drift(image, mask, count, 4 * amount / fps, _shift_rows)
        elif kind == "rise":                                       # smoke, steam, sparks: upwards
            moved = _drift(image, mask, count, -4 * amount / fps, _shift_columns)
        elif kind == "water":                                      # each row slides a little, more towards the front
            cycles = max(1, round(seconds * 0.6))
            moved = [_shift_rows(image, mask, np.rint(amount * (0.5 + 1.5 * depth) * np.sin(math.tau * (cycles * i / count - np.arange(h) / (3 + 5 * depth)))))
                     for i in range(count)]
        elif kind == "sway":                                       # leaves and grass: the top moves, the foot stays
            cycles = max(1, round(seconds * 0.4))
            moved = [_shift_rows(image, mask, np.rint(1.5 * amount * (1 - depth) * np.sin(math.tau * (cycles * i / count) + np.arange(h) / 9)))
                     for i in range(count)]
        else:                                                      # twinkle: small bright cells go dark for a moment
            around = sum(np.roll(light, s, axis=(0, 1)) for s in ((0, 1), (0, -1), (1, 0), (-1, 0))) / 4
            ys, xs = np.nonzero(mask & (light - around > 30))
            darker = np.stack([np.roll(image, s, axis=(0, 1)) for s in ((0, 1), (0, -1), (1, 0), (-1, 0))])
            pick = np.stack([np.roll(light, s, axis=(0, 1)) for s in ((0, 1), (0, -1), (1, 0), (-1, 0))]).argmin(axis=0)
            phase, cycles = rng.random(len(ys)), rng.integers(1, 3, len(ys))
            moved = []
            for i in range(count):
                frame = image.copy()
                dim = ((cycles * i / count + phase) % 1) < 0.2 * amount
                frame[ys[dim], xs[dim]] = darker[pick[ys[dim], xs[dim]], ys[dim], xs[dim]]
                moved.append(frame)
        for frame, new in zip(frames, moved):
            frame[mask] = new[mask]
    return frames, [round(1000 / fps)] * count
