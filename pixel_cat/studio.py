"""The studio: a local page to draw and animate a project, alone or together with an agent.

The server owns the project. The page and the agent both change it through the same operations, and every
change reaches the page at once. One undo history covers both, so a person can take back what an agent did.

The chat is a wire between the page and an agent's own session: `pixel-cat wait` blocks until the person
writes, `pixel-cat say` answers, `pixel-cat op` changes the project, `pixel-cat look` shows the agent what the
person is looking at.
"""

import base64
import json
import os
import queue
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import frames, jobs, joints
from .project import SUFFIX, Project, png_bytes

STATE = Path(os.environ.get("PIXEL_CAT_STATE", Path.home() / ".cache" / "pixel-cat")) / "studio.json"
APP = Path(__file__).with_name("studio_app")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png"}


class Studio:
    def __init__(self, path, agent=False, size=(64, 64)):
        self.path = Path(path).resolve()
        self.agent = agent
        if self.path.exists():
            self.project = Project.load(self.path)
        else:
            self.project = Project(*size)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.project.save(self.path)
        self.lock = threading.RLock()
        self.run = secrets.token_hex(4)       # this run of the studio: a page that outlived another run can tell
        self.rev = 0
        self.undo, self.redo = [], []
        self.listeners = []
        self.chat = []
        self.inbox = queue.Queue()
        self.heard = 0.0                      # when an agent last waited for a message
        self.view = {"frame": 0, "layer": 0}  # what the person has open
        self.listening_shown = False
        self.turned = {}                      # turned cels as PNG text, kept while they are shown

    # ------------------------------------------------------------ events

    def publish(self, event):
        for listener in list(self.listeners):
            listener.put(event)

    def listening(self):
        return self.agent and time.time() - self.heard < 40

    def tick(self):
        """Tell the page when the agent starts or stops listening."""
        now = self.listening()
        if now != self.listening_shown:
            self.listening_shown = now
            self.publish({"type": "agent", "listening": now})

    # ------------------------------------------------------------ the project

    def looking(self, view):
        """What the person has open, as a page reports it, the way it stands in the project now. The page names
        its layer and its frame by their places in the lists it shows and sends those lists along as `layers`
        and `frames`; an agent reads the places they have now, the layer with its name, or none at all for one
        that is gone."""
        if not isinstance(view, dict):
            raise ValueError("What the person has open is told as an object: frame, layer and so on")
        seen, frames = view.get("layers"), view.get("frames")
        view = {key: view[key] for key in ("frame", "layer", "selection", "color", "tool") if key in view}
        with self.lock:
            place = view.get("frame")
            if isinstance(frames, list) and isinstance(place, int) and not isinstance(place, bool) and 0 <= place < len(frames):
                now = [frame["id"] for frame in self.project.frames]
                if frames[place] in now:
                    view["frame"] = now.index(frames[place])
                else:
                    del view["frame"]
            layers, place = self.project.layers, view.get("layer")
            if isinstance(seen, list) and isinstance(place, int) and not isinstance(place, bool) and 0 <= place < len(seen):
                now = [layer["id"] for layer in layers]
                view["layer"] = now.index(seen[place]) if seen[place] in now else None
            place = view.get("layer")
            if isinstance(place, int) and not isinstance(place, bool) and 0 <= place < len(layers):
                view["layerName"] = layers[place]["name"]
            else:
                view.pop("layer", None)
        return view

    def put(self, project):
        """Another project takes the place of this one. Its layers get ids that were never given here, so that
        a list of the old layers, which someone may still show, names none of the new ones."""
        project.renumber(self.project.next_layer, self.project.next_frame)
        self.project = project

    def meant(self, op, seen, frames=None):
        """An operation as its sender meant it. A page names a layer and a frame by their places in the lists
        it shows, and those lists can be older than the project: someone else may have added, removed or moved
        one since. `seen` is the sender's list of layer ids, from the bottom up, `frames` its list of frame
        ids. The places in the operation are read from them and turned into the places those layers and frames
        have now. A place between two (`to` when one is moved, `at` when one is added) is kept between the same
        two, and a layer is merged down only into the layer its sender saw below it."""
        if not isinstance(op, dict):
            return op
        gone = "is no longer there: the project was changed in the meantime"
        whole = lambda value: isinstance(value, int) and not isinstance(value, bool)      # noqa: E731

        def older(listed, now, what):
            """Whether the sender's list is another one than the present; a list that is none is an error."""
            if not isinstance(listed, list) or listed == now:
                return False
            if not all(isinstance(name, str) for name in listed) or len(set(listed)) != len(listed):
                raise ValueError(f"`{what}s` is the list of the {what}s the sender sees: their ids, each one once")
            return True

        def between(old, place, new, what):
            """The place in the list as it is now that lies right after the one `place` lay right after; at the
            very start, right before the one it lay right before."""
            if place > 0:
                if old[place - 1] not in new:
                    raise ValueError(f"The {what} before that place {gone}")
                return new.index(old[place - 1]) + 1
            return new.index(old[0]) if old and old[0] in new else 0

        meant, kind = dict(op), op.get("op")
        now = [layer["id"] for layer in self.project.layers]
        if older(seen, now, "layer"):
            for key in ("layer", "parent"):
                place = op.get(key)
                if whole(place) and 0 <= place < len(seen):
                    if seen[place] not in now:
                        raise ValueError(f"That layer {gone}")
                    meant[key] = now.index(seen[place])
            place = op.get("layer", 0)
            if kind == "merge-down" and whole(place) and 0 <= place < len(seen):
                if place == 0:
                    raise ValueError("The bottom layer has nothing below it")  # as the sender sees it; what lies below it now was not meant
                if meant["layer"] == 0 or now[meant["layer"] - 1] != seen[place - 1]:
                    raise ValueError("Another layer lies below it now: the project was changed in the meantime. Look again, then merge.")
            if kind == "move-layer" and whole(place) and 0 <= place < len(seen) and whole(op.get("to")) and 0 <= op["to"] < len(seen):
                moved = seen[place]
                if op["to"] == place:
                    meant["to"] = meant["layer"]                                # a move that changes nothing as the sender sees it changes nothing
                else:
                    meant["to"] = between([name for name in seen if name != moved], op["to"], [name for name in now if name != moved], "layer")
            if kind == "add-layer" and whole(op.get("at")) and 0 <= op["at"] <= len(seen):
                meant["at"] = between(seen, op["at"], now, "layer")
        now = [frame["id"] for frame in self.project.frames]
        if older(frames, now, "frame"):
            def one(place):
                """A frame number of the sender's as the number that frame has now."""
                if not whole(place) or not 0 <= place < len(frames):
                    return place
                if frames[place] not in now:
                    raise ValueError(f"That frame {gone}")
                return now.index(frames[place])

            several = lambda value: [one(place) for place in value] if isinstance(value, list) else one(value)      # noqa: E731
            for key in ("frame", "frames", "from", "copy"):          # "all" stays all
                if key in op:
                    meant[key] = several(op[key])
            if isinstance(op.get("spread"), dict) and "frames" in op["spread"]:
                meant["spread"] = dict(op["spread"], frames=several(op["spread"]["frames"]))
            if kind == "add-frame" and whole(op.get("at")) and 0 <= op["at"] <= len(frames):
                meant["at"] = between(frames, op["at"], now, "frame")
            if kind == "move-frame" and whole(op.get("to")):
                block = op["frames"] if isinstance(op.get("frames"), list) else [op.get("frame", 0)]
                moved = {frames[place] for place in block if whole(place) and 0 <= place < len(frames)}
                rest = [name for name in frames if name not in moved]
                if 0 <= op["to"] <= len(rest):
                    if rest[:op["to"]] + [name for name in frames if name in moved] + rest[op["to"]:] == frames:
                        first = min(now.index(name) for name in moved if name in now) if moved & set(now) else 0
                        meant["to"] = len([name for name in now[:first] if name not in moved])   # a move that changes nothing as the sender sees it changes nothing
                    else:
                        meant["to"] = between(rest, op["to"], [name for name in now if name not in moved], "frame")
        return meant

    def change(self, op, by, seen=None, frames=None):
        with self.lock:
            op = self.meant(op, seen, frames)
            before = self.project.snapshot()
            try:
                note = self.project.apply(op)
            except Exception:
                self.project = before                # an operation that fails halfway leaves nothing behind
                raise
            self.undo.append((before, note, by))
            del self.undo[:-200]
            self.redo.clear()
            done = self._changed(by, note)
            if op["op"] == "add-frame":
                previous = {frame["id"] for frame in before.frames}
                done["addedFrames"] = [frame["id"] for frame in self.project.frames if frame["id"] not in previous]
            return done

    def _changed(self, by, note):
        self.rev += 1
        self.project.save(self.path)
        self.publish({"type": "changed", "rev": self.rev, "by": by, "note": note})
        return {"rev": self.rev, "note": note}

    def preview(self, op, seen=None, frames=None):
        """What an operation would do, without doing it: per frame the cells that would change, and the
        changed cels of its layer."""
        with self.lock:
            op = self.meant(op, seen, frames)
            trial = self.project.snapshot()
            note = trial.apply(op)
            layer = int(op.get("layer", 0))
            marks, after = [], {}
            for frame in range(len(self.project.frames)):
                if frame >= len(trial.frames) or layer >= len(trial.layers) or layer >= len(self.project.layers) \
                        or trial.width != self.project.width or trial.height != self.project.height:
                    marks.append([])
                    continue
                old, new = self.project.cel(layer, frame), trial.cel(layer, frame)
                ys, xs = (old != new).any(axis=2).nonzero()
                marks.append([[int(x), int(y)] for x, y in zip(xs[:20000], ys[:20000])])
                if len(xs):
                    after[str(frame)] = base64.b64encode(png_bytes(new)).decode()
        return {"note": note, "layer": layer, "marks": marks, "after": after}

    def make_loop(self, spec, frame, by, frames=None):
        """Turn one frame, as it looks, into a seamless loop that takes the place of the frames. `frames` is
        the sender's list of frame ids, if it names the frame by its place there."""
        from . import loops
        from .options import keys

        keys(spec, {"seconds", "fps", "seed", "regions"}, "loop")
        if not 1 <= spec.get("seconds", 4) <= 20 or spec.get("fps", 8) not in (4, 6, 8, 10, 12):
            raise ValueError("A loop runs 1 to 20 seconds at 4, 6, 8, 10 or 12 frames per second")
        if not isinstance(spec.get("regions"), list) or not spec["regions"]:
            raise ValueError("A loop needs at least one region")
        loops.validate(spec["regions"])
        with self.lock:
            frame = self.meant({"frame": frame}, None, frames)["frame"]
            before = self.project.snapshot()
            still = self.project.composite(max(0, min(len(self.project.frames) - 1, int(frame))))
            images, durations = loops.animate(still, spec)
            self.put(Project.from_frames(images, durations, "Loop"))
            if before.palette and not self.project.palette:
                self.project.palette = before.palette
            note = f"made a loop of {len(images)} frames from frame {frame}"
            self.undo.append((before, note, by))
            self.redo.clear()
            return self._changed(by, note)

    def step(self, back, by):
        with self.lock:
            source, target = (self.undo, self.redo) if back else (self.redo, self.undo)
            if not source:
                raise ValueError("Nothing to undo" if back else "Nothing to redo")
            snapshot, note, who = source.pop()
            target.append((self.project.snapshot(), note, who))
            snapshot.next_layer = max(snapshot.next_layer, self.project.next_layer)   # an id that was given stays given, also when its layer is taken back
            snapshot.next_frame = max(snapshot.next_frame, self.project.next_frame)
            self.project = snapshot
            return self._changed(by, ("took back: " if back else "did again: ") + note)

    def state(self):
        with self.lock:
            return {"meta": {"name": self.path.name, "path": str(self.path), "agent": self.agent, "listening": self.listening(),
                             "run": self.run, "rev": self.rev, "undo": len(self.undo), "redo": len(self.redo),
                             "last": self.undo[-1][1:] if self.undo else None},
                    "doc": self.document(), "chat": self.chat[-200:]}

    def document(self):
        """The project for the page, as it is saved: each picture once, cels as numbers. A jointed layer also
        says how it appears on every frame: `shown` holds its turned cels, as numbers of further pictures (null
        where the drawing shows as it is), `at` its pose, `on` the layer it hangs on."""
        project = self.project
        doc, count = project.to_json(), len(project.frames)
        number, used = {text: n for n, text in enumerate(doc["pictures"])}, set()
        for index, (layer, out) in enumerate(zip(project.layers, doc["layers"])):
            if not layer.get("joint"):
                continue
            memo, out["on"], out["shown"], out["at"] = {}, joints.parent_of(project.layers, index), [], []
            for frame in range(count):
                pose, turn = joints.world(project.layers, index, frame, count, memo)
                angle, x, y = joints.local(layer["joint"], frame, count)
                pivot = pose @ [layer["joint"]["pivot"][0], layer["joint"]["pivot"][1], 1]
                cel, picture = layer["cels"][frame], project.shown(index, frame)
                if picture is None or picture is cel:
                    out["shown"].append(None)
                else:                                                 # packed once, and sent once however many frames show it
                    kept = self.turned.get(id(picture))
                    if kept is None or kept[0] is not picture:
                        kept = self.turned[id(picture)] = (picture, base64.b64encode(png_bytes(picture, small=False)).decode())
                    used.add(id(picture))
                    if kept[1] not in number:
                        number[kept[1]] = len(doc["pictures"])
                        doc["pictures"].append(kept[1])
                    out["shown"].append(number[kept[1]])
                out["at"].append({"angle": round(angle, 3), "x": round(x, 3), "y": round(y, 3), "turn": round(turn, 3),
                                  "pivot": [round(float(pivot[0]), 3), round(float(pivot[1]), 3)],
                                  "matrix": [round(float(v), 6) for v in (pose[0, 0], pose[1, 0], pose[0, 1], pose[1, 1], pose[0, 2], pose[1, 2])]})
        for gone in set(self.turned) - used:
            del self.turned[gone]
        return doc

    def say(self, who, text, **extra):
        message = {"from": who, "text": str(text)[:8000], "at": time.time(), **extra}
        self.chat.append(message)
        self.publish({"type": "chat", "message": message})
        return message

    def bring_in(self, data, name, settings, how):
        """Convert an uploaded picture, GIF or video and place it in the project. A project file comes in as
        it is: in place of this one, or after the last frame with its layers."""
        if Path(name).suffix.lower() == SUFFIX and how in ("project", "frames"):
            data = json.loads(data)
            other = Project.from_json(data)
            summary = {"width": other.width, "height": other.height, "frames": len(other.frames), "colors": len(other.palette)}
            with self.lock:
                if how == "frames":
                    return self.change({"op": "append", "project": data}, "you") | {"summary": summary, "recommendation": None}
                before = self.project.snapshot()
                self.put(other)
                self.undo.append((before, f"opened {name}", "you"))
                self.redo.clear()
                return self._changed("you", f"opened {name} with its layers") | {"summary": summary, "recommendation": None}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / (Path(name).name or "upload.png")
            source.write_bytes(data)
            job = {"input": source.name}
            for key in ("convert", "media", "sheet", "edits"):
                if settings.get(key):
                    job[key] = settings[key]
            _, images, durations, _, summary = jobs.convert(job, folder)
        if settings.get("palette") == "project" and len(self.project.palette) >= 2:      # the picture takes this project's colours
            import numpy as np

            from . import edits, stills
            colours = np.array([edits.rgb(c) for c in self.project.palette], dtype=np.int64)
            images = [image.copy() for image in images]
            for image in images:
                stills.apply_palette(image, colours, 1)
            summary = dict(summary, colors=len(stills.histogram([(image, 1) for image in images])[0]))
        op = {"op": "place", "name": Path(name).stem[:40] or "Imported", "frames": [base64.b64encode(png_bytes(i)).decode() for i in images],
              "frame": 0 if how != "frames" else len(self.project.frames), "fit": how == "project"}
        if len(images) > 1 or how == "frames":
            op["durations"] = durations
        with self.lock:
            if how == "project":
                before = self.project.snapshot()
                self.put(Project.from_frames(images, durations, op["name"]))
                self.undo.append((before, f"opened {name}", "you"))
                self.redo.clear()
                result = self._changed("you", f"opened {name} as {summary['width']} by {summary['height']} cells")
            else:
                result = self.change(op, "you")
        return result | {"summary": {k: summary[k] for k in ("width", "height", "frames", "colors") if k in summary},
                         "recommendation": summary.get("recommendation")}

    def export(self, fmt, scale, frame):
        with self.lock:
            images, durations = self.project.animation()
        if fmt == "project":
            return self.path.read_bytes(), self.path.name, "application/json"
        stem = self.path.stem
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / f"out.{fmt}"
            jobs.write([images[frame]] if fmt == "png" else images, durations, target, fmt, scale)
            kind = {"png": "image/png", "gif": "image/gif", "mp4": "video/mp4", "webm": "video/webm"}[fmt]
            return target.read_bytes(), f"{stem}.{fmt}", kind


