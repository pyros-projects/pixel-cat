"""Joints: a layer that turns around a pivot and can hang on another layer.

A layer with a joint is drawn once, at rest. Keys on single frames say how far it is turned and moved there;
the frames between follow from the keys. A joint that hangs on a parent is carried along by whatever the
parent does, the way a hand follows a forearm.

    "joint": {"pivot": [8.5, 16.5],          the point the layer turns around, in cells
              "parent": "L2",                the id of the layer it hangs on, or null
              "wrap": true,                  after the last key, the motion runs on into the first one
              "drag": {"frames": 2, "amount": 0.5},     lag behind the parent's turning, or null
              "sway": {"amount": 6, "cycles": 1, "phase": 0},     swing to and fro on its own, or null
              "keys": [{"angle": 0, "x": 0, "y": 0, "ease": "smooth"}, null, ...]}     one entry per frame

Angles are in degrees, clockwise. `ease` says how the motion leaves a key.
"""

import math

import numpy as np

from .options import integer

EASES = ("smooth", "linear", "hold", "anticipate", "overshoot")
BACK = 1.70158            # how far anticipate pulls back and overshoot shoots past
DEPTH = 32


def ease(kind, t):
    """Progress from one key to the next, for a share `t` of the way."""
    if kind == "linear":
        return t
    if kind == "hold":
        return 0.0
    if kind == "anticipate":                 # pulls back first, then goes
        return t * t * ((BACK + 1) * t - BACK)
    if kind == "overshoot":                  # shoots past, then settles
        return 1 + (t - 1) ** 2 * ((BACK + 1) * (t - 1) + BACK)
    return t * t * (3 - 2 * t)


def number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} is a number from {low} to {high}")
    return float(value)


def point(value, width, height):
    """A pivot: [x, y] in cells. The centre of cell 8, 16 is [8.5, 16.5]."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("A pivot is [x, y] in cells; the centre of cell 8, 16 is [8.5, 16.5]")
    return [number(value[0], -width, 2 * width, "pivot x"), number(value[1], -height, 2 * height, "pivot y")]


def drag(value):
    if not value:
        return None
    if not isinstance(value, dict) or set(value) - {"frames", "amount"}:
        raise ValueError('drag is {"frames": 1 to 12, "amount": 0 to 2}, or null')
    amount = number(value.get("amount", 0.5), 0, 2, "amount")
    return {"frames": integer(value.get("frames", 2), 1, 12, "frames"), "amount": amount} if amount else None


def sway(value):
    """A swing on top of the pose: `amount` degrees to each side, `cycles` times per loop, starting `phase`
    degrees into the swing."""
    if not value:
        return None
    if not isinstance(value, dict) or set(value) - {"amount", "cycles", "phase"}:
        raise ValueError('sway is {"amount": degrees, "cycles": 1 to 12, "phase": degrees}, or null')
    amount = number(value.get("amount", 6), 0, 180, "amount")
    return {"amount": amount, "cycles": integer(value.get("cycles", 1), 1, 12, "cycles"), "phase": number(value.get("phase", 0), -360, 360, "phase")} if amount else None


def key(value):
    """One key, with every setting in place."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"angle", "x", "y", "ease"}:
        raise ValueError("A key holds angle, x, y and ease")
    if value.get("ease", "smooth") not in EASES:
        raise ValueError("ease is one of " + ", ".join(EASES))
    return {"angle": number(value.get("angle", 0), -3600, 3600, "angle"), "x": number(value.get("x", 0), -4096, 4096, "x"),
            "y": number(value.get("y", 0), -4096, 4096, "y"), "ease": value.get("ease", "smooth")}


def clean(value, frames, width, height):
    """A joint as read from a file, checked."""
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list) or len(value["keys"]) != frames:
        raise ValueError("A joint needs a pivot and one key entry per frame")
    return {"pivot": point(value.get("pivot"), width, height), "parent": value.get("parent") if isinstance(value.get("parent"), str) else None,
            "wrap": bool(value.get("wrap", True)), "drag": drag(value.get("drag")), "sway": sway(value.get("sway")),
            "keys": [key(k) for k in value["keys"]]}


def local(joint, frame, count):
    """The joint's own pose on a frame as (angle, x, y): a key, or what lies between the keys around it."""
    keyed = [(index, k) for index, k in enumerate(joint["keys"]) if k]
    if not keyed:
        return 0.0, 0.0, 0.0
    read = lambda k: (k["angle"], k["x"], k["y"])            # noqa: E731
    wrap = joint.get("wrap", True) and count > 1
    before, after = [e for e in keyed if e[0] <= frame], [e for e in keyed if e[0] > frame]
    if before:
        a = before[-1]
    elif wrap:
        a = (keyed[-1][0] - count, keyed[-1][1])
    else:
        return read(keyed[0][1])
    if a[0] == frame:
        return read(a[1])
    if after:
        b = after[0]
    elif wrap:
        b = (keyed[0][0] + count, keyed[0][1])
    else:
        return read(a[1])
    t = ease(a[1]["ease"], (frame - a[0]) / (b[0] - a[0]))
    return tuple(p + (q - p) * t for p, q in zip(read(a[1]), read(b[1])))


def matrix(pivot, angle, x, y):
    """Turn around the pivot, then move."""
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    px, py = pivot
    return np.array([[c, -s, px + x - c * px + s * py], [s, c, py + y - s * px - c * py], [0.0, 0.0, 1.0]])


def parent_of(layers, index):
    """The index of the layer a joint hangs on, or None."""
    wanted = layers[index]["joint"].get("parent")
    if wanted is None:
        return None
    return next((i for i, layer in enumerate(layers) if layer.get("id") == wanted and layer.get("joint") and i != index), None)


