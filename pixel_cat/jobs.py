"""Run one job: read the input, convert it, write every export, and report what happened."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from . import edits, frames, loops, recommend, stills
from .options import conversion, integer, keys
from .palettes import PALETTES
from .project import SUFFIX, Project

FORMATS = {".png": "png", ".gif": "gif", ".mp4": "mp4", ".webm": "webm", SUFFIX: "project"}


def convert(job, base):
    """Everything up to the finished frames. Returns (input path, images, durations, display scale, summary)."""
    keys(job, {"input", "convert", "media", "sheet", "edits", "loop", "export"}, "recipe")
    path = (Path(base) / job["input"]).resolve(strict=True)
    is_project = path.suffix.lower() == SUFFIX
    if is_project and ("convert" in job or "media" in job or "sheet" in job):
        raise ValueError("A project already is pixel art. It takes edits, a loop and exports, no conversion.")
    settings = conversion(job.get("convert", {}))
    media, sheet = job.get("media", {}), job.get("sheet")
    keys(media, {"frames", "speed", "stability"}, "media")
    if media.get("frames", "all") != "all":
        integer(media["frames"], 1, frames.MAX_FRAMES, "frames")
    speed = integer(media.get("speed", 100), 25, 300, "speed")
    animated = not is_project and frames.kind(path) != "still"
    if sheet is not None:
        keys(sheet, {"columns", "rows", "delay"}, "sheet")
        for name in ("columns", "rows"):
            integer(sheet[name], 1, 16, name)
        integer(sheet.get("delay", 120), 10, 60000, "delay")
        if animated:
            raise ValueError("A sprite sheet is a still image")
    elif media and not animated:
        raise ValueError("Frame settings need a GIF, a video or a sprite sheet")
    loop = job.get("loop")
    if loop is not None:
        keys(loop, {"seconds", "fps", "seed", "regions"}, "loop")
        if animated or sheet:
            raise ValueError("A loop is made from a still picture")
        if not 1 <= loop.get("seconds", 4) <= 20 or loop.get("fps", 8) not in (4, 6, 8, 10, 12):
            raise ValueError("A loop runs 1 to 20 seconds at 4, 6, 8, 10 or 12 frames per second")
        if not isinstance(loop.get("regions"), list) or not loop["regions"]:
            raise ValueError("A loop needs regions, each with a kind: " + ", ".join(loops.KINDS))
        for region in loop["regions"]:
            keys(region, {"kind", "area", "amount"}, "region")
            if region.get("kind") not in loops.KINDS:
                raise ValueError("A region's kind is one of " + ", ".join(loops.KINDS))
            area = region.get("area", "auto")
            flat = [v for point in area for v in (point if isinstance(point, list) else [point])] if isinstance(area, list) else None
            if area != "auto" and (flat is None or len(flat) < 4 or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in flat)):
                raise ValueError('A region\'s area is "auto", a box [left, top, right, bottom] or a polygon of [x, y] points, in fractions of the picture')
            if not 0 < region.get("amount", 1) <= 8:
                raise ValueError("A region's amount is a number up to 8")

    summary = {"input": str(path)}
    if is_project:
        images, durations = Project.load(path).animation()
    else:
        count = media.get("frames", "all" if frames.kind(path) == "gif" else frames.VIDEO_FRAMES)
        source, durations = frames.read(path, count, sheet)
        durations = [max(10, round(d * 100 / speed)) for d in durations]
        moving = len(source) > 1
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
    if job.get("edits"):
        images = edits.apply(images, job["edits"])
    if loop is not None:
        if len(images) > 1:
            raise ValueError("A loop is made from a single frame")
        images, durations = loops.animate(images[0], loop)
    height, width = images[0].shape[:2]
    display = max(1, min(24, 640 // max(width, height))) if is_project else stills.display_scale(settings, width, height)
    floor = stills.visible_from(settings)
    colours = {int(c) for image in images for c in np.unique(stills.codes(image[..., :3][image[..., 3] >= floor]))}
    summary.update(width=width, height=height, frames=len(images), durations=durations, colors=len(colours), displayScale=display)
    return path, images, durations, display, summary


def write(images, durations, target, fmt, scale):
    """Write one output file. PNG takes a single image in `images`."""
    if fmt == "png":
        frames.write_png(images[0], target, scale)
    elif fmt == "gif":
        frames.write_gif(images, durations, target, scale)
    elif fmt == "project":
        Project.from_frames(images, durations).save(target)
    else:
        frames.write_video(images, durations, target, scale, fmt)


def execute(job, base):
    path = (Path(base) / job.get("input", "")).resolve()
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
            raise ValueError(f"Unknown output format for {target.name}. Use PNG, GIF, MP4, WebM or a {SUFFIX} project.")
        if "frame" in item and item["format"] != "png":
            raise ValueError("frame picks a single PNG out of an animation")
        if "scale" in item and item["format"] == "project":
            raise ValueError("A project keeps one cell per pixel; scale applies to pictures and videos")
        if item.get("scale", "display") != "display":
            integer(item["scale"], 1, 64, "scale")
        exports.append((target, item))
    if not exports:
        raise ValueError("A recipe needs at least one export")

    path, images, durations, display, summary = convert(job, base)
    height, width = images[0].shape[:2]
    summary["outputs"] = []
    staged = []
    try:
        for target, item in exports:
            fmt = item["format"]
            scale = 1 if fmt == "project" else item.get("scale", "display" if fmt != "gif" else 1)
            scale = display if scale == "display" else scale
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=target.parent, delete=False, suffix=target.suffix) as handle:
                temp = Path(handle.name)
            staged.append((temp, target))
            if fmt == "png":
                write([images[integer(item.get("frame", 0), 0, len(images) - 1, "frame")]], durations, temp, fmt, scale)
            else:
                write(images, durations, temp, fmt, scale)
            summary["outputs"].append({"path": str(target), "format": fmt, "bytes": temp.stat().st_size,
                                       "width": width * scale, "height": height * scale,
                                       "frames": 1 if fmt == "png" else len(images)})
        for temp, target in staged:
            os.replace(temp, target)
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)
    return summary
