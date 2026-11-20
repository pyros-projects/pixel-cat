"""Render a cutscene from pixel-cat plates, moving cutouts and timed sound cues."""

import argparse
import itertools
import json
import math
import os
import random
import subprocess
import tempfile
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from sound import soundtrack

NEAREST = Image.Resampling.NEAREST
TAU = math.tau


def sample(keys, time):
    """Smoothly interpolate timestamped rows. Hold the first and last poses."""
    if time <= keys[0][0]:
        return keys[0][1:]
    for left, right in itertools.pairwise(keys):
        if time <= right[0]:
            weight = (time - left[0]) / (right[0] - left[0])
            weight *= weight * (3 - 2 * weight)
            return [
                a + (b - a) * weight for a, b in zip(left[1:], right[1:], strict=True)
            ]
    return keys[-1][1:]


def lift(plate, polygon):
    """Lift a painted prop and extend nearby background colors into the hole."""
    mask = Image.new("L", plate.size)
    ImageDraw.Draw(mask).polygon([tuple(p) for p in polygon], fill=255)
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError("Cutout misses its plate")
    actor = plate.crop(bounds).convert("RGBA")
    actor.putalpha(mask.crop(bounds))
    x0, y0, x1, y1 = bounds
    box = (
        max(0, x0 - 2),
        max(0, y0 - 2),
        min(plate.width, x1 + 2),
        min(plate.height, y1 + 2),
    )
    patch = plate.crop(box)
    unknown = mask.crop(box)
    flags, colors = unknown.load(), patch.load()
    queue = deque(
        (x, y)
        for y in range(patch.height)
        for x in range(patch.width)
        if not flags[x, y]
    )
    while queue:
        x, y = queue.popleft()
        for xx, yy in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= xx < patch.width and 0 <= yy < patch.height and flags[xx, yy]:
                flags[xx, yy] = 0
                colors[xx, yy] = colors[x, y]
                queue.append((xx, yy))
    plate.paste(patch.filter(ImageFilter.MedianFilter(3)), box[:2], mask.crop(box))
    return actor


def scale_layer(layer, time):
    source, cfg = layer
    x, y, sx, sy, degrees, opacity = sample(cfg["pose"], time)
    if "sway" in cfg:
        hz, dx, dy, dsx, dsy, turn = cfg["sway"]
        wave = math.sin(time * TAU * hz)
        x, y, sx, sy, degrees = (
            x + dx * wave,
            y + dy * wave,
            sx + dsx * wave,
            sy + dsy * wave,
            degrees + turn * wave,
        )
    width = cfg.get("width", source.width)
    art = source.resize(
        (
            max(1, round(width * sx)),
            max(1, round(width * source.height / source.width * sy)),
        ),
        NEAREST,
    )
    if cfg.get("ripple"):
        amount = cfg["ripple"]
        bent = Image.new("RGBA", art.size)
        for row in range(0, art.height, 2):
            depth = row / art.height
            shift = round(
                amount * depth**2 * math.sin(TAU * (depth * 1.4 - time * 0.65))
            )
            bent.paste(
                art.crop((0, row, art.width, min(row + 2, art.height))), (shift, row)
            )
        art = bent
    if opacity < 1:
        art.putalpha(art.getchannel("A").point(lambda a: round(a * max(0, opacity))))
    fx, fy = cfg.get("pivot", [0.5, 0.5])
    pivot = (round(art.width * fx), round(art.height * fy))
    if abs(degrees) > 0.05:
        # A square around the pivot keeps corners intact during rotation.
        radius = math.ceil(
            math.hypot(
                max(pivot[0], art.width - pivot[0]),
                max(pivot[1], art.height - pivot[1]),
            )
        )
        tile = Image.new("RGBA", (radius * 2 + 1, radius * 2 + 1))
        tile.paste(art, (radius - pivot[0], radius - pivot[1]))
        art = tile.rotate(degrees, NEAREST, center=(radius, radius))
        pivot = (radius, radius)
    return art, (round(x - pivot[0]), round(y - pivot[1]))


def deform(plate, cfg, time):
    """Move a painted prop with its immediate surroundings, keeping the edges joined."""
    dx, dy = sample(cfg["move"], time)
    cx, cy = cfg["center"]
    rx, ry = cfg["radius"]
    w, h = plate.size

    def source(x, y):
        distance = min(1, ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)
        weight = (1 - distance) ** 2 * (1 + 2 * distance)
        if "hinge_y" in cfg:
            weight *= min(1, max(0, (y - cfg["hinge_y"]) / (cy - cfg["hinge_y"])))
        sx = x if x in (0, w) else min(w, max(0, x - dx * weight))
        sy = y if y in (0, h) else min(h, max(0, y - dy * weight))
        return sx, sy

    mesh = []
    for y in range(0, h, 12):
        for x in range(0, w, 12):
            right, bottom = min(x + 12, w), min(y + 12, h)
            quad = (
                source(x, y)
                + source(x, bottom)
                + source(right, bottom)
                + source(right, y)
            )
            mesh.append(((x, y, right, bottom), quad))
    return plate.transform(plate.size, Image.Transform.MESH, mesh, NEAREST)


