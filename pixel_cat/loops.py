"""Bring a still picture to life as a seamless loop: drifting clouds, rippling water, twinkling lights,
swaying leaves, rising smoke.

Every effect only moves cells that are already there, so the palette stays as it is. A loop closes exactly:
motions that come back on their own (ripples, sway, twinkle) run a whole number of cycles, and motions that
keep going (drift, rise) fade into their own beginning.
"""

import math

import numpy as np

from . import stills

KINDS = ("clouds", "water", "twinkle", "sway", "rise")


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
    [x, y] points; numbers are fractions of the picture, so they hold for any grid size."""
    h, w = image.shape[:2]
    if area == "auto":
        mask = np.zeros((h, w), dtype=bool)
        if kind in ("clouds", "twinkle"):
            mask = sky(image)
        elif kind == "water":
            mask[waterline(image, 0.3, 0.9):] = True
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


def _shift_rows(image, mask, shift):
    """Slide the masked cells sideways by `shift` (one number per row). What slides in from beyond the end
    of a row's span is its mirror image, which joins on without a seam."""
    out = image.copy()
    for y in np.flatnonzero(mask.any(axis=1)):
        xs = np.flatnonzero(mask[y])
        n = len(xs)
        at = (np.arange(n) - int(shift[y])) % (2 * n)
        out[y, xs] = image[y, xs[np.where(at < n, at, 2 * n - 1 - at)]]
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
