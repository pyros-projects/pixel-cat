"""The command line. Every command prints one JSON object; errors go to stderr as JSON with exit code 1."""

import argparse
import json
import signal
import sys
from pathlib import Path

from .edits import OPS
from .jobs import execute
from .loops import KINDS
from .options import MODES, OPTIONS, SAMPLING
from .palettes import PALETTES
from .project import OPERATIONS


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
    check = cmds.add_parser("check", help="look for flicker, stray fragments, holes and off-palette colours in an animation or a project")
    check.add_argument("input", help="a GIF, a video, a project or a picture")
    check.add_argument("--fragment", type=int, default=3, help="groups of up to this many cells count as fragments (default 3)")
    check.add_argument("--palette", help="a palette from catalog or comma-separated #RRGGBB colours; a project's own palette is used by default")
    check.add_argument("-o", "--output", help="a GIF or PNG that shows the findings tinted: flicker magenta, fragments yellow, holes cyan, off-palette red")
    check.add_argument("--frame", type=int, help="the frame for a PNG output (default: the frame with most findings)")
    check.add_argument("--scale", type=int, default=0, help="enlargement of the output (default: easy to look at)")
    studio = cmds.add_parser("studio", help="open a project in the studio: draw and animate in the browser, alone or with an agent")
    studio.add_argument("project", help="a .pixelcat file; it is created if it does not exist")
    studio.add_argument("--size", default="64x64", help="canvas of a new project, e.g. 32x32")
    studio.add_argument("--port", type=int, default=4178)
    studio.add_argument("--agent", action="store_true", help="an agent answers in the chat (it runs `pixel-cat wait`)")
    studio.add_argument("--open", action="store_true", help="open the page in the default browser")
    wait = cmds.add_parser("wait", help="for agents: block until the person writes in the studio's chat")
    wait.add_argument("--timeout", type=int, default=3300, help="seconds before giving up")
    say = cmds.add_parser("say", help="for agents: answer in the studio's chat (- reads the text from stdin)")
    say.add_argument("text")
    op = cmds.add_parser("op", help="for agents: change the open project with one operation as JSON (- reads stdin)")
    op.add_argument("operation")
    look = cmds.add_parser("look", help="for agents: see what the person has open; -o saves the frame as PNG")
    look.add_argument("-o", "--output")
    look.add_argument("--frame", type=int)
    look.add_argument("--layer", type=int)
    look.add_argument("--scale", type=int, default=8)
    cmds.add_parser("undo", help="for agents: take back the last change in the studio")
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
    convert.add_argument("--alpha", choices=("hard", "soft"), help="hard: every cell solid or empty (default); soft: shadows and glows keep their transparency")
    convert.add_argument("--scale", type=scale_value, help="enlarge the output by a whole number, or display")
    convert.add_argument("--frame", type=int, help="zero-based frame to export as PNG")
    convert.add_argument("--frames", help="how many frames to take from an animation, or all")
    convert.add_argument("--speed", type=int, help="playback speed in percent")
    convert.add_argument("--stability", type=int, help="how much a still cell may flicker before it counts as motion (0 to 255)")
    convert.add_argument("--cutout", action="store_true", help="make the background transparent")
    convert.add_argument("--trim", action="store_true", help="crop to the drawing")
    convert.add_argument("--outline", metavar="#RRGGBB", help="draw an outline around the drawing")
    convert.add_argument("--hold", metavar="X,Y,W,H[@FRAME]", help="animations: make this area stand still, copied from a frame (the first by default)")
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
    steps = ([{"op": "cutout"}] if args.cutout else []) + ([{"op": "trim"}] if args.trim else [])
    if args.outline:
        steps.append({"op": "outline", "color": args.outline})
    if args.hold:
        box, _, frame = args.hold.partition("@")
        steps.append({"op": "hold", "rect": [int(v) for v in box.split(",")], "from": int(frame or 0)})
    if steps:
        job["edits"] = steps
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
        "outputs": ["png", "gif", "mp4", "webm", "pixelcat (a project with layers and frames)"],
        "recipe": {"input": "path", "convert": "mode and its settings", "media": "frames, speed, stability",
                   "sheet": "columns, rows, delay", "edits": "steps, each with an op",
                   "loop": "seconds, fps, seed, regions (kind, area, amount)",
                   "export": "a path, or objects with path, scale, frame, format"},
        "edits": list(OPS),
        "project operations": OPERATIONS,
        "loop kinds": list(KINDS),
    }