def light(frame, fx, time):
    (strength,) = sample(fx["level"], time)
    if strength <= 0:
        return frame
    strength *= 1 + fx.get("flicker", 0) * (
        0.6 * math.sin(23 * time) + 0.4 * math.sin(37 * time)
    )
    x, y = sample(fx["path"], time) if "path" in fx else fx["xy"]
    rx, ry = fx["radius"]
    overlay = Image.new("RGB", frame.size)
    pen = ImageDraw.Draw(overlay)
    for band in range(9, 0, -1):
        r = band / 9
        color = tuple(
            round(min(255, max(0, c * strength * (1.04 - r * 0.88) ** 1.5)))
            for c in fx["color"]
        )
        pen.ellipse((x - rx * r, y - ry * r, x + rx * r, y + ry * r), fill=color)
    return ImageChops.screen(frame, overlay)


def paint_effect(frame, fx, time, seeds):
    start = fx.get("start", 0)
    if time < start or time >= fx.get("end", float("inf")):
        return frame
    if fx["kind"] == "light":
        return light(frame, fx, time)
    pen = ImageDraw.Draw(frame, "RGBA")
    color = tuple(fx["color"])
    if fx["kind"] == "stroke":
        points = fx["points"]
        (progress,) = sample(fx["level"], time)
        extent = max(0, min(1, progress)) * (len(points) - 1)
        index = int(extent)
        if extent > 0:
            path = [tuple(p) for p in points[: index + 1]]
            if index + 1 < len(points):
                a, b = points[index : index + 2]
                path.append(
                    tuple(round(x + (y - x) * (extent - index)) for x, y in zip(a, b))
                )
            if len(path) > 1:
                pen.line(
                    path, fill=color + (fx.get("alpha", 255),), width=fx.get("width", 1)
                )
        return frame
    if fx["kind"] == "ring":
        phase = (time - start) / (fx["end"] - start)
        x, y = fx["xy"]
        rx, ry = [v * phase for v in fx["radius"]]
        pen.ellipse(
            (x - rx, y - ry, x + rx, y + ry),
            outline=color + (round(205 * (1 - phase)),),
            width=fx.get("width", 1),
        )
        return frame
    if fx["kind"] != "particles":
        raise ValueError("Unknown effect " + fx["kind"])
    left, top, right, bottom = fx["box"]
    dx, dy = fx["velocity"]
    life = fx["life"]
    for phase, a, b, c in seeds:
        age = ((time - start) / life + phase) % 1
        fade = min(1, age * 6, (1 - age) * 5)
        x = round(
            left
            + (right - left) * a
            + dx * age * life * (0.8 + 0.4 * c)
            + fx.get("drift", 0) * math.sin(TAU * (age + b))
        )
        y = round(top + (bottom - top) * b + dy * age * life * (0.8 + 0.4 * c))
        if "path" in fx:
            # Sample the emitter at birth so a moving engine leaves a trail.
            px, py = sample(fx["path"], time - age * life)
            x, y = round(x + px), round(y + py)
        ink = color + (round(fx.get("alpha", 180) * fade * (0.5 + 0.5 * c)),)
        shape = fx.get("shape", "dot")
        if shape == "rain":
            pen.line((x, y, x + round(dx * 0.045), y + round(dy * 0.045)), fill=ink)
        elif shape == "bubble":
            radius = 1 + int(c * 2)
            pen.ellipse((x - radius, y - radius, x + radius, y + radius), outline=ink)
            pen.point((x - 1, y - 1), fill=ink)
        elif shape == "steam":
            r = 1 + round(age * fx.get("spread", 8))
            pen.ellipse((x - r, y - r, x + r, y + r), fill=ink)
        elif shape == "spark" and c > 0.6:
            pen.line((x - 1, y, x + 1, y), fill=ink)
            pen.line((x, y - 2, x, y + 2), fill=ink)
        else:
            pen.point((x, y), fill=ink)
    return frame


def images(folder, shot, size, fps, seed):
    with Image.open(folder / shot["plate"]) as file:
        plate = file.convert("RGB")
    if plate.size != size:
        raise ValueError(f"{shot['plate']} is {plate.size}, expected {size}")
    foreground = None
    if "foreground" in shot:
        mask = Image.new("L", size)
        pen = ImageDraw.Draw(mask)
        for poly in shot["foreground"]:
            pen.polygon([tuple(point) for point in poly], fill=255)
        foreground = plate.copy(), mask
    layers = []
    for cfg in shot.get("layers", []):
        if "cutout" in cfg:
            art = lift(plate, cfg["cutout"])
        else:
            with Image.open(folder / cfg["image"]) as file:
                art = file.convert("RGBA")
        layers.append((art, cfg))
    effects = shot.get("effects", [])
    rng = random.Random(seed)
    seeds = [
        [tuple(rng.random() for _ in range(4)) for _ in range(fx.get("count", 0))]
        for fx in effects
    ]
    w, h = size
    for index in range(round(shot["seconds"] * fps)):
        time = index / fps
        frame = (
            deform(plate, shot["deform"], time) if "deform" in shot else plate.copy()
        )
        for layer in layers:
            art, where = scale_layer(layer, time)
            frame.paste(art, where, art)
        for fx, points in zip(effects, seeds):
            frame = paint_effect(frame, fx, time, points)
        if foreground:
            frame.paste(foreground[0], (0, 0), foreground[1])
        yield frame


