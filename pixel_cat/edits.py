"""Finishing steps on converted pixel art, applied in order to every frame.

    {"op": "cutout"}                      make the background transparent
    {"op": "trim", "margin": 1}           crop to the drawing
    {"op": "outline", "color": "#101018"} draw an outline around it
"""

import numpy as np

from . import recommend, stills
from .options import integer, keys

# largest speck in cells, share of its border one colour has to hold, how far apart the two colours may be
CLEAN = {"gentle": (2, 0.70, 0.07), "balanced": (3, 0.60, 0.12), "strong": (5, 0.55, 0.20), "melt": (12, 0.40, 9.0)}


def rgb(value, name="color"):
    if not (isinstance(value, str) and len(value) == 7 and value[0] == "#"):
        raise ValueError(f"{name} is a colour written as #RRGGBB")
    try:
        return [int(value[i:i + 2], 16) for i in (1, 3, 5)]
    except ValueError:
        raise ValueError(f"{name} is a colour written as #RRGGBB") from None


def _neighbours(mask, corners=False):
    """Cells that touch a cell of `mask`, sideways or (with corners) diagonally."""
    out = np.zeros_like(mask)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dy or dx) and (corners or not (dy and dx)):
                out |= recommend._shift(mask, dy, dx, False)
    return out


def cutout(images, step):
    """Make the background transparent: everything that connects to the picture's edge through cells close
    to a corner colour. A drawing that shares a colour with the background keeps it wherever the drawing's
    own outline encloses it. With `fringe` (the default) a thin rim of background-tinted cells goes too."""
    keys(step, {"op", "tolerance", "fringe", "color"}, "cutout")
    tolerance = integer(step.get("tolerance", 24), 0, 255, "tolerance")
    first = images[0]
    grounds = [rgb(step["color"])] if "color" in step else [first[y, x, :3].tolist() for y in (0, -1) for x in (0, -1) if first[y, x, 3] >= 128]
    for image in images:
        colour = image[..., :3].astype(np.int32)
        like = np.zeros(image.shape[:2], dtype=bool)
        for ground in grounds:
            like |= np.abs(colour - np.array(ground)).max(axis=2) <= tolerance
        like |= image[..., 3] < 128
        reached = np.zeros_like(like)
        reached[0], reached[-1], reached[:, 0], reached[:, -1] = like[0], like[-1], like[:, 0], like[:, -1]
        while True:
            grown = reached | (_neighbours(reached) & like)
            if (grown == reached).all():
                break
            reached = grown
        if step.get("fringe", True) and grounds:
            rim = _neighbours(reached) & ~reached
            tinted = np.zeros_like(rim)
            for ground in grounds:
                tinted |= np.abs(colour - np.array(ground)).max(axis=2) <= tolerance * 3
            reached |= rim & tinted
        image[reached] = 0
    return images


def outline(images, step):
    """Draw an outline around everything opaque, `width` cells thick (1 to 3). The picture grows by that many
    cells on each side so the outline has room, unless `grow` is false. With `inside`, the outermost cells
    of the drawing take the colour instead and nothing grows."""
    keys(step, {"op", "color", "corners", "grow", "width", "inside"}, "outline")
    colour, width = rgb(step.get("color", "#000000")), integer(step.get("width", 1), 1, 3, "width")
    corners, inside = bool(step.get("corners", False)), bool(step.get("inside", False))
    out = []
    for image in images:
        if step.get("grow", True) and not inside:
            image = np.pad(image, ((width, width), (width, width), (0, 0)))
        solid = image[..., 3] >= 128
        for _ in range(width):
            if inside:                                                # what touches emptiness, or the picture's border
                edge = solid & _neighbours(np.pad(~solid, 1, constant_values=True), corners)[1:-1, 1:-1]
                solid = solid & ~edge
            else:
                edge = _neighbours(solid, corners) & ~solid
                solid = solid | edge
            image[edge] = colour + [255]
        out.append(image)
    return out


