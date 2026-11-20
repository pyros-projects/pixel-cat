"""Frames in and out: stills, GIFs, videos and sprite sheets in; PNG, GIF, MP4 and WebM out.

Videos go through FFmpeg, which has to be on the PATH. Everything else needs only Pillow.
"""

import json
import mimetypes
import shutil
import subprocess

import numpy as np
from PIL import Image

from . import stills

MAX_FRAMES = 256
VIDEO_FRAMES = 24          # frames taken from a video unless the recipe asks for another count
TICK = 20                  # video output repeats frames on a 20 ms clock (50 frames per second)


def kind(path):
    suffix = path.suffix.lower()
    if suffix == ".gif":
        return "gif"
    if (mimetypes.guess_type(path.name)[0] or "").startswith("video/") or suffix in (".m4v", ".ogv", ".mkv"):
        return "video"
    return "still"


def ffmpeg(*args, **kwargs):
    tool = shutil.which(args[0])
    if not tool:
        raise RuntimeError(f"{args[0]} is not installed. Videos need FFmpeg.")
    return subprocess.run([tool, *args[1:]], check=True, capture_output=True, **kwargs)


def read(path, count="all", sheet=None):
    """Load frames and their durations in milliseconds. `count` thins an animation out evenly."""
    what = kind(path)
    if sheet:
        image = stills.load(path)
        cell_h, cell_w = image.shape[0] // sheet["rows"], image.shape[1] // sheet["columns"]
        if not cell_h or not cell_w:
            raise ValueError("The sprite sheet is smaller than its grid")
        frames = [image[r * cell_h:(r + 1) * cell_h, c * cell_w:(c + 1) * cell_w].copy()
                  for r in range(sheet["rows"]) for c in range(sheet["columns"])]
        return frames, [sheet.get("delay", 120)] * len(frames)
    if what == "still":
        return [stills.load(path)], [120]
    if what == "gif":
        with Image.open(path) as gif:
            total = getattr(gif, "n_frames", 1)
            frames, durations = [], []
            for index in range(total):
                gif.seek(index)
                frames.append(np.array(gif.convert("RGBA"), dtype=np.uint8))
                durations.append(gif.info.get("duration") or 100)
        wanted = total if count == "all" else min(total, count)
        wanted = min(wanted, MAX_FRAMES)
        picks = [(i * total // wanted, (i + 1) * total // wanted) for i in range(wanted)]
        return [frames[a] for a, _ in picks], [sum(durations[a:b]) for a, b in picks]
    probe = json.loads(ffmpeg("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(path)).stdout)
    stream, seconds = probe["streams"][0], float(probe["format"]["duration"])
    w, h = stream["width"], stream["height"]
    if count == "all":
        top, bottom = (float(n) for n in stream["avg_frame_rate"].split("/"))
        wanted = min(MAX_FRAMES, max(1, round(seconds * top / bottom)))
    else:
        wanted = min(MAX_FRAMES, count)
    raw = ffmpeg("ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={wanted}/{seconds}", "-frames:v", str(wanted),
                 "-f", "rawvideo", "-pix_fmt", "rgba", "-").stdout
    frames = [f.copy() for f in np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 4)]
    return frames, [max(TICK, round(seconds * 1000 / len(frames)))] * len(frames)


def stabilise(images, weights, tolerance):
    """Stop still areas from flickering. Returns the number of cells settled.

    A cell that is opaque in every frame and whose colour wanders by at most `tolerance` per channel over the
    whole loop is not moving, it is noise. It takes one colour for all frames: the one it really showed that
    is closest to its average over time. Cells that change more are motion and stay untouched.
    """
    if len(images) < 2 or tolerance <= 0:
        return 0
    stack = np.stack(images)
    rgb = stack[..., :3].astype(np.float64)
    low, high = stack[..., :3].min(axis=0), stack[..., :3].max(axis=0)
    calm = (stack[..., 3] == 255).all(axis=0) & ((high - low) <= tolerance).all(axis=2) & (high != low).any(axis=2)
    share = np.asarray(weights, dtype=np.float64) / sum(weights)
    centre = np.tensordot(share, rgb, axes=1)
    gap = ((rgb - centre[None]) ** 2).sum(axis=3) + stills.codes(stack[..., :3]) * 1e-9      # ties: the smaller colour
    best = np.take_along_axis(stack[..., :3], gap.argmin(axis=0)[None, ..., None], axis=0)[0]
    for image in images:
        image[..., :3][calm] = best[calm]
    return int(calm.sum())


def enlarge(image, scale):
    return image if scale == 1 else np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)


def write_png(image, path, scale):
    Image.fromarray(enlarge(image, scale)).save(path, format="PNG", optimize=True)


def write_gif(images, durations, path, scale):
    """One shared colour table for the whole loop; transparency takes one entry of it."""
    found, counts, see_through = stills.histogram(list(zip(images, durations)))
    table = stills.median_cut(found, counts, 256 - int(see_through))
    flat = [0] * 768
    flat[:len(table) * 3] = [int(v) for v in table.reshape(-1)]
    frames = []
    empty = len(table) if see_through else 0         # with no gaps every cell gets a colour, and the table may be full
    for image in images:
        indexed = np.full(image.shape[:2], empty, dtype=np.uint8)
        solid = image[..., 3] >= 128
        indexed[solid] = stills.nearest(image[..., :3][solid], table)
        frame = Image.fromarray(enlarge(indexed, scale), mode="P")
        frame.putpalette(flat)
        frames.append(frame)
    extra = {"transparency": len(table), "disposal": 2} if see_through else {}
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:], loop=0, optimize=False,
                   duration=[max(TICK, round(d / 10) * 10) for d in durations], **extra)


def write_video(images, durations, path, scale, codec):
    """Transparent cells become black. Sides are padded to even numbers, which the encoders need."""
    h, w = images[0].shape[0] * scale, images[0].shape[1] * scale
    even_w, even_h = w + w % 2, h + h % 2
    encoder = (["-c:v", "libx264", "-crf", "14", "-preset", "slow", "-movflags", "+faststart"] if codec == "mp4"
               else ["-c:v", "libvpx-vp9", "-crf", "18", "-b:v", "0"])
    tool = shutil.which("ffmpeg")
    if not tool:
        raise RuntimeError("ffmpeg is not installed. MP4 and WebM need FFmpeg.")
    process = subprocess.Popen([tool, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{even_w}x{even_h}",
                                "-r", str(1000 // TICK), "-i", "-", *encoder, "-pix_fmt", "yuv420p", "-f", codec, str(path)],
                               stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for image, duration in zip(images, durations):
            solid = (image[..., 3:4] >= 128)
            frame = np.pad(enlarge(image[..., :3] * solid, scale), ((0, even_h - h), (0, even_w - w), (0, 0)), mode="edge")
            process.stdin.write(frame.tobytes() * max(1, round(duration / TICK)))
        process.stdin.close()
    except BrokenPipeError:
        pass
    complaint = process.stderr.read().decode(errors="replace").strip()
    process.stderr.close()
    if process.wait():
        raise RuntimeError("FFmpeg could not encode the video: " + complaint[-300:])
