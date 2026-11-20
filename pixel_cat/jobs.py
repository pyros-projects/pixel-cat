"""Run one job: read the input, convert it, write every export, and report what happened."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from . import frames, recommend, stills
from .options import conversion, integer, keys
from .palettes import PALETTES

FORMATS = {".png": "png", ".gif": "gif", ".mp4": "mp4", ".webm": "webm"}


def execute(job, base):
    keys(job, {"input", "convert", "media", "sheet", "export"}, "recipe")
    path = (Path(base) / job["input"]).resolve(strict=True)
    settings = conversion(job.get("convert", {}))
    media, sheet = job.get("media", {}), job.get("sheet")
    keys(media, {"frames", "speed", "stability"}, "media")
    if media.get("frames", "all") != "all":
        integer(media["frames"], 1, frames.MAX_FRAMES, "frames")
    speed = integer(media.get("speed", 100), 25, 300, "speed")
    animated = frames.kind(path) != "still"
    if sheet is not None:
        keys(sheet, {"columns", "rows", "delay"}, "sheet")
        for name in ("columns", "rows"):
            integer(sheet[name], 1, 16, name)
        integer(sheet.get("delay", 120), 10, 60000, "delay")
        if animated:
            raise ValueError("A sprite sheet is a still image")
    elif media and not animated:
        raise ValueError("Frame settings need a GIF, a video or a sprite sheet")

    outputs = job.get("export", [])
    exports = []
    for item in [outputs] if isinstance(outputs, (dict, str)) else outputs:
        item = {"path": item} if isinstance(item, str) else dict(item)
        keys(item, {"path", "format", "frame", "scale"}, "export")
        target = (Path(base) / item["path"]).resolve()
        if target == path or any(t == target for t, _ in exports):
            raise ValueError("Inputs and outputs must have distinct paths")
        if target.exists() and not target.is_file():
            raise ValueError(f"Output must be a file: {target}")
        item["format"] = item.get("format", FORMATS.get(target.suffix.lower()))
        if item["format"] not in FORMATS.values():
            raise ValueError(f"Unknown output format for {target.name}. Use PNG, GIF, MP4 or WebM.")
        if "frame" in item and item["format"] != "png":
            raise ValueError("frame picks a single PNG out of an animation")
        if item.get("scale", "display") != "display":
            integer(item["scale"], 1, 64, "scale")
        exports.append((target, item))
    if not exports:
        raise ValueError("A recipe needs at least one export")

    count = media.get("frames", "all" if frames.kind(path) == "gif" else frames.VIDEO_FRAMES)
    source, durations = frames.read(path, count, sheet)
    durations = [max(10, round(d * 100 / speed)) for d in durations]
    moving = len(source) > 1
    summary = {"input": str(path)}
    settle = lambda images: frames.stabilise(images, durations, integer(media.get("stability", 24), 0, 255, "stability"))  # noqa: E731
    if settings["mode"] == "recommended":
        chosen = recommend.plan(source, settings["contrast"])
        images = [recommend.sample(image, chosen) for image in source]
        if moving:
            summary["stabilized"] = settle(images)
        images = recommend.finish(images, chosen, durations)
        summary["recommendation"] = {k: chosen[k] for k in ("width", "height", "sampling", "colors", "cleanup", "contrast")}
    else:
        images = [stills.sample(image, settings) for image in source]
        if moving and settings["mode"] == "grid":
            summary["stabilized"] = settle(images)
        stills.finish(images, settings, stills.theme(settings, PALETTES), durations)
    height, width = images[0].shape[:2]
    display = stills.display_scale(settings, width, height)
    colours = {int(c) for image in images for c in np.unique(stills.codes(image[..., :3][image[..., 3] >= 128]))}
    summary.update(width=width, height=height, frames=len(images), durations=durations, colors=len(colours),
                   displayScale=display, outputs=[])

    staged = []
    try:
        for target, item in exports:
            fmt = item["format"]
            scale = item.get("scale", "display" if fmt != "gif" else 1)
            scale = display if scale == "display" else scale
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=target.parent, delete=False, suffix=target.suffix) as handle:
                temp = Path(handle.name)
            staged.append((temp, target))
            if fmt == "png":
                index = integer(item.get("frame", 0), 0, len(images) - 1, "frame")
                frames.write_png(images[index], temp, scale)
            elif fmt == "gif":
                frames.write_gif(images, durations, temp, scale)
            else:
                frames.write_video(images, durations, temp, scale, fmt)
            summary["outputs"].append({"path": str(target), "format": fmt, "bytes": temp.stat().st_size,
                                       "width": width * scale, "height": height * scale,
                                       "frames": 1 if fmt == "png" else len(images)})
        for temp, target in staged:
            os.replace(temp, target)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)
    return summary
