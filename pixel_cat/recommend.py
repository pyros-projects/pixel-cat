"""The recommended mode: read the picture, then choose a grid, a sampler and a colour count for it.

Flat cartoon art gets a small grid and a sampler that never blends outline and fill. Photographs and
paintings get a larger grid and averaged cells. A picture that already is pixel art, perhaps enlarged,
gets its own grid back, pixel for pixel.
"""

import math

import numpy as np

from . import stills
from .palettes import oklab

COLOUR_STEPS = (8, 12, 16, 24, 32, 48, 64)
RING = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]     # (dy, dx)


def measure(image):
    """What kind of picture is this? Shares are over pairs of neighbouring opaque pixels."""
    h, w = image.shape[:2]
    rgb, solid = image[..., :3].astype(np.int32), image[..., 3] >= 128
    light = stills.luma(rgb)
    pairs = flat = hard = dark = 0
    jumps = []                                                    # hard steps per column border, per row border
    for across in (True, False):
        a, b = (np.s_[:, 1:], np.s_[:, :-1]) if across else (np.s_[1:], np.s_[:-1])
        both = solid[a] & solid[b]
        step = np.abs(rgb[a] - rgb[b]).max(axis=2)
        edge = both & (step > 50)
        pairs += int(both.sum())
        flat += int((both & (step <= 4)).sum())
        hard += int(edge.sum())
        dark += int((edge & (np.minimum(light[a], light[b]) < 70)).sum())
        jumps.append(edge.sum(axis=0 if across else 1))

    def on_a_grid(steps, n):
        """Do nearly all hard steps fall on multiples of n, on at least three different lines?"""
        aligned = steps[n - 1::n]
        return steps.sum() > 0 and aligned.sum() / steps.sum() > 0.98 and np.count_nonzero(aligned) >= 3

    block = 1
    for n in range(2, 25):
        if w % n == 0 and h % n == 0 and on_a_grid(jumps[0], n) and on_a_grid(jumps[1], n):
            block = n
    colours = len(np.unique(stills.codes(rgb[solid]))) if solid.any() else 0
    soft_alpha = bool(((image[..., 3] > 0) & (image[..., 3] < 255)).any())
    return {"flat": flat / pairs if pairs else 1.0, "edges": hard / pairs if pairs else 0.0,
            "ink": dark / hard if hard else 0.0, "block": block,
            "pixel_art": colours <= 256 and not soft_alpha and (block > 1 or max(w, h) <= 256)}


def cut_alpha(image):
    """Pixel art has no half-transparent pixels: each one becomes solid or empty."""
    empty = image[..., 3] < 128
    image[empty] = 0
    image[~empty, 3] = 255
    return image


def sample(image, plan):
    out = stills.SAMPLERS[plan["sampling"]](image, plan["width"], plan["height"])
    return out if plan["keep_pixels"] else cut_alpha(out)


def colour_error(before, after):
    """How far a colour reduction moved the opaque pixels: the brightness-weighted root mean square, and
    the share of pixels that ended up visibly wrong."""
    solid = before[..., 3] >= 128
    if not solid.any():
        return 0.0, 0.0
    step = ((before[..., :3][solid].astype(np.float64) - after[..., :3][solid]) ** 2) @ np.array([0.299, 0.587, 0.114])
    return float(math.sqrt(step.mean())), float((step > 40 ** 2).mean())


def plan(images, contrast=False):
    """Choose the settings for one picture, or for all frames of an animation together."""
    facts = [measure(image) for image in images]
    flat, edges, ink = min(f["flat"] for f in facts), max(f["edges"] for f in facts), max(f["ink"] for f in facts)
    keep = all(f["pixel_art"] for f in facts) and len({f["block"] for f in facts}) == 1
    h, w = images[0].shape[:2]
    longest = max(w, h)
    limit = longest / facts[0]["block"] if keep else 128 if flat > 0.88 and edges < 0.08 else 192 if flat > 0.62 else 256
    scale = min(1, limit / longest)
    chosen = {"width": max(1, stills.half_up(w * scale)), "height": max(1, stills.half_up(h * scale)),
              "sampling": "exact" if keep else ("ink" if ink > 0.4 else "crisp") if flat > 0.62 else "area",
              "colors": 64, "cleanup": "gentle", "keep_pixels": keep, "contrast": contrast}
    if contrast:
        chosen.update(keep_pixels=False, sampling="area", cleanup="shapes", palette=shape_palette(images))
        chosen["colors"] = len(chosen["palette"])
        return chosen
    sampled = [sample(image, chosen) for image in images]
    if keep:
        colours, _, see_through = stills.histogram([(image, 1) for image in sampled])
        chosen.update(colors=min(256, max(2, len(colours) + int(see_through))), cleanup="off")
        return chosen
    pool = np.concatenate([image.reshape(-1, 4)[::max(1, math.ceil(image.shape[0] * image.shape[1] / 4096))] for image in sampled])[None]
    tolerance = 7 if flat > 0.62 else 11
    for count in COLOUR_STEPS:
        trial = pool.copy()
        stills.quantize(trial, count)
        chosen["colors"] = count
        drift, wrong = colour_error(pool, trial)
        if drift <= tolerance and wrong <= 0.004:
            break
    preview = sampled[0].copy()
    stills.quantize(preview, chosen["colors"])
    if not clean_specks(preview.copy()):
        chosen["cleanup"] = "off"
    return chosen