def trim(images, step):
    """Crop every frame to the box that holds the drawing in any frame, plus a margin of empty cells."""
    keys(step, {"op", "margin"}, "trim")
    margin = integer(step.get("margin", 0), 0, 64, "margin")
    solid = np.any([image[..., 3] >= 128 for image in images], axis=0)
    if not solid.any():
        return images
    rows, cols = np.flatnonzero(solid.any(axis=1)), np.flatnonzero(solid.any(axis=0))
    cropped = [image[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1] for image in images]
    return [np.pad(image, ((margin, margin), (margin, margin), (0, 0))) for image in cropped]


def pad(images, step):
    keys(step, {"op", "cells", "color"}, "pad")
    cells = step.get("cells", 1)
    left, top, right, bottom = cells if isinstance(cells, list) and len(cells) == 4 else [cells] * 4
    for n in (left, top, right, bottom):
        integer(n, 0, 512, "cells")
    fill = rgb(step["color"]) + [255] if "color" in step else [0, 0, 0, 0]
    out = []
    for image in images:
        h, w = image.shape[:2]
        grown = np.empty((h + top + bottom, w + left + right, 4), dtype=np.uint8)
        grown[:] = fill
        grown[top:top + h, left:left + w] = image
        out.append(grown)
    return out


def flip(images, step):
    keys(step, {"op", "axis"}, "flip")
    if step.get("axis", "x") not in ("x", "y"):
        raise ValueError('axis is "x" (mirror left and right) or "y"')
    return [np.ascontiguousarray(image[:, ::-1] if step.get("axis", "x") == "x" else image[::-1]) for image in images]


def shift(images, step):
    """Move the picture by whole cells; what leaves on one side comes back on the other."""
    keys(step, {"op", "x", "y"}, "shift")
    return [np.roll(image, (integer(step.get("y", 0), -4096, 4096, "y"), integer(step.get("x", 0), -4096, 4096, "x")), axis=(0, 1)) for image in images]


def replace(images, step):
    """Turn one colour (or several) into another. `tolerance` also catches near misses."""
    keys(step, {"op", "from", "to", "tolerance"}, "replace")
    sources = step.get("from")
    sources = [rgb(c, "from") for c in (sources if isinstance(sources, list) else [sources])]
    target, tolerance = rgb(step.get("to"), "to"), integer(step.get("tolerance", 0), 0, 255, "tolerance")
    for image in images:
        colour = image[..., :3].astype(np.int32)
        hit = np.zeros(image.shape[:2], dtype=bool)
        for source in sources:
            hit |= np.abs(colour - np.array(source)).max(axis=2) <= tolerance
        image[..., :3][hit & (image[..., 3] >= 128)] = target
    return images


def clean(images, step):
    """Remove specks: small enclosed groups of cells join the colour around them when the two look alike.
    `strength` is gentle (one or two cells), balanced or strong (up to five cells, looser match). `melt` takes
    flecks of up to twelve cells whatever their colour; it is meant for an area you point at."""
    keys(step, {"op", "strength"}, "clean")
    if step.get("strength", "balanced") not in CLEAN:
        raise ValueError("strength is gentle, balanced, strong or melt")
    size, share, distance = CLEAN[step.get("strength", "balanced")]

    def alike(here, there):
        a, b = recommend.oklab([[here >> 16, here >> 8 & 255, here & 255], [there >> 16, there >> 8 & 255, there & 255]])
        return float(np.linalg.norm(a - b)) <= distance

    for image in images:
        recommend._merge(image, share, size, image[..., 3] == 255, alike)
    return images


