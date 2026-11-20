"""A project: pixel art with layers and frames, saved as one .pixelcat file.

    {"format": "pixel-cat project", "version": 1, "width": 64, "height": 64,
     "palette": ["#101018", ...],
     "frames": [{"duration": 120}, ...],
     "layers": [{"name": "Colour", "visible": true, "locked": false, "opacity": 1,
                 "cels": ["<PNG, base64>", null, ...]}]}

Layers are listed from the bottom up. Every layer holds one cel per frame; an empty cel is null. A cel is a
PNG of the project's size, so any tool that reads JSON and PNG can read a project.

Everything that changes a project goes through `apply`, one small JSON operation at a time. The studio's
browser, an agent and a script all speak the same operations.
"""

import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import edits, stills
from .options import integer, keys

FORMAT = "pixel-cat project"
SUFFIX = ".pixelcat"
LIMITS = {"side": 1024, "frames": 256, "layers": 32}


def png_bytes(image):
    out = io.BytesIO()
    Image.fromarray(image).save(out, format="PNG", optimize=True)
    return out.getvalue()


def from_png(data, size=None):
    with Image.open(io.BytesIO(data)) as file:
        image = np.array(file.convert("RGBA"), dtype=np.uint8)
    if size and (image.shape[1], image.shape[0]) != size:
        raise ValueError(f"A cel has to be {size[0]} by {size[1]} cells, this one is {image.shape[1]} by {image.shape[0]}")
    return image


def colour(value):
    """'#rrggbb' to [r, g, b, 255]; None or 'none' erases."""
    if value in (None, "none", "transparent"):
        return [0, 0, 0, 0]
    return edits.rgb(value) + [255]