def handler_for(studio):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def send(self, status, body, kind="application/json", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/state":
                    return self.send(200, studio.state())
                if url.path == "/api/events":
                    return self.events()
                if url.path == "/api/wait":
                    studio.heard = time.time()
                    studio.tick()
                    messages = []
                    try:
                        messages.append(studio.inbox.get(timeout=min(30, float(query.get("slice", 25)))))
                        while True:
                            messages.append(studio.inbox.get_nowait())
                    except queue.Empty:
                        pass
                    studio.heard = time.time() if not messages else 0
                    studio.tick()
                    return self.send(200, {"messages": messages, "project": studio.path.name})
                if url.path == "/api/look":
                    with studio.lock:
                        view = dict(studio.view)
                        frame = int(query.get("frame", view.get("frame", 0)))
                        frame = max(0, min(len(studio.project.frames) - 1, frame))
                        layer = int(query["layer"]) if "layer" in query else None
                        image = studio.project.composite(frame, layer)
                        summary = studio.project.summary()
                    if query.get("as") == "json":
                        return self.send(200, {"project": str(studio.path), "viewing": view, **summary})
                    return self.send(200, png_bytes(frames.enlarge(image, max(1, min(32, int(query.get("scale", 8)))))), "image/png")
                if url.path == "/api/palettes":
                    from .palettes import PALETTES
                    return self.send(200, PALETTES)
                if url.path == "/api/check":
                    from . import check
                    with studio.lock:
                        images, _ = studio.project.animation()
                        palette = studio.project.palette if query.get("palette") == "1" else None
                    marks = check.inspect(images, palette, max(1, min(12, int(query.get("fragment", 3)))))
                    found = check.report(marks)
                    # the marked cells themselves, per kind and frame, for the page to draw (capped)
                    cells, budget = {}, 40000
                    for kind in check.KINDS:
                        cells[kind] = []
                        for frame in marks[kind]:
                            ys, xs = frame.nonzero()
                            take = min(len(xs), max(0, budget))
                            budget -= take
                            cells[kind].append([[int(x), int(y)] for x, y in zip(xs[:take], ys[:take])])
                    return self.send(200, {**found, "cells": cells})
                if url.path == "/api/export":
                    fmt = query.get("format", "png")
                    if fmt not in ("png", "gif", "mp4", "webm", "project"):
                        raise ValueError("Export as png, gif, mp4, webm or project")
                    data, name, kind = studio.export(fmt, max(1, min(64, int(query.get("scale", 1)))), max(0, min(len(studio.project.frames) - 1, int(query.get("frame", 0)))))
                    return self.send(200, data, kind, {"Content-Disposition": f'attachment; filename="{name}"'})
                name = "index.html" if url.path == "/" else url.path.lstrip("/")
                file = (APP / name).resolve()
                if APP.resolve() in file.parents and file.is_file():
                    return self.send(200, file.read_bytes(), TYPES.get(file.suffix, "application/octet-stream"))
                self.send(404, {"error": "Not found"})
            except (ValueError, TypeError, KeyError, RuntimeError) as error:
                self.send(400, {"error": str(error)})

        def do_POST(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                raw = self.body()
                data = {} if url.path == "/api/import" else json.loads(raw or b"{}")
                sent = query.get("run") or data.get("run")
                if sent and sent != studio.run:
                    # a page that was open before this studio started shows what an earlier one held, and that may be another project
                    return self.send(409, {"error": "The studio was started again since this page was opened. The page looks again.", "again": True})
                if not sent and self.headers.get("Origin"):
                    # a browser names where it comes from; a page of ours also names its run. One that does not was loaded
                    # from an older studio, or is no page of ours at all
                    return self.send(409, {"error": "This page is older than the studio. Reload it.", "again": True})
                if url.path == "/api/import":
                    settings = json.loads(query.get("settings", "{}"))
                    return self.send(200, studio.bring_in(raw, query.get("name", "upload.png"), settings, query.get("how", "layer")))
                by = "agent" if data.get("by") == "agent" else "you"
                if url.path == "/api/op":
                    return self.send(200, studio.change(data["op"], by, data.get("layers"), data.get("frames")))
                if url.path == "/api/loop":
                    return self.send(200, studio.make_loop(data["loop"], data.get("frame", 0), by, data.get("frames")))
                if url.path == "/api/preview":
                    return self.send(200, studio.preview(data["op"], data.get("layers"), data.get("frames")))
                if url.path == "/api/undo":
                    return self.send(200, studio.step(True, by))
                if url.path == "/api/redo":
                    return self.send(200, studio.step(False, by))
                if url.path == "/api/view":
                    studio.view = studio.looking(data)
                    return self.send(200, {})
                if url.path == "/api/chat":
                    text = str(data.get("text", "")).strip()
                    if not text:
                        raise ValueError("Write something first")
                    context = studio.looking(data.get("context") or {})
                    if context:                                       # a page tells all of what it has open with every message
                        studio.view = context
                    studio.say("you", text, context=context)
                    if studio.agent:
                        studio.inbox.put({"text": text, "context": context})
                    else:
                        studio.say("studio", "No agent is connected. Start the studio with `pixel-cat studio --agent` from an agent's session, "
                                             "and it can answer here and draw with you.")
                    return self.send(200, {})
                if url.path == "/api/say":
                    return self.send(200, studio.say("agent", data.get("text", "")))
                self.send(404, {"error": "Not found"})
            except (ValueError, TypeError, KeyError, RuntimeError, json.JSONDecodeError) as error:
                self.send(400, {"error": str(error)})

        def events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            mine = queue.Queue()
            studio.listeners.append(mine)
            try:
                # first where the project stands: what changed before this listener was there, it has not heard
                self.wfile.write(f": connected\n\ndata: {json.dumps({'type': 'hello', 'run': studio.run, 'rev': studio.rev})}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        event = mine.get(timeout=10)
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    except queue.Empty:
                        studio.tick()
                        self.wfile.write(b": still here\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                studio.listeners.remove(mine)

    return Handler


def serve(path, port=4178, agent=False, size=(64, 64)):
    """Start the studio and return (server, studio). The caller runs server.serve_forever()."""
    path = Path(path)
    if path.suffix.lower() != SUFFIX:
        raise ValueError(f"A project file ends in {SUFFIX}")
    studio = Studio(path, agent, size)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(studio))
    server.daemon_threads = True
    return server, studio


def running():
    """The address of the studio that was started last, from its note on disk."""
    try:
        return json.loads(STATE.read_text())["url"]
    except (OSError, ValueError, KeyError):
        raise RuntimeError("No studio is running. Start one with `pixel-cat studio project.pixelcat --agent`.") from None


def call(path, payload=None, url=None, timeout=60, raw=False):
    """Talk to the running studio from the command line."""
    request = urllib.request.Request((url or running()) + path, method="POST" if payload is not None else "GET",
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        try:
            raise ValueError(json.loads(error.read())["error"]) from None
        except (json.JSONDecodeError, KeyError):
            raise RuntimeError(f"The studio answered {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("The studio does not answer. Is `pixel-cat studio` still running?") from None
    return data if raw else json.loads(data)


def describe(message, project):
    """One chat message as the line an agent reads."""
    context = message.get("context") or {}
    parts = [project]
    if "frame" in context:
        parts.append(f"frame {context['frame']}")
    if "layer" in context:
        parts.append(f"layer {context['layer']}" + (f" \"{context['layerName']}\"" if context.get("layerName") else ""))
    if context.get("selection"):
        x, y, w, h = context["selection"]
        parts.append(f"selection x {x} y {y}, {w} by {h}")
    if context.get("color"):
        parts.append(f"colour {context['color']}")
    return f"YOU [{', '.join(parts)}]: {message['text']}"
