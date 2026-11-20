"""The command line. Every command prints one JSON object; errors go to stderr as JSON with exit code 1."""

import argparse
import json
import sys
from pathlib import Path

from .jobs import execute
from .loops import KINDS
from .options import MODES, OPTIONS, SAMPLING
from .palettes import PALETTES


def grid_value(value):
    return value if value == "native" else int(value)


def scale_value(value):
    return value if value == "display" else int(value)


def parser():
    app = argparse.ArgumentParser(prog="pixel-cat", description="Turn images, GIFs, videos and sprite sheets into pixel art. Results are JSON.")
    cmds = app.add_subparsers(dest="command", required=True)
    cmds.add_parser("catalog", help="list palettes, modes and recipe options")
    run = cmds.add_parser("run", help="execute a JSON recipe, or read it from stdin with -")
    run.add_argument("recipe")
    convert = cmds.add_parser("convert", help="convert a still, GIF, video or sprite sheet")
    convert.add_argument("input")
    convert.add_argument("-o", "--output", required=True, help="a PNG, GIF, MP4 or WebM file")
    convert.add_argument("--mode", choices=MODES)
    convert.add_argument("--grid", type=grid_value, help="longest side in cells, or native")
    convert.add_argument("--sampling", choices=SAMPLING)
    convert.add_argument("--colors", type=int)
    convert.add_argument("--palette", help="source, a palette from catalog, or comma-separated #RRGGBB colours")
    convert.add_argument("--width", type=int, help="output width in faithful and artist mode, before snapping to whole cells")
    convert.add_argument("--pixel", type=int, help="size of one cell in faithful and artist mode")
    convert.add_argument("--strength", type=int, choices=(1, 2, 3), help="how freely artist mode interprets")
    convert.add_argument("--contrast", action="store_true", default=None, help="recommended mode: reduce the picture to its big shapes")
    convert.add_argument("--symmetry", choices=("off", "left", "right"))
    convert.add_argument("--scale", type=scale_value, help="enlarge the output by a whole number, or display")
    convert.add_argument("--frame", type=int, help="zero-based frame to export as PNG")
    convert.add_argument("--frames", help="how many frames to take from an animation, or all")
    convert.add_argument("--speed", type=int, help="playback speed in percent")
    convert.add_argument("--stability", type=int, help="how much a still cell may flicker before it counts as motion (0 to 255)")
    convert.add_argument("--loop", help="animate a still: comma-separated kinds, e.g. clouds,water (areas are guessed)")
    convert.add_argument("--seconds", type=float, help="length of the loop")
    convert.add_argument("--sheet", help="split a sprite sheet into columns x rows, e.g. 4x2")
    convert.add_argument("--delay", type=int, help="sprite sheet frame duration in milliseconds")
    return app


def convert_job(args):
    opts = vars(args)
    settings = {k: opts[k] for k in set.union(*OPTIONS.values(), {"mode"}) if opts.get(k) is not None}
    if "," in settings.get("palette", ""):
        settings["palette"] = [c.strip() for c in settings["palette"].split(",")]
    output = {"path": args.output}
    output.update({k: opts[k] for k in ("scale", "frame") if opts.get(k) is not None})
    job = {"input": args.input, "export": [output]}
    if settings:
        job["convert"] = settings
    if args.sheet:
        columns, rows = map(int, args.sheet.lower().split("x"))
        job["sheet"] = {"columns": columns, "rows": rows, "delay": args.delay or 120}
    elif args.delay is not None:
        raise ValueError("--delay needs --sheet")
    if args.loop:
        job["loop"] = {"regions": [{"kind": kind.strip()} for kind in args.loop.split(",")]}
        if args.seconds is not None:
            job["loop"]["seconds"] = args.seconds
    elif args.seconds is not None:
        raise ValueError("--seconds needs --loop")
    media = {k: opts[k] for k in ("frames", "speed", "stability") if opts[k] is not None}
    if media.get("frames", "all") != "all":
        media["frames"] = int(media["frames"])
    if media:
        job["media"] = media
    return job


def catalog():
    return {
        "palettes": {name: len(colours) for name, colours in PALETTES.items()},
        "modes": {mode: sorted(options) for mode, options in OPTIONS.items()},
        "sampling": list(SAMPLING),
        "inputs": ["png, jpg, webp and other stills", "gif", "video (needs FFmpeg)", "sprite sheet"],
        "outputs": ["png", "gif", "mp4", "webm"],
        "recipe": {"input": "path", "convert": "mode and its settings", "media": "frames, speed, stability",
                   "sheet": "columns, rows, delay", "loop": "seconds, fps, seed, regions (kind, area, amount)",
                   "export": "a path, or objects with path, scale, frame, format"},
        "loop kinds": list(KINDS),
    }


def main():
    args = parser().parse_args()
    try:
        if args.command == "catalog":
            result = catalog()
        elif args.command == "convert":
            result = execute(convert_job(args), Path.cwd())
        elif args.recipe == "-":
            result = execute(json.load(sys.stdin), Path.cwd())
        else:
            path = Path(args.recipe).resolve()
            result = execute(json.loads(path.read_text()), path.parent)
        print(json.dumps(result, ensure_ascii=False))
    except KeyboardInterrupt:
        return
    except Exception as exc:  # noqa: BLE001 - the command line reports every failure as JSON
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
