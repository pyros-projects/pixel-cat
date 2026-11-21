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

    First the straight lines: a line or a border that the picture shows on both sides of the hole is drawn
    through it (see _lines). Then the patterns: where the picture repeats itself around the hole, as bricks,
    tiles or any regular pattern do, whole patches are copied from places that look like what is already
    there (see _carry_on). Everything else is painted cell by cell in the colours that are common nearby (see
    _common). No colour comes in that the picture did not have, and the same hole is always filled the same way.
    """
    out = image.copy()
    if not hole.any() or hole.all():
        return out
    lines = _lines(out, hole)
    left = _carry_on(out, hole, lines) & ~lines
    if left.any():
        _common(out, left, window, lines)
    return out


def _lines(out, hole, back=32, least=12, short=8, half=3, same=22.0, marked=36.0, most=360):
    """Draw the straight lines of the picture through a hole, in place, and return the cells of those lines:
    the ones drawn in the hole, and the ones outside it where each line comes from.

    Two cells on the rim that face each other across the hole are joined when the picture shows the same line
    behind both. Behind each of them lies a strip, seven cells wide, that runs on from the hole along the way
    from one to the other. It is a third as long as that way, at least `least` cells and at most `back`
    (`short` will do where the picture ends before that): the further a line has to go, the more exactly its
    direction has to be known. Three things are asked of the strips. Each has to look the same step after
    step, so something runs along it; a line drawn in cells may step a column aside now and then, but it must
    not move away, as it does when it is followed along a direction that is not its own. The two have to look
    alike, so it is the same thing on both sides. And something in them has to stand out from their ground by
    `marked` levels of light: a line, or the border between two areas. Then the cells that stand out are
    drawn from rim to rim, each half in the colours of its own side. The ground beside them is left to the
    stages after this one.

    The better a pair fits, the earlier its line is drawn. Each end of a line is used once, and a line that
    would run along one that is already there, in its place or right beside it, is left out; lines may cross.
    A curve, a line that shows on one side of the hole only, and a painted line that changes as it goes are
    not drawn: this stage draws what it can see on both sides, and leaves the rest open.
    """
    h, w = hole.shape
    drawn = np.zeros_like(hole)
    lines = np.zeros_like(hole)
    art = ~hole & (out[..., 3] > 0)
    rgb = out[..., :3].astype(np.float32)
    tone = stills.luma(rgb)
    # a rim cell is worth a look when the picture around it is not plain
    views = lambda fill: np.lib.stride_tricks.sliding_window_view(np.pad(np.where(art, tone, fill), 2, constant_values=fill), (5, 5))     # noqa: E731
    lively = views(-np.inf).max(axis=(2, 3)) - views(np.inf).min(axis=(2, 3)) >= marked
    ys, xs = np.nonzero(art & _neighbours(hole, corners=True) & lively)
    if len(ys) > most:
        keep = np.arange(0, len(ys), -(-len(ys) // most))
        ys, xs = ys[keep], xs[keep]
    count = len(ys)
    if count < 2:
        return lines
    rim = np.stack([ys, xs], axis=1).astype(np.float32)
    steps_back = np.arange(1, back + 1, dtype=np.float32)
    beside = np.arange(-half, half + 1, dtype=np.float32)             # its columns, across the way
    inner = slice(1, 2 * half)                                        # the columns that are compared; the outermost two are room to wobble
    found = []
    for a in range(count - 1):
        way = rim[a + 1:] - rim[a]                                    # to every later rim cell
        far = np.hypot(way[:, 0], way[:, 1])
        others = np.nonzero(far >= 6)[0]
        through = np.ones(len(others), dtype=bool)
        for share in (0.25, 0.5, 0.75):                               # the way between them leads through the hole
            y, x = np.rint(rim[a, 0] + way[others, 0] * share).astype(int), np.rint(rim[a, 1] + way[others, 1] * share).astype(int)
            through &= hole[y, x]
        others = others[through]
        if not len(others):
            continue
        far = far[others]
        along = way[others] / far[:, None]
        across = np.stack([along[:, 1], -along[:, 0]], axis=1)
        need = np.clip(np.rint(far / 3), least, back)                 # the further a line has to go, the longer it has to be seen
        behind = steps_back[:int(need.max())]                         # the steps of a strip, away from the hole

        def strip(start, heading):
            """The picture behind rim cells, as (pairs, steps, columns): its colours, its light, which cells
            are known, and which steps count. A line that meets the rim at a slant has the hole beside it for
            its first steps, so a strip begins where its whole width is clear of the hole."""
            def cells(steps):
                y = np.rint(start[:, 0, None, None] + heading[:, 0, None, None] * steps[:, :, None] + across[:, 0, None, None] * beside[None, None, :]).astype(int)
                x = np.rint(start[:, 1, None, None] + heading[:, 1, None, None] * steps[:, :, None] + across[:, 1, None, None] * beside[None, None, :]).astype(int)
                within = (y >= 0) & (y < h) & (x >= 0) & (x < w)
                return np.clip(y, 0, h - 1), np.clip(x, 0, w - 1), within

            y, x, within = cells(np.repeat(steps_back[None, :13], len(start), axis=0))
            clear = ~(within & hole[y, x]).any(axis=2)                # a line that runs along the rim for longer than these is not followed
            skip = clear.argmax(axis=1)
            y, x, within = cells(skip[:, None] + behind[None, :])
            steps = np.minimum(need, within[:, :, half].cumprod(axis=1).sum(axis=1))     # no further than the picture goes
            counts = behind[None, :] <= steps[:, None]
            return rgb[y, x], tone[y, x], within & art[y, x] | ~counts[:, :, None], counts, np.where(clear.any(axis=1), steps, 0)

        mine = strip(np.repeat(rim[a][None], len(others), axis=0), -along)
        theirs = strip(rim[others + a + 1], along)
        both = mine[2].all(axis=1) & theirs[2].all(axis=1)            # columns that are known at every step on both sides
        enough = (mine[4] >= short) & (theirs[4] >= short) & (both.sum(axis=1) >= 3) & both[:, half]
        if not enough.any():
            continue

        def differ(one, other, marks_one, marks_other, known):
            """How far two strips are apart, per pair, and by how many columns the second one had to step
            aside for it (-1, 0 or 1): a slanted line drawn in cells wobbles by one. They have to show a line
            in the same columns or none in either; a line that one of them lacks is a difference, however small
            its share of the strip. Then it is the mean over their cells of the largest step in any channel."""
            best = by = None
            for aside in (0, -1, 1):
                moved = slice(1 + aside, 2 * half + aside)
                valid = known[..., inner] & known[..., moved]
                apart = (np.abs(one[..., inner, :] - other[..., moved, :]).max(axis=-1) * valid).sum(axis=-1) / np.maximum(1, valid.sum(axis=-1))
                unlike = ((marks_one[..., inner] != marks_other[..., moved]) & valid).any(axis=-1)
                apart = np.where(valid[..., half - 1] & (valid.sum(axis=-1) >= 3) & ~unlike, apart, 1e9)
                if best is None:
                    best, by = apart, np.zeros(apart.shape, dtype=np.float32)
                else:
                    by = np.where((apart < best) & (best > same), aside, by)       # aside only when it does not fit straight on
                    best = np.minimum(best, apart)
            return best, by

        def runs(colours, light, counts):
            """Whether a strip looks the same step after step. Its usual look is the middle colour of each
            column over its steps, so that a line crossing it somewhere does not set the measure. Gives the
            usual look, which of its cells stand out, the share of steps that look like it, how far those are
            from it, the share that stepped aside, and how far the far half of the strip stands aside from the
            near half: a line that is followed along its own direction steps aside now and then, one that is
            followed along another direction moves away."""
            middle = np.maximum(0, counts.sum(axis=1) - 1) // 2      # the lower middle one, so that it is a colour the column really has
            usual = np.take_along_axis(np.sort(np.where(counts[:, :, None, None], colours, np.inf), axis=1), middle[:, None, None, None], axis=1)[:, 0]
            usual = np.where(np.isfinite(usual), usual, 0)
            level = stills.luma(usual)
            open_ = both | ~both.any(axis=1)[:, None]
            stands = np.abs(level - np.nanmedian(np.where(open_, level, np.nan), axis=1)[:, None]) >= marked / 2
            marks = np.abs(light - np.nanmedian(np.where(open_[:, None, :], light, np.nan), axis=2)[:, :, None]) >= marked / 2
            apart, aside = differ(np.repeat(usual[:, None], len(behind), axis=1), colours, np.repeat(stands[:, None], len(behind), axis=1), marks, np.repeat(both[:, None, :], len(behind), axis=1))
            alike = (apart <= same) & counts
            steps = np.maximum(1, counts.sum(axis=1))
            near, far_half = alike & (behind[None, :] * 2 <= steps[:, None]), alike & (behind[None, :] * 2 > steps[:, None])
            drift = np.abs((aside * far_half).sum(axis=1) / np.maximum(1, far_half.sum(axis=1)) - (aside * near).sum(axis=1) / np.maximum(1, near.sum(axis=1)))
            return usual, level, stands, alike.sum(axis=1) / steps, np.where(alike, apart, 0).sum(axis=1) / np.maximum(1, alike.sum(axis=1)), ((aside != 0) & alike).sum(axis=1) / steps, drift

        look_mine, level_mine, stands_mine, share_mine, apart_mine, aside_mine, drift_mine = runs(mine[0], mine[1], mine[3])
        look_theirs, level_theirs, stands_theirs, share_theirs, apart_theirs, aside_theirs, drift_theirs = runs(theirs[0], theirs[1], theirs[3])
        ends, stepped = differ(look_mine, look_theirs, stands_mine, stands_theirs, both)
        wobble = (aside_mine + aside_theirs) / 2                      # a line that runs between two columns steps aside half the time
        light = (level_mine + level_theirs) / 2                       # the usual look of both sides together
        seen = both.copy()
        seen[:, 0] = seen[:, -1] = False
        contrast = np.where(seen, light, -1e9).max(axis=1) - np.where(seen, light, 1e9).min(axis=1)
        worst = np.maximum(np.maximum(apart_mine, apart_theirs), ends)
        take = enough & (worst <= same) & (contrast >= np.maximum(marked, 2 * worst)) & (wobble <= 0.55) & (share_mine >= 0.75) & (share_theirs >= 0.75)
        take &= np.maximum(drift_mine, drift_theirs) <= 0.5           # but one that is followed along another direction moves away
        if not take.any():
            continue
        for pair in np.nonzero(take)[0]:
            line = seen[pair] & stands_mine[pair]                     # only what stands out is drawn; the ground is left for later
            if line.any():
                columns = [int(column) for column in np.nonzero(line)[0] if 0 <= column + int(stepped[pair]) <= 2 * half]
                looks = tuple((column - half, look_mine[pair, column], look_theirs[pair, column + int(stepped[pair])]) for column in columns)
                found.append((float(worst[pair] + 60 * wobble[pair] + 30 * abs(stepped[pair])), a, int(others[pair] + a + 1), looks, float(stepped[pair])))
    found.sort()
    source = out.copy()
    flank = np.zeros_like(hole)                                       # ground next to a drawn line: no other line belongs there
    ended = np.zeros_like(hole)                                       # where a drawn line meets the rim: each end is used once

    def cell(start, heading, step, column, across):
        return int(round(start[0] + heading[0] * step + across[0] * column)), int(round(start[1] + heading[1] * step + across[1] * column))

    for _, a, b, looks, aside in found:                               # the columns as this end shows them; the other end shows them `aside` further
        if not looks:
            continue
        columns = [column for column, _, _ in looks]
        way = rim[b] - rim[a]
        far = float(np.hypot(*way))
        along = way / far
        across = np.array([along[1], -along[0]])
        steps = int(round(far))
        sides = ((rim[a], -along, 0.0), (rim[b], along, aside))
        tips = [cell(start, along, 0, column + shift, across) for start, _, shift in sides for column in columns]
        tips = [(y, x) for y, x in tips if 0 <= y < h and 0 <= x < w]
        if any(ended[y, x] for y, x in tips):
            continue
        need = int(np.clip(round(far / 3), least, back))
        cells, clash, fresh, outside = [], 0, 0, []
        for column, *look in sorted(looks, key=lambda entry: abs(entry[0])):
            usual = []                                                # per side: the cell behind the rim that is most like the line's usual look there
            for (start, heading, shift), colour in zip(sides, look):
                row = [cell(start, heading, step, column + shift, across) for step in range(1, need + 1)]
                row = [(y, x) for y, x in row if 0 <= y < h and 0 <= x < w and art[y, x] and np.abs(rgb[y, x] - colour).max() <= same]
                usual.append(row[int(np.argmin([np.abs(rgb[y, x] - colour).max() for y, x in row]))] if row else None)
                offset, missed = column + shift, 0
                for step in range(-2, 72 if row else -2):            # the line where it comes from, followed outwards from the rim
                    near = [(by, cell(start, heading, step, offset + by, across)) for by in (0, -1, 1)]
                    near = [(by, (y, x)) for by, (y, x) in near if 0 <= y < h and 0 <= x < w and art[y, x] and np.abs(rgb[y, x] - rgb[usual[-1]]).max() <= same]
                    outside += [place for _, place in near[:1]]         # one cell a step: what lies beside it is not the line
                    missed = 0 if near else missed + 1
                    if step > 2 and missed > 3:
                        break                                         # it ends here; up to three cells that a slanted line skips are no end
                    if step > 2 and near and near[0][0]:
                        offset += near[0][0]                          # it has moved a column aside: go with it
            for step in range(-half, steps + half + 1):               # a little beyond both ends: a line beside the rim cell enters the hole before it
                y, x = cell(rim[a], along, step, column + aside * min(1, max(0, step / steps)), across)
                if not (0 <= y < h and 0 <= x < w) or not hole[y, x]:
                    continue
                if step <= steps / 2:                                 # each half takes its cells from its own side, mirrored at the rim
                    back_by, (start, heading, shift), typical = (step - 1) % need + 1, sides[0], usual[0]
                else:
                    back_by, (start, heading, shift), typical = (steps - step - 1) % need + 1, sides[1], usual[1]
                if typical is None:
                    continue
                sy, sx = cell(start, heading, back_by, column + shift, across)
                if not (0 <= sy < h and 0 <= sx < w) or not art[sy, sx] or np.abs(rgb[sy, sx] - rgb[typical]).max() > same:
                    sy, sx = typical                                  # not the line there (another one crosses, or this one takes a step): the usual cell instead
                if drawn[y, x]:
                    clash += int(np.abs(out[y, x, :3].astype(int) - source[sy, sx, :3]).max() > 2 * same)
                else:
                    clash += int(flank[y, x])
                    fresh += int(not flank[y, x])
                    cells.append((y, x, sy, sx))
        if not fresh or clash > 0.15 * (fresh + clash):               # it contradicts what better lines have put there
            continue
        for y, x, sy, sx in cells:
            if not drawn[y, x]:
                out[y, x] = source[sy, sx]
                drawn[y, x] = lines[y, x] = True
        for y, x in outside:
            lines[y, x] = True
        for column in (min(columns) - 1, max(columns) + 1):
            for step in range(-half, steps + half + 1):
                y, x = cell(rim[a], along, step, column + aside * min(1, max(0, step / steps)), across)
                if 0 <= y < h and 0 <= x < w and hole[y, x]:
                    flank[y, x] = True
        for y, x in tips:
            ended[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = True
    return lines


def _carry_on(out, hole, lines=None, radius=8, reach=48, fit=10.0, far=0.1):
    """Carry a pattern on into a hole, in place, and return the cells that are still open.

    The cell on the rim of the hole where a line of the picture runs most strongly into it comes first. The
    patch around it, 17 cells wide, is half known. Among all patches near the hole that are known in full, the
    one that looks most like the known half is copied into the open half; of two that fit alike, the nearer
    one. A patch is only taken when it differs from the known half by at most `fit` levels (root mean square),
    when a third of the patch is known, and when the known half has a pattern at all: on calm ground a copied
    patch would only bring along whatever lone detail lies in it. Cells for which no patch is good enough stay
    open.

    `lines` are the cells of lines that were drawn through the hole before, in the hole and outside it. They
    stay as they are, and a patch is only taken when it agrees with them: it shows the line where it would lie
    on one, by the same measure as with the known half, and it brings none of these lines to a cell that has
    none, or a skirting would come back a second time further down. Apart from that the drawn cells are open
    cells like the others: a line through the hole says where it runs, not what lies beside it, so it is no
    rim to go on from and makes no patch known.
    """
    h, w = hole.shape
    k, r = 2 * radius + 1, radius
    ys, xs = np.nonzero(hole)
    top, left = max(0, int(ys.min()) - reach), max(0, int(xs.min()) - reach)
    bottom, right = min(h, int(ys.max()) + 1 + reach), min(w, int(xs.max()) + 1 + reach)
    tall, wide = bottom - top + 2 * r, right - left + 2 * r           # the hole and its surroundings, with a rim so that every patch is whole
    art = np.zeros((tall, wide, 4), dtype=np.float32)
    art[r:tall - r, r:wide - r] = out[top:bottom, left:right]
    inside = np.zeros((tall, wide), dtype=bool)
    inside[r:tall - r, r:wide - r] = True
    todo = np.zeros((tall, wide), dtype=bool)
    todo[r:tall - r, r:wide - r] = hole[top:bottom, left:right]
    ruled = np.zeros((tall, wide), dtype=bool)
    if lines is not None:
        ruled[r:tall - r, r:wide - r] = lines[top:bottom, left:right]
    cy, cx = np.nonzero(np.lib.stride_tricks.sliding_window_view(inside & ~todo, (k, k)).all(axis=(2, 3)))     # patches known in full, by their top left corner
    most = max(500, 48_000_000 // (k * k * 4 * 8))                    # as many as fit into some 50 MB
    if len(cy) > most:
        keep = np.arange(0, len(cy), -(-len(cy) // most))
        cy, cx = cy[keep], cx[keep]
    if not len(cy):
        return hole.copy()
    brings = np.lib.stride_tricks.sliding_window_view(ruled, (k, k))[cy, cx].reshape(len(cy), k * k).astype(np.float32) if ruled.any() else None
    stack = np.lib.stride_tricks.sliding_window_view(art, (k, k), axis=(0, 1))[cy, cx]
    stack = np.ascontiguousarray(stack.transpose(0, 2, 3, 1).reshape(len(cy), k * k * 4))
    squares = stack * stack
    sure = (inside & ~todo).astype(np.float32)                        # how far a cell can be trusted: 1 where it was known
    skipped = np.zeros_like(todo)
    boxes = lambda a: np.lib.stride_tricks.sliding_window_view(np.pad(a, r), (k, k))     # noqa: E731
    while True:
        done = inside & ~todo
        rim = np.zeros_like(todo)
        rim[1:] |= done[:-1]
        rim[:-1] |= done[1:]
        rim[:, 1:] |= done[:, :-1]
        rim[:, :-1] |= done[:, 1:]
        fy, fx = np.nonzero(todo & rim & ~skipped)
        if not len(fy):
            break
        trust = boxes(sure)[fy, fx].sum(axis=(1, 2)) / (k * k)
        # how strongly a line runs into the hole: the strongest change of light nearby, turned by a quarter, against the way out of the hole
        tone = np.where(done, stills.luma(art[..., :3].astype(np.float64)), np.nan)
        gy, gx = (np.nan_to_num(g) for g in np.gradient(tone))
        at = boxes(gx * gx + gy * gy)[fy, fx].reshape(len(fy), -1).argmax(axis=1)
        sy, sx = np.clip(fy + at // k - r, 0, tall - 1), np.clip(fx + at % k - r, 0, wide - 1)
        ny, nx = np.gradient(todo.astype(np.float64))
        length = np.hypot(ny[fy, fx], nx[fy, fx]) + 1e-6
        line = np.abs(gx[sy, sx] * ny[fy, fx] - gy[sy, sx] * nx[fy, fx]) / length / 255
        first = int(np.argmax(trust * (line + 0.001)))
        y, x = int(fy[first]), int(fx[first])
        here = (slice(y - r, y + r + 1), slice(x - r, x + r + 1))
        have = done[here].reshape(k * k)
        shades = tone[here].reshape(k * k)[have]
        patterned = len(shades) and float((np.abs(shades - np.median(shades)) >= 24).mean()) >= 0.05
        if have.mean() < 1 / 3 or not patterned:
            skipped[y, x] = True
            continue
        def apart(cells):
            """Per known patch, its mean squared difference per value to this patch, over these cells of it."""
            weight = np.repeat(cells, 4).astype(np.float32)
            target = art[here].reshape(-1) * weight
            return (squares @ weight - 2 * (stack @ target) + float(target @ target)) / float(weight.sum())

        ground = apart(have)
        line = (ruled[here] & todo[here]).reshape(k * k)             # drawn lines the patch would lie on
        if line.any():
            ground = np.where(apart(line) > fit * fit, np.inf, ground)     # a patch that does not agree with them is none
        if brings is not None:                                       # nor is one that would bring such a line to where the place has none
            ground = np.where(brings @ (todo[here] & ~ruled[here]).reshape(k * k).astype(np.float32) > 0, np.inf, ground)
        pick = int(np.argmin(ground + far * ((cy + r - y) ** 2 + (cx + r - x) ** 2)))
        if ground[pick] > fit * fit:
            skipped[y, x] = True
            continue
        open_ = todo[here] & ~ruled[here]
        art[here][open_] = stack[pick].reshape(k, k, 4)[open_]
        sure[here][todo[here]] = trust[first]
        todo[here] = False
        skipped[max(0, y - k):y + k + 1, max(0, x - k):x + k + 1] = False     # with more known around them, their turn may come
    filled = hole[top:bottom, left:right] & ~todo[r:tall - r, r:wide - r]
    out[top:bottom, left:right][filled] = art[r:tall - r, r:wide - r][filled].astype(np.uint8)
    left_open = hole.copy()
    left_open[top:bottom, left:right] &= ~filled
    return left_open


def _common(out, hole, window=3, walls=None):
    """Fill a hole cell by cell, in place, with the colours that are common around it.

    Each cell of the hole looks at the nearest cell outside. If that cell is empty, the cell stays empty, so
    a figure on empty ground does not grow where a piece of it was taken away. Otherwise it draws one of
    the colours that are common around that cell, by their shares: the grain of the surroundings carries on,
    and a lone highlight that passes close by is a minority and stays out. A cell that then stands out from
    the middle tone around it takes a colour nearer to that tone.

    `walls` are lines that were drawn through the hole before, in the hole and outside it. They are not the
    ground: no cell takes its colours from one. And the ground does not spread across them: each side
    of a line is filled from its own side, in the colours that are common on that side. Only a place that
    lines close in on all sides is filled from beyond them.
    """
    image = out.copy()
    h, w = hole.shape
    walls = np.zeros_like(hole) if walls is None else walls
    # the nearest cell outside, for every cell: what is known spreads inwards ring by ring
    ys, xs = np.indices((h, w))
    known = ~hole & ~walls
    near_y, near_x = np.where(known, ys, -1), np.where(known, xs, -1)
    for shut in (walls, np.zeros_like(hole)):                         # around the walls first, then through them
        while not known.all():
            grown, grown_y, grown_x = known.copy(), near_y.copy(), near_x.copy()
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                here = (slice(max(0, -dy), h + min(0, -dy)), slice(max(0, -dx), w + min(0, -dx)))
                there = (slice(max(0, dy), h + min(0, dy)), slice(max(0, dx), w + min(0, dx)))
                take = known[there] & ~grown[here] & ~shut[here]
                if dy and dx:                                          # and not between two cells of a slanted line
                    take &= ~(shut[(there[0], here[1])] & shut[(here[0], there[1])])
                grown_y[here][take], grown_x[here][take] = near_y[there][take], near_x[there][take]
                grown[here] |= take
            if (grown == known).all():
                break
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
        around = art[box]
        if walls[box].any():                                       # what lies beyond a line is another ground
            side = np.zeros_like(around)
            side[y - box[0].start, x - box[1].start] = True
            while True:
                grown = side | _neighbours(side) & ~walls[box]
                if (grown == side).all():
                    break
                side = grown
            around = around & side
        colours, counts = np.unique(code[box][around], return_counts=True)
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