def mend(image, hole, window=3):
    """Paint what lies around a hole into it, and return the new picture.

    Each cell of the hole looks at the nearest cell outside. If that cell is empty, the cell stays empty, so
    a figure on empty ground does not grow where a piece of it was taken away. Otherwise it draws one of
    the colours that are common around that cell, by their shares: the grain of the surroundings carries on,
    and a lone highlight that passes close by is a minority and stays out. A cell that then stands out from
    the middle tone around it takes a colour nearer to that tone. The same hole is always filled the same way.
    """
    out = image.copy()
    h, w = hole.shape
    if not hole.any() or hole.all():
        return out
    # the nearest cell outside, for every cell: what is known spreads inwards ring by ring
    ys, xs = np.indices((h, w))
    known = ~hole
    near_y, near_x = np.where(known, ys, -1), np.where(known, xs, -1)
    while not known.all():
        grown, grown_y, grown_x = known.copy(), near_y.copy(), near_x.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            here = (slice(max(0, -dy), h + min(0, -dy)), slice(max(0, -dx), w + min(0, -dx)))
            there = (slice(max(0, dy), h + min(0, dy)), slice(max(0, dx), w + min(0, dx)))
            take = known[there] & ~grown[here]
            grown_y[here][take], grown_x[here][take] = near_y[there][take], near_x[there][take]
            grown[here] |= take
        known, near_y, near_x = grown, grown_y, grown_x
    code = image[..., 0].astype(np.int64) << 24 | image[..., 1].astype(np.int64) << 16 | image[..., 2].astype(np.int64) << 8 | image[..., 3]
    art = ~hole & (image[..., 3] > 0)
    cells = np.flatnonzero(hole)
    origin = near_y.ravel()[cells] * w + near_x.ravel()[cells]
    dice = np.random.default_rng(len(cells) * 7919 + int(cells[0]))
    filled = np.zeros(len(cells), dtype=np.int64)
    for spot in np.unique(origin):
        y, x = divmod(int(spot), w)
        if not art[y, x]:
            continue                                               # empty ground stays empty
        box = (slice(max(0, y - window), y + window + 1), slice(max(0, x - window), x + window + 1))
        colours, counts = np.unique(code[box][art[box]], return_counts=True)
        keep = counts >= min(counts.max(), counts.sum() * 0.3)     # when nothing holds that much: the most common one
        shares = np.cumsum(counts[keep] / counts[keep].sum())
        mine = origin == spot
        filled[mine] = colours[keep][np.minimum(len(shares) - 1, np.searchsorted(shares, dice.random(int(mine.sum()))))]
    flat = out.reshape(-1, 4)
    flat[cells] = np.stack([filled >> 24 & 255, filled >> 16 & 255, filled >> 8 & 255, filled & 255], axis=1)
    # strays: a filled cell far from the middle tone of the art around it takes the colour there that is nearest to it
    light = np.where(out[..., 3] > 0, stills.luma(out[..., :3].astype(np.float64)), np.nan)
    views = np.lib.stride_tricks.sliding_window_view(np.pad(light, 3, constant_values=np.nan), (7, 7))
    painted = cells[filled != 0]
    if len(painted):
        around = views.reshape(h * w, 49)[painted]
        middle = np.nanmedian(around, axis=1)
        stray = np.abs(light.ravel()[painted] - middle) > 60
        if stray.any():
            best = np.nanargmin(np.abs(around[stray] - middle[stray, None]), axis=1)
            py, px = np.divmod(painted[stray], w)
            sy, sx = np.clip(py + best // 7 - 3, 0, h - 1), np.clip(px + best % 7 - 3, 0, w - 1)
            flat[painted[stray]] = out[sy, sx]
    return out


def area(step, width, height):
    """The cells a step works on, as a mask: `rect` [x, y, width, height], `points` (a polygon in cells; a
    cell is inside when its centre is), or `cells` (a list of [x, y]). Without any of them: the whole picture."""
    mask = np.zeros((height, width), dtype=bool)
    if "rect" in step:
        x, y, w, h = (int(v) for v in step["rect"])
        mask[max(0, y):max(0, y + h), max(0, x):max(0, x + w)] = True
    elif "points" in step:
        points = [(float(x), float(y)) for x, y in step["points"]]
        if len(points) < 3:
            raise ValueError("A polygon needs at least three points")
        xs, ys = (np.arange(width) + 0.5)[None, :], (np.arange(height) + 0.5)[:, None]
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
            if y1 != y2:
                mask ^= ((y1 > ys) != (y2 > ys)) & (xs < (x2 - x1) * (ys - y1) / (y2 - y1) + x1)
    elif "cells" in step:
        for x, y in step["cells"]:
            if 0 <= x < width and 0 <= y < height:
                mask[int(y), int(x)] = True
    else:
        mask[:] = True
    return mask


def targets(step, count, name="frames"):
    """Which frames a step reaches: "all" (the default) or a list of frame numbers."""
    wanted = step.get(name, "all")
    if wanted == "all":
        return list(range(count))
    return [integer(f, 0, count - 1, "frame") for f in wanted]


def hold(images, step):
    """Make an area stand still: its cells are copied from one frame into the others. Everything outside the
    area keeps moving. This is the cure for a spot that flickers next to motion that is wanted."""
    keys(step, {"op", "from", "frames", "rect", "points", "cells", "outside"}, "hold")
    source = images[integer(step.get("from", 0), 0, len(images) - 1, "from")]
    mask = area(step, source.shape[1], source.shape[0])
    if step.get("outside"):                                 # the other way round: only the area keeps moving
        mask = ~mask
    for index in targets(step, len(images)):
        images[index] = images[index].copy()
        images[index][mask] = source[mask]
    return images


def thin(images, step):
    """Thin drawn lines to one cell; with `gap`, close breaks of up to three cells. Lines are dark cells, or
    cells near `color`; only what is at most `width` cells thick counts as a line."""
    from . import lines

    keys(step, {"op", "color", "tolerance", "width", "gap"}, "thin")
    colour = rgb(step["color"]) if step.get("color") else None
    tolerance, width = integer(step.get("tolerance", 28), 0, 180, "tolerance"), integer(step.get("width", 4), 2, 8, "width")
    gap = integer(step.get("gap", 0), 0, 3, "gap")
    return [lines.thin(image, colour, tolerance, width, gap) for image in images]


def halo(images, step):
    """Remove the thin rim of in-between colour beside dark lines. `strength`: gentle or stronger."""
    from . import lines

    keys(step, {"op", "strength", "width"}, "halo")
    return [lines.halo(image, step.get("strength", "gentle"), integer(step.get("width", 4), 2, 8, "width")) for image in images]


def fringe(images, step):
    """Remove loose bits at the edge of a cut-out drawing: groups of at most `max` cells of one colour whose
    surroundings are mostly empty. `contact` is the share of empty cells around a group from which it goes."""
    keys(step, {"op", "max", "contact"}, "fringe")
    most, contact = integer(step.get("max", 4), 1, 12, "max"), float(step.get("contact", 0.5))
    if not 0 < contact <= 1:
        raise ValueError("contact is a share between 0 and 1")
    out = []
    for image in images:
        image = image.copy()
        h, w = image.shape[:2]
        solid = image[..., 3] >= 128
        flat = image.reshape(-1, 4)
        for group in recommend.islands(stills.codes(image[..., :3]), solid, most):
            members, around, empty = set(group.tolist()), 0, 0
            for p in group:
                y, x = divmod(int(p), w)
                for dy, dx in recommend.RING:
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and ny * w + nx in members:
                        continue
                    around += 1
                    empty += not (0 <= ny < h and 0 <= nx < w and solid[ny, nx])
            if around and empty / around >= contact:
                flat[group] = 0
        out.append(image)
    return out


def smooth(images, step):
    """Take single-cell bumps off an edge and fill single-cell dents in it. `what` is the silhouette (the
    border between drawing and emptiness) or the lines (dark cells, or cells near `color`). A bump sits alone
    on a straight run; a dent is one cell deep with a straight run on both sides. Tips of thin lines and gaps
    more than one cell deep stay."""
    from . import lines

    keys(step, {"op", "what", "color", "tolerance", "width", "passes"}, "smooth")
    what = step.get("what", "silhouette")
    if what not in ("silhouette", "lines"):
        raise ValueError("what is silhouette or lines")
    colour = rgb(step["color"]) if step.get("color") else None
    tolerance, width = integer(step.get("tolerance", 28), 0, 180, "tolerance"), integer(step.get("width", 4), 2, 8, "width")
    passes = integer(step.get("passes", 1), 1, 4, "passes")

    def at(mask, dy, dx):
        return lines._shift(mask, dy, dx)

    out = []
    for image in images:
        image = image.copy()
        for _ in range(passes):
            inked = image[..., 3] >= 128 if what == "silhouette" else lines.find(image, colour, tolerance, width)
            up, down, left, right = at(inked, 1, 0), at(inked, -1, 0), at(inked, 0, 1), at(inked, 0, -1)
            ul, ur, dl, dr = at(inked, 1, 1), at(inked, 1, -1), at(inked, -1, 1), at(inked, -1, -1)
            four = up.astype(np.int8) + down + left + right
            # it hangs on one side, that side is a straight run, and nothing is beside or beyond it
            bump = inked & (four == 1) & ((down & dl & dr & ~ul & ~ur) | (up & ul & ur & ~dl & ~dr) | (left & ul & dl & ~ur & ~dr) | (right & ur & dr & ~ul & ~dl))
            # it is open to one side only, and the run goes on straight to both sides of the opening
            dent = ~inked & (four == 3) & ((~up & ~ul & ~ur) | (~down & ~dl & ~dr) | (~left & ~ul & ~dl) | (~right & ~ur & ~dr))
            if not (bump.any() or dent.any()):
                break
            before = image.copy()
            code = stills.codes(before[..., :3])
            for mask, wanted in ((bump, ~inked), (dent, inked)):      # each takes the colour most common beside it, from the other kind
                for y, x in zip(*np.nonzero(mask)):
                    votes = {}
                    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < image.shape[0] and 0 <= nx < image.shape[1] and wanted[ny, nx] and before[ny, nx, 3] >= 128:
                            votes[code[ny, nx]] = votes.get(code[ny, nx], 0) + 1
                    if votes:
                        winner = int(max(votes, key=lambda c: (votes[c], -c)))
                        image[y, x] = (winner >> 16, winner >> 8 & 255, winner & 255, 255)
                    elif what == "silhouette" and mask is bump:
                        image[y, x] = 0                               # a bump on the silhouette goes back to emptiness
        out.append(image)
    return out


OPS = {"cutout": cutout, "trim": trim, "outline": outline, "pad": pad, "flip": flip, "shift": shift, "replace": replace, "clean": clean,
       "hold": hold, "thin": thin, "halo": halo, "fringe": fringe, "smooth": smooth}
# these also take an area and then change nothing outside it; with "protect": true, nothing inside it. With
# "only": [colours], just cells of those colours change. With "steady": true, cells that showed the same in
# every frame before the step show the same after it.
LIMITED = ("replace", "clean", "thin", "halo", "fringe", "smooth")


def apply(images, steps):
    if not isinstance(steps, list):
        raise TypeError("edits is a list of steps")
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get("op") not in OPS:
            raise ValueError(f"Edit {number}: op is one of {', '.join(OPS)}")
        try:
            limited = step["op"] in LIMITED or (step["op"] == "outline" and (step.get("inside") or step.get("grow") is False))
            where = {k: step[k] for k in ("rect", "points", "cells", "protect", "only") if k in step} if limited else {}
            steady = limited and bool(step.get("steady")) and len(images) > 1
            if where or steady:
                if where.get("protect") and not any(k in where for k in ("rect", "points", "cells")):
                    raise ValueError("protect needs an area: rect, points or cells")
                only = [c[0] << 16 | c[1] << 8 | c[2] for c in map(rgb, where.get("only") or [])]
                done = OPS[step["op"]]([image.copy() for image in images], {k: v for k, v in step.items() if k not in where and k != "steady"})
                if steady:                                        # what stood still before the step stands still after it
                    still = np.all([(image == images[0]).all(axis=2) for image in images[1:]], axis=0)
                    for changed in done[1:]:
                        changed[still] = done[0][still]
                mask = area(where, images[0].shape[1], images[0].shape[0])
                if where.get("protect"):                          # the area is what must stay as it is
                    mask = ~mask
                for image, changed in zip(images, done):
                    reach = mask & np.isin(stills.codes(image[..., :3]), only) & (image[..., 3] >= 128) if only else mask
                    image[reach] = changed[reach]                 # with `only`, just cells that had one of these colours
            else:
                images = OPS[step["op"]](images, {k: v for k, v in step.items() if k != "steady"} if limited else step)
        except (ValueError, TypeError) as error:
            raise ValueError(f"Edit {number} ({step['op']}): {error}") from None
    return [np.ascontiguousarray(image) for image in images]