def run_check(args):
    """Check a file as it is: nothing is converted, an animation is read frame by frame."""
    from . import check, frames
    from .options import integer
    from .project import SUFFIX, Project
    from .stills import harden

    path = Path(args.input).resolve(strict=True)
    palette = None
    if path.suffix.lower() == SUFFIX:
        project = Project.load(path)
        images, _ = project.animation()
        palette = project.palette or None
    else:
        images, _ = frames.read(path, "all" if frames.kind(path) != "video" else frames.VIDEO_FRAMES)
        images = [harden(image.copy()) for image in images]
    if args.palette:
        palette = [c.strip() for c in args.palette.split(",")] if "," in args.palette else PALETTES.get(args.palette)
        if not palette:
            raise ValueError(f"Unknown palette: {args.palette}. See `pixel-cat catalog`.")
    marks = check.inspect(images, palette, integer(args.fragment, 1, 12, "fragment"))
    result = {"input": str(path), "width": images[0].shape[1], "height": images[0].shape[0], **check.report(marks)}
    if args.output:
        target = Path(args.output).resolve()
        scale = args.scale or max(1, min(24, 640 // max(images[0].shape[:2])))
        shown = check.marked(images, marks, scale)
        if target.suffix.lower() == ".gif":
            frames.write_gif(shown, [250] * len(shown), target, 1)                 # slow enough to read every frame
        elif target.suffix.lower() == ".png":
            worst = max(range(len(images)), key=lambda f: sum(result["frames"][f][k] for k in check.KINDS))
            frames.write_png(shown[worst if args.frame is None else integer(args.frame, 0, len(images) - 1, "frame")], target, 1)
        else:
            raise ValueError("The marked output is a GIF or a PNG")
        result["output"] = str(target)
    return result


def run_studio(args):
    import webbrowser

    from . import studio

    width, height = (int(n) for n in args.size.lower().split("x"))
    server, room = studio.serve(args.project, args.port, args.agent, (width, height))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    studio.STATE.parent.mkdir(parents=True, exist_ok=True)
    studio.STATE.write_text(json.dumps({"url": url, "project": str(room.path)}))
    print(json.dumps({"studio": "started", "url": url, "project": str(room.path), "agent": args.agent}), flush=True)
    if args.open:
        webbrowser.open(url)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))        # so that the note about the running studio is removed
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        studio.STATE.unlink(missing_ok=True)


def agent_command(args):
    """The commands an agent uses to work in a running studio."""
    import time

    from . import studio

    if args.command == "wait":
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            reply = studio.call("/api/wait?slice=25", timeout=40)
            if reply["messages"]:
                return "\n".join(studio.describe(m, reply["project"]) for m in reply["messages"])
        return "Nobody wrote for a while. Run `pixel-cat wait` again to keep listening."
    if args.command == "say":
        studio.call("/api/say", {"text": sys.stdin.read() if args.text == "-" else args.text})
        return None
    if args.command == "op":
        return json.dumps(studio.call("/api/op", {"op": json.loads(sys.stdin.read() if args.operation == "-" else args.operation), "by": "agent"}))
    if args.command == "undo":
        return json.dumps(studio.call("/api/undo", {"by": "agent"}))
    seen = studio.call("/api/look?as=json")
    if args.output:
        query = f"/api/look?scale={args.scale}" + (f"&frame={args.frame}" if args.frame is not None else "") + (f"&layer={args.layer}" if args.layer is not None else "")
        Path(args.output).write_bytes(studio.call(query, raw=True))
        seen["saved"] = str(Path(args.output).resolve())
    return json.dumps(seen)


def main():
    args = parser().parse_args()
    try:
        if args.command == "studio":
            return run_studio(args)
        if args.command in ("wait", "say", "op", "look", "undo"):
            text = agent_command(args)
            if text:
                print(text)
            return
        if args.command == "check":
            result = run_check(args)
        elif args.command == "catalog":
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