def finish(out, chosen, weights=None):
    """Bring sampled frames to their final colours and clean them. Frames of an animation share one palette."""
    if chosen["contrast"]:
        out = [soften(image) for image in out]
        for image in out:
            stills.apply_palette(image, chosen["palette"])
            merge_flecks(image)
        return out
    found, counts, see_through = stills.histogram(list(zip(out, weights or [1] * len(out))))
    slots = max(1, chosen["colors"] - int(see_through))
    if len(found) > slots:
        shared = stills.median_cut(found, counts, slots)
        for image in out:
            stills.apply_palette(image, shared)
    if chosen["cleanup"] == "gentle":
        for image in out:
            clean_specks(image)
    return out


# ---------------------------------------------------------------- small islands


def _shift(a, dy, dx, fill):
    """out[y, x] = a[y + dy, x + dx], with `fill` beyond the edge."""
    h, w = a.shape
    out = np.full_like(a, fill)
    out[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)] = a[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)]
    return out


def islands(code, inside, limit):
    """Groups of touching pixels (diagonals count) of one colour, with at most `limit` pixels.

    Labels spread from pixel to pixel `limit` times, which is enough to settle every group that small. A group
    that still borders the same colour under another label belongs to something larger and is left out.
    """
    h, w = code.shape
    label = np.where(inside, np.arange(h * w).reshape(h, w), -1)
    code = np.where(inside, code, -1)
    same = [(_shift(code, dy, dx, -2) == code) & inside for dy, dx in RING]
    for _ in range(limit):
        spread = label
        for (dy, dx), mask in zip(RING, same):
            spread = np.where(mask, np.minimum(spread, _shift(label, dy, dx, h * w)), spread)
        if (spread == label).all():
            break
        label = spread
    unsettled = np.zeros((h, w), dtype=bool)
    for (dy, dx), mask in zip(RING, same):
        unsettled |= mask & (_shift(label, dy, dx, -3) != label)
    pixels = np.flatnonzero(inside)
    order = pixels[np.argsort(label.ravel()[pixels], kind="stable")]
    names, starts, sizes = np.unique(label.ravel()[order], return_index=True, return_counts=True)
    spoiled = set(label[unsettled].tolist())
    return [order[s:s + n] for name, s, n in zip(names, starts, sizes) if n <= limit and name not in spoiled]


def _surroundings(group, code, opaque, w, h):
    """Votes of the colours around a group: side neighbours count double. None if it touches an edge or a gap."""
    members, votes = set(group.tolist()), {}
    for p in group:
        y, x = divmod(int(p), w)
        if x == 0 or y == 0 or x == w - 1 or y == h - 1:
            return None
        for dy, dx in RING:
            q = (y + dy) * w + x + dx
            if not opaque[q]:
                return None
            if q not in members:
                votes[code[q]] = votes.get(code[q], 0) + (1 if dy and dx else 2)
    return votes