class Project:
    def __init__(self, width, height):
        integer(width, 1, LIMITS["side"], "width")
        integer(height, 1, LIMITS["side"], "height")
        self.width, self.height = width, height
        self.frames = [{"duration": 120}]
        self.layers = [self._layer("Layer 1")]
        self.palette = []
        self.locked = False              # a locked palette: whatever is drawn takes the nearest palette colour

    # ------------------------------------------------------------ building

    def _layer(self, name):
        return {"name": name, "visible": True, "locked": False, "opacity": 1.0, "cels": [None] * len(self.frames)}

    def blank(self):
        return np.zeros((self.height, self.width, 4), dtype=np.uint8)

    def cel(self, layer, frame):
        """The cel as an array; an empty cel comes back as a blank one. Never change it in place."""
        found = self.layers[layer]["cels"][frame]
        return self.blank() if found is None else found

    def put(self, layer, frame, image):
        self.layers[layer]["cels"][frame] = self.snap(image) if image[..., 3].any() else None

    def snap(self, image):
        """With a locked palette, every visible cell moves to its nearest palette colour."""
        if not (self.locked and self.palette):
            return image
        image = image.copy()
        stills.apply_palette(image, np.array([edits.rgb(c) for c in self.palette], dtype=np.int64), 1)
        return image

    @classmethod
    def from_frames(cls, images, durations, name="Layer 1"):
        """A project with one layer from converted frames."""
        project = cls(images[0].shape[1], images[0].shape[0])
        project.frames = [{"duration": int(d)} for d in durations]
        project.layers = [project._layer(name)]
        for index, image in enumerate(images):
            project.put(0, index, np.ascontiguousarray(image))
        found, _, _ = stills.histogram([(image, 1) for image in images])
        if len(found) <= 64:
            project.palette = ["#%02x%02x%02x" % tuple(c) for c in found]
        return project

    def snapshot(self):
        """A copy that shares the cels. Cels are never changed in place, so this is cheap and safe."""
        other = object.__new__(Project)
        other.width, other.height = self.width, self.height
        other.frames = [dict(f) for f in self.frames]
        other.layers = [dict(layer, cels=list(layer["cels"])) for layer in self.layers]
        other.palette = list(self.palette)
        other.locked = self.locked
        return other

    # ------------------------------------------------------------ files

    def to_json(self):
        return {"format": FORMAT, "version": 1, "width": self.width, "height": self.height, "palette": self.palette,
                "locked": self.locked, "frames": self.frames,
                "layers": [dict(layer, cels=[None if c is None else base64.b64encode(png_bytes(c)).decode() for c in layer["cels"]])
                           for layer in self.layers]}

    @classmethod
    def from_json(cls, data):
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("This is not a pixel-cat project")
        project = cls(data["width"], data["height"])
        project.frames = [{"duration": integer(f.get("duration", 120), 10, 60000, "duration")} for f in data["frames"]]
        if not 1 <= len(project.frames) <= LIMITS["frames"] or not 1 <= len(data["layers"]) <= LIMITS["layers"]:
            raise ValueError("A project has 1 to 256 frames and 1 to 32 layers")
        project.palette = [c for c in data.get("palette", []) if edits.rgb(c)]
        project.locked = bool(data.get("locked", False))
        project.layers = []
        for layer in data["layers"]:
            if len(layer["cels"]) != len(project.frames):
                raise ValueError("Every layer needs one cel per frame")
            cels = [None if c is None else from_png(base64.b64decode(c), (project.width, project.height)) for c in layer["cels"]]
            project.layers.append({"name": str(layer.get("name", "Layer")), "visible": bool(layer.get("visible", True)),
                                   "locked": bool(layer.get("locked", False)), "opacity": float(layer.get("opacity", 1)),
                                   "cels": [c if c is not None and c[..., 3].any() else None for c in cels]})
        return project

    @classmethod
    def load(cls, path):
        return cls.from_json(json.loads(Path(path).read_text()))

    def save(self, path):
        path = Path(path)
        temp = path.with_name(path.name + ".part")
        temp.write_text(json.dumps(self.to_json()) + "\n")
        temp.replace(path)

    # ------------------------------------------------------------ looking

    def composite(self, frame, only=None):
        """One frame as it looks: the visible layers from the bottom up."""
        out = self.blank().astype(np.float64)
        for index, layer in enumerate(self.layers):
            cel = layer["cels"][frame]
            if cel is None or not layer["visible"] or (only is not None and index != only):
                continue
            cover = cel[..., 3:4] / 255 * layer["opacity"]
            alpha = cover + out[..., 3:4] / 255 * (1 - cover)
            mixed = cel[..., :3] * cover + out[..., :3] * (out[..., 3:4] / 255) * (1 - cover)
            out[..., :3] = np.divide(mixed, alpha, out=np.zeros_like(mixed), where=alpha > 0)
            out[..., 3:4] = alpha * 255
        return np.rint(out).astype(np.uint8)

    def animation(self):
        return [self.composite(i) for i in range(len(self.frames))], [f["duration"] for f in self.frames]

    def summary(self):
        return {"width": self.width, "height": self.height, "frames": len(self.frames),
                "durations": [f["duration"] for f in self.frames], "palette": len(self.palette), "locked": self.locked,
                "layers": [{"name": layer["name"], "visible": layer["visible"], "locked": layer["locked"],
                            "cels": [c is not None for c in layer["cels"]]} for layer in self.layers]}

    # ------------------------------------------------------------ changing

    def _at(self, op, name, count):
        return integer(op.get(name, 0), 0, count - 1, name)

    def _draw(self, op):
        """Set cells: single points, a line, a rectangle (filled or outlined) or a flood fill. With `frames`
        ("all" or a list) the same is drawn on each of those frames."""
        if "frames" in op:
            for frame in edits.targets(op, len(self.frames)):
                self._draw({k: v for k, v in op.items() if k != "frames"} | {"frame": frame})
            return f"drew on layer {op.get('layer', 0)}, {len(edits.targets(op, len(self.frames)))} frames"
        layer, frame = self._at(op, "layer", len(self.layers)), self._at(op, "frame", len(self.frames))
        if self.layers[layer]["locked"]:
            raise ValueError(f"Layer {layer} is locked")
        image, paint = self.cel(layer, frame).copy(), colour(op.get("color"))
        inside = lambda x, y: 0 <= x < self.width and 0 <= y < self.height   # noqa: E731
        if "points" in op:
            for x, y in op["points"]:
                if inside(x, y):
                    image[y, x] = paint
        if "line" in op:
            x0, y0, x1, y1 = op["line"]
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for i in range(steps + 1):
                x, y = round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps)
                if inside(x, y):
                    image[y, x] = paint
        if "rect" in op:
            x, y, w, h = op["rect"]
            x0, y0, x1, y1 = max(0, x), max(0, y), min(self.width, x + w), min(self.height, y + h)
            if op.get("outline"):
                image[y0:y1, x0:min(x0 + 1, x1)] = image[y0:y1, max(x1 - 1, x0):x1] = paint
                image[y0:min(y0 + 1, y1), x0:x1] = image[max(y1 - 1, y0):y1, x0:x1] = paint
            else:
                image[y0:y1, x0:x1] = paint
        if "fill" in op:
            x, y = op["fill"]
            if inside(x, y):
                same = (image == image[y, x]).all(axis=2)
                reached = np.zeros_like(same)
                reached[y, x] = True
                while True:
                    grown = reached | (edits._neighbours(reached) & same)
                    if (grown == reached).all():
                        break
                    reached = grown
                image[reached] = paint
        self.put(layer, frame, image)
        return f"drew on layer {layer}, frame {frame}"

    def apply(self, op):
        """Carry out one operation and say what it did. Raises ValueError with a plain reason when it cannot."""
        if not isinstance(op, dict) or "op" not in op:
            raise ValueError("An operation is an object with an op")
        kind, layers, frames = op["op"], len(self.layers), len(self.frames)
        if kind == "set-cel":
            layer, frame = self._at(op, "layer", layers), self._at(op, "frame", frames)
            before = self.cel(layer, frame)
            self.put(layer, frame, from_png(base64.b64decode(op["png"]), (self.width, self.height)))
            spread = op.get("spread")
            if not spread:
                return f"drew on layer {layer}, frame {frame}"
            # the cells this stroke changed are repeated on other frames; with `matching`, only where the other
            # frame showed what this frame showed before the stroke, so that moving parts are left alone
            keys(spread, {"frames", "matching"}, "spread")
            after = self.cel(layer, frame)
            changed = (before != after).any(axis=2)
            reached = 0
            for other in edits.targets(spread, frames):
                if other == frame:
                    continue
                image = self.cel(layer, other).copy()
                mask = changed & ((image == before).all(axis=2) if spread.get("matching", True) else True)
                if mask.any():
                    image[mask] = after[mask]
                    self.put(layer, other, image)
                    reached += 1
            return f"drew on layer {layer}, frame {frame} and {reached} more frame{'s' if reached != 1 else ''}"
        if kind == "draw":
            keys(op, {"op", "layer", "frame", "frames", "color", "points", "line", "rect", "outline", "fill"}, "draw")
            return self._draw(op)
        if kind == "clear":
            layer, frame = self._at(op, "layer", layers), self._at(op, "frame", frames)
            self.layers[layer]["cels"][frame] = None
            return f"cleared layer {layer}, frame {frame}"
        if kind == "add-layer":
            if layers >= LIMITS["layers"]:
                raise ValueError("A project holds up to 32 layers")
            at = integer(op.get("at", layers), 0, layers, "at")
            self.layers.insert(at, self._layer(str(op.get("name") or f"Layer {layers + 1}")))
            return f"added layer {at}"
        if kind == "remove-layer":
            if layers == 1:
                raise ValueError("A project keeps at least one layer")
            return f"removed layer {self.layers.pop(self._at(op, 'layer', layers))['name']}"
        if kind == "move-layer":
            layer = self.layers.pop(self._at(op, "layer", layers))
            self.layers.insert(integer(op.get("to", 0), 0, layers - 1, "to"), layer)
            return "moved a layer"
        if kind == "set-layer":
            layer = self.layers[self._at(op, "layer", layers)]
            for name in ("visible", "locked"):
                if name in op:
                    layer[name] = bool(op[name])
            if "name" in op:
                layer["name"] = str(op["name"])[:60] or layer["name"]
            if "opacity" in op:
                layer["opacity"] = min(1.0, max(0.0, float(op["opacity"])))
            return f"changed layer {layer['name']}"
        if kind == "merge-down":
            upper = self._at(op, "layer", layers)
            if upper == 0:
                raise ValueError("The bottom layer has nothing below it")
            keep = self.snapshot()
            for layer in keep.layers:
                layer["visible"] = False
            keep.layers[upper]["visible"] = keep.layers[upper - 1]["visible"] = True
            for frame in range(frames):
                self.put(upper - 1, frame, keep.composite(frame))
            self.layers.pop(upper)
            return "merged a layer into the one below"
        if kind == "add-frame":
            if frames >= LIMITS["frames"]:
                raise ValueError("A project holds up to 256 frames")
            at = integer(op.get("at", frames), 0, frames, "at")
            source = op.get("copy")
            if source is not None:
                integer(source, 0, frames - 1, "copy")
            self.frames.insert(at, {"duration": self.frames[source]["duration"] if source is not None else 120})
            for layer in self.layers:
                layer["cels"].insert(at, layer["cels"][source] if source is not None else None)
            return f"added frame {at}"
        if kind == "remove-frame":
            if frames == 1:
                raise ValueError("A project keeps at least one frame")
            at = self._at(op, "frame", frames)
            self.frames.pop(at)
            for layer in self.layers:
                layer["cels"].pop(at)
            return f"removed frame {at}"
        if kind == "move-frame":
            at, to = self._at(op, "frame", frames), integer(op.get("to", 0), 0, frames - 1, "to")
            self.frames.insert(to, self.frames.pop(at))
            for layer in self.layers:
                layer["cels"].insert(to, layer["cels"].pop(at))
            return f"moved frame {at} to {to}"
        if kind == "set-frame":
            targets = range(frames) if op.get("frame") == "all" else [self._at(op, "frame", frames)]
            for at in targets:
                self.frames[at]["duration"] = integer(op.get("duration", 120), 10, 60000, "duration")
            return "changed the timing"
        if kind == "palette":
            keys(op, {"op", "colors", "name", "lock"}, "palette")
            note = "changed the palette"
            if "name" in op:
                from .palettes import PALETTES

                if op["name"] not in PALETTES:
                    raise ValueError("Palettes: " + ", ".join(PALETTES))
                self.palette = list(PALETTES[op["name"]])
                note = f"took the palette {op['name']} ({len(self.palette)} colours)"
                if self.locked and "lock" not in op:              # a locked project follows its new palette
                    op = {**op, "lock": True}
            if "colors" in op:
                self.palette = ["#%02x%02x%02x" % tuple(edits.rgb(c)) for c in op["colors"]][:256]
            if "lock" in op:
                if op["lock"] and not self.palette:
                    raise ValueError("There is no palette to lock. Add colours or take them from the art first.")
                self.locked = bool(op["lock"])
                if self.locked:                                   # what is there already moves onto the palette too
                    for layer in self.layers:
                        layer["cels"] = [None if c is None else self.snap(c) for c in layer["cels"]]
                if "name" in op:
                    return note + (", and the art took its colours" if self.locked else "")
                return "locked the palette" if self.locked else "unlocked the palette"
            return note
        if kind == "palette-from-art":
            found, counts, _ = stills.histogram([(self.composite(i), 1) for i in range(frames)])
            order = np.argsort(-counts)[:64]
            self.palette = ["#%02x%02x%02x" % tuple(found[i]) for i in sorted(order, key=lambda i: stills.luma(found[i].astype(float)))]
            return f"took {len(self.palette)} colours from the art"
        if kind == "edits":
            # the finishing steps of the converter, on one layer; steps that change the size are not for here
            layer = self._at(op, "layer", layers)
            targets = list(range(frames)) if op.get("frames", "all") == "all" else [integer(f, 0, frames - 1, "frame") for f in op["frames"]]
            steps = op.get("steps", [])
            if any(isinstance(s, dict) and s.get("op") in ("trim", "pad") or (isinstance(s, dict) and s.get("op") == "outline" and s.get("grow", False)) for s in steps):
                raise ValueError("trim, pad and a growing outline change the size; use resize for that")
            if any(isinstance(s, dict) and s.get("op") == "hold" for s in steps):
                raise ValueError("hold is an operation of its own in a project: {\"op\": \"hold\", ...}")
            steps = [dict(s, grow=False) if isinstance(s, dict) and s.get("op") == "outline" else s for s in steps]
            done = edits.apply([self.cel(layer, f).copy() for f in targets], steps)
            for frame, image in zip(targets, done):
                self.put(layer, frame, image)
            return f"applied {len(steps)} step{'s' if len(steps) != 1 else ''} to layer {layer}"
        if kind == "hold":
            # an area of one frame, copied into the others on the same layer: the area stands still
            keys(op, {"op", "layer", "from", "frames", "rect", "points", "cells"}, "hold")
            layer = self._at(op, "layer", layers)
            if self.layers[layer]["locked"]:
                raise ValueError(f"Layer {layer} is locked")
            source = self.cel(layer, integer(op.get("from", 0), 0, frames - 1, "from"))
            mask = edits.area(op, self.width, self.height)
            changed = 0
            for frame in edits.targets(op, frames):
                image = self.cel(layer, frame).copy()
                changed += int((image[mask] != source[mask]).any(axis=1).sum())
                image[mask] = source[mask]
                self.put(layer, frame, image)
            return f"held {int(mask.sum())} cells of frame {op.get('from', 0)} still, {changed} cells changed"
        if kind == "place":
            # converted frames as a new layer, starting at a frame; the project grows by frames as needed
            images = [from_png(base64.b64decode(p)) for p in op["frames"]]
            at = integer(op.get("frame", 0), 0, frames, "frame")
            if not any(c is not None for layer in self.layers for c in layer["cels"]) and op.get("fit", True):
                self.height, self.width = images[0].shape[:2]
            while len(self.frames) < at + len(images):
                if len(self.frames) >= LIMITS["frames"]:
                    raise ValueError("A project holds up to 256 frames")
                self.frames.append({"duration": 120})
                for layer in self.layers:
                    layer["cels"].append(None)
            new = self._layer(str(op.get("name") or "Imported"))
            x, y = int(op.get("x", 0)), int(op.get("y", 0))
            for index, image in enumerate(images):
                canvas = self.blank()
                h, w = image.shape[:2]
                x0, y0, x1, y1 = max(0, x), max(0, y), min(self.width, x + w), min(self.height, y + h)
                if x1 > x0 and y1 > y0:
                    canvas[y0:y1, x0:x1] = image[y0 - y:y1 - y, x0 - x:x1 - x]
                new["cels"][at + index] = self.snap(canvas) if canvas[..., 3].any() else None
                if "durations" in op:
                    self.frames[at + index]["duration"] = integer(op["durations"][index], 10, 60000, "duration")
            if len(self.layers) == 1 and not any(c is not None for c in self.layers[0]["cels"]):
                self.layers = [new]
            else:
                if len(self.layers) >= LIMITS["layers"]:
                    raise ValueError("A project holds up to 32 layers")
                self.layers.append(new)
            return f"placed {len(images)} frame{'s' if len(images) != 1 else ''} as layer {new['name']}"
        if kind == "resize":
            # change the canvas, keeping the art anchored by its top left corner plus an offset
            width, height = integer(op.get("width", self.width), 1, LIMITS["side"], "width"), integer(op.get("height", self.height), 1, LIMITS["side"], "height")
            dx, dy = int(op.get("x", 0)), int(op.get("y", 0))
            for layer in self.layers:
                for frame, cel in enumerate(layer["cels"]):
                    if cel is None:
                        continue
                    canvas = np.zeros((height, width, 4), dtype=np.uint8)
                    x0, y0, x1, y1 = max(0, dx), max(0, dy), min(width, dx + self.width), min(height, dy + self.height)
                    if x1 > x0 and y1 > y0:
                        canvas[y0:y1, x0:x1] = cel[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
                    layer["cels"][frame] = canvas if canvas[..., 3].any() else None
            self.width, self.height = width, height
            return f"canvas is now {width} by {height}"
        raise ValueError(f"Unknown operation: {kind}")


OPERATIONS = ["draw", "clear", "set-cel", "add-layer", "remove-layer", "move-layer", "set-layer", "merge-down", "add-frame",
              "remove-frame", "move-frame", "set-frame", "palette", "palette-from-art", "edits", "hold", "place", "resize"]