def window(shot, time, size, smooth):
    """The part of the drawing the camera shows: left, top, width, height in cells."""
    w, h = size
    if "camera" not in shot:
        return 0, 0, w, h
    cx, cy, zoom = sample(shot["camera"], time)
    if not smooth:
        cw, ch = min(w, round(w / zoom)), min(h, round(h / zoom))
        return min(w - cw, max(0, round(cx - cw / 2))), min(h - ch, max(0, round(cy - ch / 2))), cw, ch
    cw, ch = min(w, w / zoom), min(h, h / zoom)
    return min(w - cw, max(0, cx - cw / 2)), min(h - ch, max(0, cy - ch / 2)), cw, ch


def look(frame, view, scale, smooth):
    """Show a window of the drawing at output size.

    A smooth camera is not bound to the grid: it rests between cells and zooms by any factor. Each output
    pixel shows the average of the patch it covers. Inside a cell that is the cell's own colour; only an
    output pixel that straddles two cells blends them. Cells stay sharp and their edges do not crawl when
    the camera moves. A grid camera snaps to whole cells, which makes zooms step and shimmer.
    """
    art = np.asarray(frame)
    h, w = art.shape[:2]
    x, y, cw, ch = view
    if not smooth:
        cols, rows = x + np.arange(w) * cw // w, y + np.arange(h) * ch // h
        return np.repeat(np.repeat(art[rows][:, cols], scale, axis=0), scale, axis=1)
    if (x, y, cw, ch) == (0, 0, w, h):
        return np.repeat(np.repeat(art, scale, axis=0), scale, axis=1)

    def taps(start, length, cells, pixels):
        low = start + np.arange(pixels) * (length / pixels)
        high = low + length / pixels
        first = np.clip(np.floor(low).astype(int), 0, cells - 1)
        second = np.clip(np.ceil(high).astype(int) - 1, 0, cells - 1)
        return first, second, np.clip((first + 1 - low) / (high - low), 0, 1).astype(np.float32)

    top, bottom, share = taps(y, ch, h, h * scale)
    art = art[top] * share[:, None, None] + art[bottom] * (1 - share)[:, None, None]
    left, right, share = taps(x, cw, w, w * scale)
    art = art[:, left] * share[None, :, None] + art[:, right] * (1 - share)[None, :, None]
    return np.rint(art).astype(np.uint8)


def render(story, destination):
    s = json.loads(story.read_text())
    fps, scale, size = s["fps"], s["scale"], tuple(s["size"])
    smooth = s.get("lens", "smooth") == "smooth"
    if s.get("lens", "smooth") not in ("smooth", "grid"):
        raise ValueError('lens is "smooth" or "grid"')
    duration = sum(shot["seconds"] for shot in s["shots"])
    if fps not in (6, 8, 12, 24) or any(
        abs(shot["seconds"] * fps - round(shot["seconds"] * fps)) > 1e-8
        for shot in s["shots"]
    ):
        raise ValueError(
            "Choose a drawing rate dividing 24 and cuts on whole drawing frames."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        wav = Path(tmp) / "sound.wav"
        soundtrack(wav, duration, s["sound"], s["seed"])
        mp4 = Path(tmp) / "film.mp4"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-filter_threads",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{size[0] * scale}x{size[1] * scale}",
            "-framerate",
            "24",
            "-i",
            "pipe:0",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "1",
            "-preset",
            "medium",
            "-crf",
            "17",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-t",
            str(duration),
            "-movflags",
            "+faststart",
            str(mp4),
        ]
        with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
            try:
                # The drawing changes at the drawing rate. A smooth camera moves on every one of the 24 frames.
                for i, shot in enumerate(s["shots"]):
                    for index, frame in enumerate(images(story.parent, shot, size, fps, s["seed"] + i)):
                        for step in range(24 // fps):
                            time = (index + (step * fps / 24 if smooth else 0)) / fps
                            encoder.stdin.write(look(frame, window(shot, time, size, smooth), scale, smooth).tobytes())
            finally:
                encoder.stdin.close()
            if encoder.wait():
                raise RuntimeError("FFmpeg could not encode the film")
        os.replace(mp4, destination)
    return {
        "file": str(destination),
        "seconds": duration,
        "drawings": round(duration * fps),
        "frames": round(duration * 24),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.story, args.output)))