def chain(layers, index):
    """The layers a joint hangs on, nearest first."""
    out = []
    while True:
        index = parent_of(layers, index)
        if index is None or index in out or len(out) > DEPTH:
            return out
        out.append(index)


def hanging_on(layers, index):
    """Every layer that hangs on this one, directly or through others."""
    return [i for i, layer in enumerate(layers) if layer.get("joint") and index in chain(layers, i)]


def world(layers, index, frame, count, memo=None):
    """A jointed layer's pose on a frame, carried by its parents: (matrix, total angle)."""
    memo = {} if memo is None else memo
    if (index, frame) not in memo:
        joint = layers[index]["joint"]
        angle, x, y = local(joint, frame, count)
        swing = joint.get("sway")
        if swing:                             # whole swings per loop, so the last frame leads into the first
            angle += swing["amount"] * math.sin(math.tau * swing["cycles"] * frame / count + math.radians(swing["phase"]))
        carried, turned = np.eye(3), 0.0
        parent = parent_of(layers, index)
        if parent is not None and index not in chain(layers, parent):
            carried, turned = world(layers, parent, frame, count, memo)
            lag = joint.get("drag")
            if lag:                           # lag behind: turn against what the parent turned over the last frames
                earlier = (frame - lag["frames"]) % count if joint.get("wrap", True) else max(0, frame - lag["frames"])
                angle -= lag["amount"] * (turned - world(layers, parent, earlier, count, memo)[1])
        memo[(index, frame)] = (carried @ matrix(joint["pivot"], angle, x, y), turned + angle)
    return memo[(index, frame)]


def warp(image, m, k=4):
    """Turn and move pixel art without chewing it up.

    Every cell of the result is looked at in k by k places. It is filled when at least half of them land on
    art, and it takes the colour most of them found; in a close vote the colour at its centre decides, so a
    line one cell wide that falls between two cells stays whole.

    Without a turn, or with a quarter turn, cells land on cells: every cell takes the art under its centre, so
    the drawing arrives at the nearest whole cell exactly as drawn. Voting there would break up a fine pattern
    wherever a move ends between cells.
    """
    turn = np.round(m[:2, :2])
    if np.allclose(m[:2, :2], turn, atol=1e-9):
        m = np.array(m, dtype=np.float64)
        m[:2, :2] = turn
        m[:2, 2] = -turn @ np.floor(0.5 - turn.T @ m[:2, 2] + 1e-9)     # the whole-cell move that puts the same art under each centre
    if np.allclose(m, np.eye(3), atol=1e-9):
        return image
    h, w = image.shape[:2]
    out = np.zeros_like(image)
    ys, xs = np.nonzero(image[..., 3] > 0)
    if not len(xs):
        return out
    corners = m @ np.array([[xs.min(), xs.max() + 1, xs.min(), xs.max() + 1], [ys.min(), ys.min(), ys.max() + 1, ys.max() + 1], [1, 1, 1, 1]])
    x0, x1 = max(0, math.floor(corners[0].min())), min(w, math.ceil(corners[0].max()))
    y0, y1 = max(0, math.floor(corners[1].min())), min(h, math.ceil(corners[1].max()))
    if x1 <= x0 or y1 <= y0:
        return out
    code = np.where(image[..., 3] > 0, image[..., 0].astype(np.int64) << 24 | image[..., 1].astype(np.int64) << 16
                    | image[..., 2].astype(np.int64) << 8 | image[..., 3], -1)
    back = np.linalg.inv(m)

    def found(px, py):                       # the art under each place of the result, or -1
        sx = np.floor(back[0, 0] * px + back[0, 1] * py + back[0, 2]).astype(np.int64)
        sy = np.floor(back[1, 0] * px + back[1, 1] * py + back[1, 2]).astype(np.int64)
        inside = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        return np.where(inside, code[np.clip(sy, 0, h - 1), np.clip(sx, 0, w - 1)], -1)

    steps = (np.arange(k) + 0.5) / k
    ox, oy = np.tile(steps, k)[None, None, :], np.repeat(steps, k)[None, None, :]
    portion = max(1, 400_000 // (x1 - x0))                              # rows at a time, so a large picture stays cheap
    for top in range(y0, y1, portion):
        bottom = min(y1, top + portion)
        gx, gy = np.arange(x0, x1)[None, :, None], np.arange(top, bottom)[:, None, None]
        samples = found(gx + ox, gy + oy)                               # (rows, columns, k * k)
        centre = found(gx[..., 0] + 0.5, gy[..., 0] + 0.5)
        filled = (samples >= 0).sum(axis=2) * 2 >= k * k
        ordered = np.sort(samples, axis=2)
        first = np.concatenate([np.ones(ordered.shape[:2] + (1,), dtype=bool), ordered[..., 1:] != ordered[..., :-1]], axis=2)
        start = np.maximum.accumulate(np.where(first, np.arange(k * k), 0), axis=2)
        length = np.where(ordered >= 0, np.arange(k * k) - start + 1, 0)    # empty places do not vote
        winner = np.take_along_axis(ordered, length.argmax(axis=2)[..., None], axis=2)[..., 0]
        close = (centre >= 0) & ((samples == centre[..., None]).sum(axis=2) + 2 >= length.max(axis=2))
        winner = np.where(close, centre, winner)
        part = out[top:bottom, x0:x1]
        part[filled] = np.stack([winner[filled] >> 24 & 255, winner[filled] >> 16 & 255, winner[filled] >> 8 & 255, winner[filled] & 255], axis=1)
    return out
