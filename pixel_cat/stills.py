"""Turn one image into pixel art.

An image is a numpy array of shape (height, width, 4), uint8, straight RGBA. A "cell" is one pixel of the
finished art; its "footprint" is the patch of the source image it covers.
"""

import math

import numpy as np
from PIL import Image

from .palettes import spread

INK_LUMA = 85        # a colour this dark can be an outline ...
INK_CONTRAST = 40    # ... when it is this much darker than the cell's main colour ...
INK_COVERAGE = 0.2   # ... and outlines cover at least this share of the cell


def load(path):
    with Image.open(path) as image:
        return np.array(image.convert("RGBA"), dtype=np.uint8)


def luma(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def codes(rgb):
    """One integer per colour."""
    rgb = rgb.astype(np.int64)
    return rgb[..., 0] << 16 | rgb[..., 1] << 8 | rgb[..., 2]


def half_up(value):
    return math.floor(value + 0.5)


def fit(width, height, longest):
    """Shrink a size so its longer side is at most `longest`. Never enlarges."""
    ratio = min(1, longest / max(width, height))
    return max(1, half_up(width * ratio)), max(1, half_up(height * ratio))


# ---------------------------------------------------------------- sampling


def _spans(n_out, n_in):
    """Along one axis: which source pixels each cell covers, and how much of each. Three aligned arrays."""
    edges = np.arange(n_out + 1) * (n_in / n_out)
    low, high = edges[:-1], edges[1:]
    first, last = np.floor(low + 1e-9).astype(int), np.minimum(n_in, np.ceil(high - 1e-9).astype(int))
    cell = np.repeat(np.arange(n_out), last - first)
    source = np.concatenate([np.arange(a, b) for a, b in zip(first, last)])
    weight = np.minimum(high[cell], source + 1) - np.maximum(low[cell], source)
    return cell, source, weight


def sample_exact(rgba, width, height):
    """Each cell takes the source pixel under its centre."""
    sh, sw = rgba.shape[:2]
    xs = np.minimum(sw - 1, ((np.arange(width) + 0.5) * sw / width).astype(int))
    ys = np.minimum(sh - 1, ((np.arange(height) + 0.5) * sh / height).astype(int))
    return rgba[ys][:, xs].copy()


def sample_area(rgba, width, height):
    """Each cell is the average of its footprint. Transparent pixels do not tint the result."""
    sh, sw = rgba.shape[:2]
    if (width, height) == (sw, sh):
        return rgba.copy()

    def matrix(n_out, n_in):
        cell, source, weight = _spans(n_out, n_in)
        m = np.zeros((n_out, n_in))
        m[cell, source] = weight
        return m

    down, across = matrix(height, sh), matrix(width, sw).T
    alpha = rgba[..., 3] / 255
    opacity = down @ alpha @ across
    out = np.zeros((height, width, 4), dtype=np.uint8)
    solid = opacity > 1e-12
    for c in range(3):
        total = down @ (rgba[..., c] * alpha) @ across
        out[..., c] = np.floor(np.divide(total, opacity, out=np.zeros_like(total), where=solid) + 0.5)
    out[..., 3] = np.clip(np.floor(opacity / ((sh / height) * (sw / width)) * 255 + 0.5), 0, 255)
    return out


def sample_crisp(rgba, width, height, keep_ink=False):
    """Each cell takes one real source colour, never a blend.

    The footprint's pixels are sorted into colour families (three bits per channel). The family that covers
    most of the cell wins, and the cell takes that family's pixel nearest the centre. With keep_ink, dark
    families that together cover enough of the cell win instead, so thin outlines survive.
    """
    sh, sw = rgba.shape[:2]
    if (width, height) == (sw, sh):
        return rgba.copy()
    out = np.zeros((height, width, 4), dtype=np.uint8)
    flat = rgba.reshape(-1, 4)
    family = (flat[:, 0] >> 5).astype(np.int64) * 64 + (flat[:, 1] >> 5) * 8 + (flat[:, 2] >> 5)
    brightness = luma(flat.astype(np.float64))
    opaque_share = flat[:, 3] / 255
    area = (sh / height) * (sw / width)
    cx, sx, wx = _spans(width, sw)
    cy, sy, wy = _spans(height, sh)
    rows_per_band = max(1, 2_000_000 // max(1, len(cx) * math.ceil(sh / height + 1)))
    for top in range(0, height, rows_per_band):
        band = (cy >= top) & (cy < top + rows_per_band)
        by, bsy, bwy = cy[band] - top, sy[band], wy[band]
        rows = min(rows_per_band, height - top)
        cell = (by[:, None] * width + cx[None, :]).ravel()
        source = (bsy[:, None] * sw + sx[None, :]).ravel()
        weight = (bwy[:, None] * wx[None, :]).ravel() * opaque_share[source]
        distance = (((bsy + 0.5 - (by + top + 0.5) * sh / height) ** 2)[:, None]
                    + ((sx + 0.5 - (cx + 0.5) * sw / width) ** 2)[None, :]).ravel()
        keep = weight > 0
        cell, source, weight, distance = cell[keep], source[keep], weight[keep], distance[keep]
        if not len(cell):
            continue
        key = cell * 512 + family[source]
        order = np.lexsort((distance, key))                       # per family: nearest the centre first
        key, starts = np.unique(key[order], return_index=True)
        g_weight = np.add.reduceat(weight[order], starts)
        g_pixel, g_distance = source[order][starts], distance[order][starts]
        g_cell, g_family = key // 512, key % 512

        def first_per_cell(order, cells):
            cells = cells[order]
            return order[np.r_[True, cells[1:] != cells[:-1]]]

        winner = first_per_cell(np.lexsort((g_family, g_distance, -g_weight, g_cell)), g_cell)
        choice = np.full(rows * width, -1)
        choice[g_cell[winner]] = winner
        if keep_ink:
            fill = brightness[g_pixel[choice[g_cell]]]
            ink = (brightness[g_pixel] <= INK_LUMA) & (fill - brightness[g_pixel] >= INK_CONTRAST)
            covered = np.bincount(g_cell[ink], g_weight[ink], minlength=rows * width) / area
            groups = np.flatnonzero(ink & (covered[g_cell] >= INK_COVERAGE))
            if len(groups):
                best = first_per_cell(np.lexsort((g_family[groups], -g_weight[groups], g_cell[groups])), g_cell[groups])
                choice[g_cell[groups[best]]] = groups[best]
        filled = np.flatnonzero(choice >= 0)
        block = out[top:top + rows].reshape(-1, 4)
        block[filled, :3] = flat[g_pixel[choice[filled]], :3]
        opacity = np.bincount(g_cell, g_weight, minlength=rows * width)
        block[:, 3] = np.clip(np.floor(255 * opacity / area + 0.5), 0, 255)
    return out


SAMPLERS = {"exact": sample_exact, "area": sample_area, "crisp": sample_crisp,
            "ink": lambda rgba, w, h: sample_crisp(rgba, w, h, keep_ink=True)}


# ---------------------------------------------------------------- colour


def nearest(pixels, palette):
    """For every row of `pixels` (n, 3): the index of the closest palette colour."""
    palette = np.asarray(palette, dtype=np.int64)
    pixels = np.asarray(pixels, dtype=np.int64)
    out = np.empty(len(pixels), dtype=np.int64)
    for start in range(0, len(pixels), 65536):
        chunk = pixels[start:start + 65536]
        out[start:start + 65536] = ((chunk[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    return out


def histogram(frames):
    """Opaque colours and how often they occur, in the order the frames first show them.

    `frames` is a list of (image, weight) pairs. Returns (colours (n, 3), counts (n,), any transparency).
    """
    seen, see_through = {}, False
    for image, weight in frames:
        solid = image[..., 3] >= 128
        see_through = see_through or not solid.all()
        unique, first, counts = np.unique(codes(image[..., :3][solid]), return_index=True, return_counts=True)
        for at in np.argsort(first, kind="stable"):
            seen[int(unique[at])] = seen.get(int(unique[at]), 0) + int(counts[at]) * weight
    colours = np.array([[c >> 16, c >> 8 & 255, c & 255] for c in seen], dtype=np.int64).reshape(-1, 3)
    return colours, np.array(list(seen.values()), dtype=np.float64), see_through


def median_cut(colours, counts, limit):
    """Pick `limit` colours for a histogram.

    Start with one box around all colours. Split the box whose widest channel times the square root of its
    population is largest, at the population median along that channel. Each box yields its mean colour.
    """
    if len(colours) <= limit:
        return colours.copy()
    boxes = [np.arange(len(colours))]

    def score(box):
        if len(box) < 2:
            return -1.0, 0
        spans = colours[box].max(axis=0) - colours[box].min(axis=0)
        return float(spans.max() * math.sqrt(counts[box].sum())), int(spans.argmax())

    scores = [score(boxes[0])]
    while len(boxes) < limit:
        at = max(range(len(boxes)), key=lambda i: scores[i][0])
        if scores[at][0] < 0:
            break
        box = boxes[at]
        box = box[np.argsort(colours[box, scores[at][1]], kind="stable")]
        running = np.cumsum(counts[box])
        cut = min(int(np.searchsorted(running, running[-1] / 2)) + 1, len(box) - 1)
        boxes[at:at + 1] = [box[:cut], box[cut:]]
        scores[at:at + 1] = [score(box[:cut]), score(box[cut:])]
    palette = np.array([np.floor(counts[b] @ colours[b] / counts[b].sum() + 0.5) for b in boxes], dtype=np.int64)
    return use_every_slot(rescue(palette, colours, counts) if limit >= 4 else palette, colours, counts)


def use_every_slot(palette, colours, counts):
    """Spend the whole colour budget. A palette entry can end up with no cell at all, because every colour of
    its box sits closer to a neighbour's entry. Such an entry is given to the colour that is served worst."""
    palette = palette.copy()
    for _ in range(4):
        owner = nearest(colours, palette)
        idle = np.flatnonzero(np.bincount(owner, minlength=len(palette)) == 0)
        if not len(idle):
            break
        cost = ((colours - palette[owner]) ** 2).sum(axis=1) * counts
        for slot in idle:
            worst = int(cost.argmax())
            if cost[worst] <= 0:
                break
            palette[slot] = colours[worst]
            cost[worst] = 0
    return palette


def rescue(palette, colours, counts, far=48, share=0.0015):
    """Give small but distinct areas their colour back.

    Splitting at the median follows the crowd: a red scarf in a blue picture can end up blue. So look for
    colours that sit far from every palette entry. If such a group fills a visible share of the picture, it
    gets an entry, paid for by merging the two palette colours that are closest to each other.
    """
    palette = palette.copy()
    for _ in range(max(1, len(palette) // 4)):
        owner = nearest(colours, palette)
        gap = ((colours - palette[owner]) ** 2).sum(axis=1)
        lost = gap > far * far
        if not lost.any():
            break
        family = (colours[lost] >> 4) @ np.array([256, 16, 1])
        names, index = np.unique(family, return_inverse=True)
        sizes = np.bincount(index, counts[lost])
        if sizes.max() < share * counts.sum():
            break
        members = np.flatnonzero(lost)[index == sizes.argmax()]
        found = np.floor(counts[members] @ colours[members] / counts[members].sum() + 0.5).astype(np.int64)
        between = ((palette[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2) + np.eye(len(palette), dtype=np.int64) * 10 ** 9
        a, b = np.unravel_index(between.argmin(), between.shape)
        if between[a, b] >= ((found - palette) ** 2).sum(axis=1).min():
            break                                                 # the palette has no pair closer than the newcomer is
        mass = np.bincount(owner, counts, minlength=len(palette))
        palette[a] = np.floor((palette[a] * mass[a] + palette[b] * mass[b]) / max(1, mass[a] + mass[b]) + 0.5)
        palette[b] = found
    return palette


def apply_palette(image, palette):
    """Move every opaque pixel to its nearest palette colour, in place."""
    solid = image[..., 3] >= 128
    if not solid.any() or not len(palette):
        return
    unique, inverse = np.unique(codes(image[..., :3][solid]), return_inverse=True)
    rgb = np.stack([unique >> 16, unique >> 8 & 255, unique & 255], axis=1)
    image[..., :3][solid] = np.asarray(palette, dtype=np.uint8)[nearest(rgb, palette)][inverse.reshape(-1)]


def quantize(image, limit):
    """Reduce an image to at most `limit` colours, counting transparency as one of them."""
    colours, counts, see_through = histogram([(image, 1)])
    slots = max(1, limit - int(see_through))
    if len(colours) > slots:
        apply_palette(image, median_cut(colours, counts, slots))


def mirror(image, side):
    """Copy one half onto the other. "left" keeps the left half."""
    half = image.shape[1] // 2
    if side == "off" or not half:
        return
    if side == "left":
        image[:, -half:] = image[:, :half][:, ::-1]
    else:
        image[:, :half] = image[:, -half:][:, ::-1]


def stylize(image, strength, palette):
    """The artist look. Blur inside areas of similar brightness so edges stay sharp, lift the saturation,
    snap to the palette, then let a colour that holds four of a pixel's five-pixel cross take the pixel."""
    h, w = image.shape[:2]
    original = image[..., :3].astype(np.float64)
    light = luma(original)
    total, weight = np.zeros((h, w, 3)), np.zeros((h, w))
    rows, cols = np.arange(h), np.arange(w)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            ys, xs = np.clip(rows + dy, 0, h - 1), np.clip(cols + dx, 0, w - 1)
            share = (np.abs(light - light[ys][:, xs]) < 30 + 12 * strength) * (4 if dx == 0 and dy == 0 else 1)
            weight += share
            total += original[ys][:, xs] * share[..., None]
    rgb = total / weight[..., None]
    grey = rgb.mean(axis=2, keepdims=True)
    vivid = grey + (rgb - grey) * ((1.1 + 0.1 * strength) if palette is not None else 1)
    if palette is not None:
        image[..., :3] = np.asarray(palette, dtype=np.uint8)[nearest(np.rint(vivid).reshape(-1, 3), palette)].reshape(h, w, 3)
    else:
        image[..., :3] = np.clip(np.rint(vivid), 0, 255)
    for _ in range(1, strength):
        code = codes(image[..., :3])
        cross = [code[1:-1, 1:-1], code[1:-1, :-2], code[1:-1, 2:], code[:-2, 1:-1], code[2:, 1:-1]]
        winner = np.full(cross[0].shape, -1, dtype=np.int64)
        for candidate in cross:
            votes = sum((other == candidate).astype(int) for other in cross)
            winner[(votes >= 4) & (winner < 0)] = candidate[(votes >= 4) & (winner < 0)]
        hit = winner >= 0
        image[1:-1, 1:-1, :3][hit] = np.stack([winner[hit] >> 16, winner[hit] >> 8 & 255, winner[hit] & 255], axis=1)


# ---------------------------------------------------------------- modes


def theme(settings, palettes):
    """The fixed colours a conversion maps to, or None to keep the image's own colours."""
    palette = settings["palette"]
    if palette == "source":
        return None
    if isinstance(palette, list):
        colours = sorted({c.lower() for c in palette})
    elif palette in palettes:
        colours = palettes[palette]
    else:
        raise ValueError(f"Unknown palette: {palette}. See `pixel-cat catalog`.")
    rgb = np.array([[int(c[p:p + 2], 16) for p in (1, 3, 5)] for c in colours], dtype=np.int64)
    if not isinstance(palette, list) and settings["mode"] != "grid" and settings["colors"] < len(rgb):
        rgb = spread(rgb, settings["colors"])
    return rgb


def harden(image):
    """Pixel art has no half-transparent cells: each one becomes solid or empty. A soft edge would otherwise
    carry colours that no palette covers, and a 16-colour sprite would hold hundreds."""
    empty = image[..., 3] < 128
    image[empty] = 0
    image[~empty, 3] = 255
    return image


def sample(source, settings):
    """The first half of a conversion: the source at its final size, colours untouched."""
    h, w = source.shape[:2]
    if settings["mode"] == "grid":
        width, height = fit(w, h, math.inf if settings["grid"] == "native" else settings["grid"])
        image = SAMPLERS[settings["sampling"]](source, width, height)
        mirror(image, settings["symmetry"])
        return harden(image)
    scaled = max(1, half_up(h * settings["width"] / w))
    width, height = (max(1, half_up(n / settings["pixel"])) for n in (settings["width"], scaled))
    return harden((sample_area if settings["mode"] == "artist" else sample_exact)(source, width, height))


def finish(images, settings, colours, weights=None):
    """The second half: bring the sampled images to their final colours. Several images share one palette."""
    weights = weights or [1] * len(images)
    if settings["mode"] == "artist":
        for image in images:
            stylize(image, settings["strength"], colours)
    if colours is not None:
        for image in images:
            apply_palette(image, colours)
    else:
        found, counts, see_through = histogram(list(zip(images, weights)))
        slots = max(1, settings["colors"] - int(see_through))
        if len(found) > slots:
            shared = median_cut(found, counts, slots)
            for image in images:
                apply_palette(image, shared)
    if settings["mode"] != "grid":
        for image in images:
            mirror(image, settings["symmetry"])


def display_scale(settings, width, height):
    """How much to enlarge a PNG so small art is easy to look at: the longer side lands near 640 pixels."""
    if settings["mode"] in ("artist", "faithful"):
        return settings["pixel"]
    return max(1, min(24, 640 // max(width, height)))