def _merge(image, minimum_share, limit, inside, allow):
    """Recolour small enclosed groups to the colour that dominates their border. Returns the pixels changed."""
    h, w = image.shape[:2]
    code, opaque = stills.codes(image[..., :3]), inside.ravel()
    flat_code, changed = code.ravel(), 0
    target = image.reshape(-1, 4)
    for group in islands(code, inside, limit):
        votes = _surroundings(group, flat_code, opaque, w, h)
        if not votes:
            continue
        ys, xs = np.divmod(group, w)
        if len(group) >= 3 and max(np.ptp(ys), np.ptp(xs)) >= len(group) - 1:
            continue                                              # a thin line is a drawn detail
        winner = min(votes, key=lambda c: (-votes[c], c))
        if votes[winner] / sum(votes.values()) < minimum_share or not allow(int(flat_code[group[0]]), int(winner)):
            continue
        target[group, :3] = [winner >> 16, winner >> 8 & 255, winner & 255]
        changed += len(group)
    return changed


def clean_specks(image):
    """Remove one- and two-pixel specks whose colour is a near miss of what surrounds them.

    A speck goes only when one colour holds most of its border and the two colours look alike, so an eye, a
    highlight or a star stays. Very dark and very light specks need to be closer still.
    """
    def alike(here, there):
        a, b = oklab([[here >> 16, here >> 8 & 255, here & 255], [there >> 16, there >> 8 & 255, there & 255]])
        distance = float(np.linalg.norm(a - b))
        return distance <= (0.05 if a[0] < 0.3 or a[0] > 0.93 else 0.07)

    return _merge(image, 0.7, 2, image[..., 3] == 255, alike)


# ---------------------------------------------------------------- contrast shapes


def shape_palette(images):
    """A handful of colours that carry the picture's big shapes: what remains when you squint.

    Small averaged copies are clustered in Oklab with two to six centres. The smallest count that explains
    nine tenths of the colour variation and leaves hardly any pixel far from a centre wins; each centre is
    then replaced by the real colour nearest to it.
    """
    points = []
    for image in images:
        h, w = image.shape[:2]
        small = stills.sample_area(image, *stills.fit(w, h, 64))
        points.append(small[..., :3][small[..., 3] >= 128])
    rgb = np.concatenate(points)
    if not len(rgb):
        return np.zeros((1, 3), dtype=np.int64)
    lab = oklab(rgb)
    spread = ((lab - lab.mean(axis=0)) ** 2).sum(axis=1).mean()
    for count in range(1, 7):
        centres = [lab[0]]
        while len(centres) < count:                               # start far apart
            gap = ((lab[:, None, :] - np.array(centres)[None]) ** 2).sum(axis=2).min(axis=1)
            if gap.max() < 1e-10:
                break
            centres.append(lab[gap.argmax()])
        centres = np.array(centres)
        for _ in range(12):
            owner = ((lab[:, None, :] - centres[None]) ** 2).sum(axis=2).argmin(axis=1)
            centres = np.array([lab[owner == k].mean(axis=0) if (owner == k).any() else centres[k] for k in range(len(centres))])
        gap = ((lab[:, None, :] - centres[None]) ** 2).sum(axis=2).min(axis=1)
        if gap.mean() <= max(0.0009, spread * 0.1) and (gap > 0.12 ** 2).mean() <= 0.05:
            break
    chosen = {tuple(rgb[((lab - centre) ** 2).sum(axis=1).argmin()]) for centre in centres}
    return np.array(sorted(chosen, key=lambda c: oklab([c])[0][0]), dtype=np.int64)


def soften(image):
    """Blur away texture but stop at edges: a neighbour counts less the farther and the more different it is."""
    h, w = image.shape[:2]
    radius = max(1, min(3, stills.half_up(max(w, h) / 96)))
    rgb, solid = image[..., :3].astype(np.float64), image[..., 3] >= 128
    total, weight = np.zeros((h, w, 3)), np.zeros((h, w))
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            there = np.stack([_shift(rgb[..., c], dy, dx, 0.0) for c in range(3)], axis=2)
            present = _shift(solid, dy, dx, False)
            gap = ((rgb - there) ** 2) @ np.array([0.299, 0.587, 0.114])
            share = np.where(present & (gap <= 48 ** 2), 1 / (1 + dx * dx + dy * dy) / (1 + gap / 24 ** 2), 0.0)
            total += there * share[..., None]
            weight += share
    out = image.copy()
    out[..., :3][solid] = np.floor(total[solid] / weight[solid][:, None] + 0.5)
    return out


def merge_flecks(image):
    """After posterising: small enclosed patches join the colour around them. Lines and edges stay."""
    h, w = image.shape[:2]
    limit = max(2, min(24, stills.half_up(w * h * 0.0004)))
    return _merge(image, 0.65, limit, image[..., 3] >= 128, lambda here, there: True)
