"""Finishing steps on converted pixel art, applied in order to every frame.

    {"op": "cutout"}                      make the background transparent
    {"op": "trim", "margin": 1}           crop to the drawing
    {"op": "outline", "color": "#101018"} draw an outline around it
"""

import numpy as np

from . import recommend, stills
from .options import integer, keys

CLEAN = {"gentle": (2, 0.70, 0.07), "balanced": (3, 0.60, 0.12), "strong": (5, 0.55, 0.20)}


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
    """Draw a one-cell outline around everything opaque. The picture grows by one cell on each side so the
    outline has room, unless `grow` is false."""
    keys(step, {"op", "color", "corners", "grow"}, "outline")
    colour = rgb(step.get("color", "#000000"))
    out = []
    for image in images:
        if step.get("grow", True):
            image = np.pad(image, ((1, 1), (1, 1), (0, 0)))
        solid = image[..., 3] >= 128
        edge = _neighbours(solid, bool(step.get("corners", False))) & ~solid
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
    `strength` is gentle (one or two cells), balanced or strong (up to five cells, looser match)."""
    keys(step, {"op", "strength"}, "clean")
    if step.get("strength", "balanced") not in CLEAN:
        raise ValueError("strength is gentle, balanced or strong")
    size, share, distance = CLEAN[step.get("strength", "balanced")]

    def alike(here, there):
        a, b = recommend.oklab([[here >> 16, here >> 8 & 255, here & 255], [there >> 16, there >> 8 & 255, there & 255]])
        return float(np.linalg.norm(a - b)) <= distance

    for image in images:
        recommend._merge(image, share, size, image[..., 3] == 255, alike)
    return images


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
    keys(step, {"op", "from", "frames", "rect", "points", "cells"}, "hold")
    source = images[integer(step.get("from", 0), 0, len(images) - 1, "from")]
    mask = area(step, source.shape[1], source.shape[0])
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


OPS = {"cutout": cutout, "trim": trim, "outline": outline, "pad": pad, "flip": flip, "shift": shift, "replace": replace, "clean": clean,
       "hold": hold, "thin": thin, "halo": halo, "fringe": fringe}
LIMITED = ("replace", "clean", "thin", "halo", "fringe")        # these also take an area and then change nothing outside it


def apply(images, steps):
    if not isinstance(steps, list):
        raise TypeError("edits is a list of steps")
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get("op") not in OPS:
            raise ValueError(f"Edit {number}: op is one of {', '.join(OPS)}")
        try:
            where = {k: step[k] for k in ("rect", "points", "cells") if k in step} if step["op"] in LIMITED else {}
            if where:
                done = OPS[step["op"]]([image.copy() for image in images], {k: v for k, v in step.items() if k not in where})
                mask = area(where, images[0].shape[1], images[0].shape[0])
                for image, changed in zip(images, done):
                    image[mask] = changed[mask]
            else:
                images = OPS[step["op"]](images, step)
        except (ValueError, TypeError) as error:
            raise ValueError(f"Edit {number} ({step['op']}): {error}") from None
    return [np.ascontiguousarray(image) for image in images]
