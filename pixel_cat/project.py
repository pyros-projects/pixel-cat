"""A project: pixel art with layers and frames, saved as one .pixelcat file.

    {"format": "pixel-cat project", "version": 2, "width": 64, "height": 64,
     "palette": ["#101018", ...],
     "frames": [{"duration": 120}, ...],
     "pictures": ["<PNG, base64>", ...],
     "layers": [{"id": "L1", "name": "Colour", "visible": true, "locked": false, "opacity": 1,
                 "cels": [0, 0, null, 1, ...]}]}

Layers are listed from the bottom up. Every layer holds one cel per frame; an empty cel is null, a filled one
is the number of its picture in "pictures". A picture is a PNG of the project's size, so any tool that reads
JSON and PNG can read a project. A drawing that is held over several frames, or shows on several layers, is
stored once. Files of version 1 held the PNG in the cel itself; they still open. A layer can carry a "joint":
then its cels are the drawing at rest, and its keys turn and move it (see joints.py).

Everything that changes a project goes through `apply`, one small JSON operation at a time. The studio's
browser, an agent and a script all speak the same operations.
"""

import base64
import copy
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import edits, joints, stills
from .options import integer, keys

FORMAT = "pixel-cat project"
SUFFIX = ".pixelcat"
LIMITS = {"side": 2048, "frames": 256, "layers": 32}


def png_bytes(image, small=True):
    """A picture as a PNG. `small` packs it as tightly as it goes, for files; without it packing is several
    times faster and a little larger, for pictures that are only passed on."""
    out = io.BytesIO()
    Image.fromarray(image).save(out, format="PNG", optimize=small)
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
        self._posed = {}                 # jointed cels as they appear, kept until their drawing or pose changes
        self._pngs = {}                  # cels as PNG text, kept while the cel is in use: a cel never changes in place

    # ------------------------------------------------------------ building

    def _new_id(self):
        taken = {layer.get("id") for layer in getattr(self, "layers", [])}
        return next(f"L{n}" for n in range(1, len(taken) + 2) if f"L{n}" not in taken)

    def _layer(self, name):
        return {"id": self._new_id(), "name": name, "visible": True, "locked": False, "opacity": 1.0, "cels": [None] * len(self.frames)}

    def _order(self, order):
        """Rebuild the frames from a list of frame numbers; None stands for a new, empty frame. A number may
        come twice: then the frame is copied, with its cels and keys."""
        self.frames = [{"duration": 120} if f is None else dict(self.frames[f]) for f in order]
        for layer in self.layers:
            layer["cels"] = [None if f is None else layer["cels"][f] for f in order]
            if layer.get("joint"):
                held = layer["joint"]["keys"]
                layer["joint"]["keys"] = [dict(held[f]) if f is not None and held[f] else None for f in order]

    def _pin(self):
        """Write the motion of joints that loop or sway out as a key on every frame. Such a motion depends on
        the number of frames; written out, it plays the same when the timeline grows. Returns how many layers
        were changed."""
        count, memo, written = len(self.frames), {}, {}
        for index, layer in enumerate(self.layers):
            joint = layer.get("joint")
            if not joint or not (joint.get("sway") or (joint.get("wrap", True) and (any(joint["keys"]) or joint.get("drag")))):
                continue
            parent = joints.parent_of(self.layers, index)
            carried = parent is not None and index not in joints.chain(self.layers, parent)
            written[index] = []
            for frame in range(count):                                # its own turn: the whole turn less what the parent brings
                turn = joints.world(self.layers, index, frame, count, memo)[1] - (joints.world(self.layers, parent, frame, count, memo)[1] if carried else 0.0)
                _, x, y = joints.local(joint, frame, count)
                written[index].append(joints.key({"angle": turn, "x": x, "y": y, "ease": "linear"}))
        for index, held in written.items():
            self.layers[index]["joint"].update(keys=held, wrap=False, sway=None, drag=None)
        return len(written)

    @staticmethod
    def _tracks(layer):
        """The lists of a layer that hold one entry per frame."""
        return [layer["cels"]] + ([layer["joint"]["keys"]] if layer.get("joint") else [])

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
        other.layers = [dict(layer, cels=list(layer["cels"]), **({"joint": copy.deepcopy(layer["joint"])} if layer.get("joint") else {}))
                        for layer in self.layers]
        other.palette = list(self.palette)
        other.locked = self.locked
        other._posed = self._posed
        other._pngs = self._pngs
        return other

    # ------------------------------------------------------------ files

    def to_json(self):
        """The project as it is saved. Every picture is written once, however many cels show it."""
        pictures, number, placed, layers = [], {}, {}, []
        for layer in self.layers:
            cels = []
            for cel in layer["cels"]:
                if cel is not None and id(cel) not in placed:
                    kept = self._pngs.get(id(cel))
                    if kept is None or kept[0] is not cel:
                        kept = self._pngs[id(cel)] = (cel, base64.b64encode(png_bytes(cel)).decode())
                    if kept[1] not in number:                         # two cels that look the same share a picture too
                        number[kept[1]] = len(pictures)
                        pictures.append(kept[1])
                    placed[id(cel)] = number[kept[1]]
                cels.append(None if cel is None else placed[id(cel)])
            layers.append(dict(layer, cels=cels))
        for gone in set(self._pngs) - set(placed):                    # what is no longer shown is not kept
            del self._pngs[gone]
        return {"format": FORMAT, "version": 2, "width": self.width, "height": self.height, "palette": self.palette,
                "locked": self.locked, "frames": self.frames, "pictures": pictures, "layers": layers}

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
        pictures, opened = data.get("pictures", []), {}

        def picture(entry):
            """A cel of the file as an array: the number of a picture, or the PNG itself as version 1 wrote it.
            Each picture is opened once, and the cels that show it share it."""
            if entry is None:
                return None
            if isinstance(entry, int) and not isinstance(entry, bool) and isinstance(pictures, list) and 0 <= entry < len(pictures):
                entry = pictures[entry]
            if not isinstance(entry, str):
                raise ValueError("A cel is the number of one of the project's pictures, or null")
            if entry not in opened:
                opened[entry] = image = from_png(base64.b64decode(entry), (project.width, project.height))
                project._pngs[id(image)] = (image, entry)             # as it stands in the file, so saving need not pack it again
            return opened[entry]

        for layer in data["layers"]:
            if len(layer["cels"]) != len(project.frames):
                raise ValueError("Every layer needs one cel per frame")
            cels = [picture(c) for c in layer["cels"]]
            taken = {known["id"] for known in project.layers}
            name = layer.get("id") if isinstance(layer.get("id"), str) and layer.get("id") not in taken else project._new_id()
            project.layers.append({"id": name, "name": str(layer.get("name", "Layer")), "visible": bool(layer.get("visible", True)),
                                   "locked": bool(layer.get("locked", False)), "opacity": float(layer.get("opacity", 1)),
                                   "cels": [c if c is not None and c[..., 3].any() else None for c in cels]})
            if layer.get("joint"):
                project.layers[-1]["joint"] = joints.clean(layer["joint"], len(project.frames), project.width, project.height)
        for index, layer in enumerate(project.layers):               # a joint hangs on a jointed layer, and never on itself
            if layer.get("joint") and layer["joint"]["parent"] is not None:
                parent = joints.parent_of(project.layers, index)
                if parent is None or index in [parent] + joints.chain(project.layers, parent):
                    layer["joint"]["parent"] = None
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

    def shown(self, layer, frame):
        """A cel as it appears: a jointed layer is turned and moved by its pose and carried by its parents."""
        cel = self.layers[layer]["cels"][frame]
        if cel is None or not self.layers[layer].get("joint"):
            return cel
        pose = joints.world(self.layers, layer, frame, len(self.frames))[0]
        name = self.layers[layer]["id"]
        mark, kept = tuple(np.round(pose, 6).ravel()), self._posed.get((name, frame))
        if kept is None or kept[0] is not cel or kept[1] != mark:
            before = self._posed.get((name, frame - 1))                # a pose that is held shows one picture
            held = before is not None and before[0] is cel and before[1] == mark
            kept = self._posed[(name, frame)] = (cel, mark, before[2] if held else joints.warp(cel, pose))
        return kept[2]

    def composite(self, frame, only=None):
        """One frame as it looks: the visible layers from the bottom up."""
        out = self.blank().astype(np.float64)
        for index, layer in enumerate(self.layers):
            cel = self.shown(index, frame)
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
                            "cels": [c is not None for c in layer["cels"]],
                            **({"joint": {"pivot": layer["joint"]["pivot"], "parent": joints.parent_of(self.layers, index),
                                          "keys": [f for f, k in enumerate(layer["joint"]["keys"]) if k]}} if layer.get("joint") else {})}
                           for index, layer in enumerate(self.layers)]}

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
            image = from_png(base64.b64decode(op["png"]), (self.width, self.height))
            # A browser canvas holds a partly see-through cell only roughly: the fainter the cell, the coarser
            # its colour comes back. Where such a cell returns with its alpha and with a colour inside that
            # error, nobody drew on it, and it keeps the colour it had.
            alpha = before[..., 3].astype(np.int64)
            slack = np.floor(127.5 / np.maximum(alpha, 1) + 0.5) + 1
            near = (np.abs(image[..., :3].astype(np.int64) - before[..., :3]) <= slack[..., None]).all(axis=2)
            same = (alpha > 0) & (alpha < 255) & (image[..., 3] == alpha) & near
            image[same] = before[same]
            self.put(layer, frame, image)
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
            gone = self.layers.pop(self._at(op, "layer", layers))
            for layer in self.layers:                                 # what hung on it hangs on its parent now
                if layer.get("joint") and layer["joint"]["parent"] == gone["id"]:
                    layer["joint"]["parent"] = gone["joint"]["parent"] if gone.get("joint") else None
            return f"removed layer {gone['name']}"
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
            for layer in self.layers[upper - 1:upper + 1]:
                if layer.get("joint"):
                    raise ValueError(f"Layer {layer['name']} has a joint. Bake it first, then merge.")
            keep = self.snapshot()
            for layer in keep.layers:
                layer["visible"] = False
            keep.layers[upper]["visible"] = keep.layers[upper - 1]["visible"] = True
            for frame in range(frames):
                self.put(upper - 1, frame, keep.composite(frame))
            self.layers.pop(upper)
            return "merged a layer into the one below"
        if kind == "add-frame":
            # one empty frame, or copies: of one frame (`count` times) or of several frames in a row
            keys(op, {"op", "at", "copy", "count"}, "add-frame")
            at = integer(op.get("at", frames), 0, frames, "at")
            source = op.get("copy")
            if isinstance(source, list):
                copies = [integer(f, 0, frames - 1, "copy") for f in source]
            elif source is not None:
                copies = [integer(source, 0, frames - 1, "copy")] * integer(op.get("count", 1), 1, LIMITS["frames"], "count")
            else:
                copies = [None] * integer(op.get("count", 1), 1, LIMITS["frames"], "count")
            if frames + len(copies) > LIMITS["frames"]:
                raise ValueError("A project holds up to 256 frames")
            self._order(list(range(at)) + copies + list(range(at, frames)))
            return f"added frame {at}" if len(copies) == 1 else f"added {len(copies)} frames at {at}"
        if kind == "remove-frame":
            keys(op, {"op", "frame", "frames"}, "remove-frame")
            gone = set(edits.targets(op, frames)) if "frames" in op else {self._at(op, "frame", frames)}
            if len(gone) >= frames:
                raise ValueError("A project keeps at least one frame")
            self._order([f for f in range(frames) if f not in gone])
            return f"removed frame {min(gone)}" if len(gone) == 1 else f"removed {len(gone)} frames"
        if kind == "move-frame":
            # one frame, or several: they are taken out and put back in a row, starting at `to`
            keys(op, {"op", "frame", "frames", "to"}, "move-frame")
            block = [integer(f, 0, frames - 1, "frame") for f in op["frames"]] if "frames" in op else [self._at(op, "frame", frames)]
            rest = [f for f in range(frames) if f not in set(block)]
            to = integer(op.get("to", 0), 0, len(rest), "to")
            self._order(rest[:to] + block + rest[to:])
            return f"moved frame {block[0]} to {to}" if len(block) == 1 else f"moved {len(block)} frames to {to}"
        if kind == "set-frame":
            targets = edits.targets(op, frames) if "frames" in op else range(frames) if op.get("frame") == "all" else [self._at(op, "frame", frames)]
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
            keys(op, {"op", "layer", "from", "frames", "rect", "points", "cells", "outside"}, "hold")
            layer = self._at(op, "layer", layers)
            if self.layers[layer]["locked"]:
                raise ValueError(f"Layer {layer} is locked")
            source = self.cel(layer, integer(op.get("from", 0), 0, frames - 1, "from"))
            mask = edits.area(op, self.width, self.height)
            if op.get("outside"):                                     # only the area keeps moving
                mask = ~mask
            changed = 0
            for frame in edits.targets(op, frames):
                image = self.cel(layer, frame).copy()
                changed += int((image[mask] != source[mask]).any(axis=1).sum())
                image[mask] = source[mask]
                self.put(layer, frame, image)
            return f"held {int(mask.sum())} cells of frame {op.get('from', 0)} still, {changed} cells changed"
        if kind == "joint":
            # a layer turns around a pivot and can hang on another jointed layer
            keys(op, {"op", "layer", "frame", "pivot", "parent", "wrap", "drag", "sway", "cover", "spread", "remove"}, "joint")
            index = self._at(op, "layer", layers)
            layer = self.layers[index]
            if op.get("remove"):
                if not layer.get("joint"):
                    raise ValueError(f"Layer {layer['name']} has no joint")
                gone = layer.pop("joint")
                for other in self.layers:
                    if other.get("joint") and other["joint"]["parent"] == layer["id"]:
                        other["joint"]["parent"] = gone["parent"]
                return f"removed the joint of layer {layer['name']}; its drawing stays as it is at rest"
            new = not layer.get("joint")
            if new:
                if "pivot" not in op:
                    raise ValueError("A new joint needs a pivot, the point the layer turns around: [x, y] in cells")
                layer["joint"] = {"pivot": [0.0, 0.0], "parent": None, "wrap": True, "drag": None, "sway": None, "keys": [None] * frames}
            joint = layer["joint"]
            try:
                if "pivot" in op:
                    joint["pivot"] = joints.point(op["pivot"], self.width, self.height)
                if "parent" in op and op["parent"] is not None:
                    parent = integer(op["parent"], 0, layers - 1, "parent")
                    if not self.layers[parent].get("joint"):
                        raise ValueError(f"Layer {self.layers[parent]['name']} has no joint to hang on. Give it one first.")
                    if index in [parent] + joints.chain(self.layers, parent):
                        raise ValueError(f"Layer {self.layers[parent]['name']} already hangs on layer {layer['name']}")
                    joint["parent"] = self.layers[parent]["id"]
                elif "parent" in op:
                    joint["parent"] = None
                if "wrap" in op:
                    joint["wrap"] = bool(op["wrap"])
                if "drag" in op:
                    joint["drag"] = joints.drag(op["drag"])
                if "sway" in op:
                    joint["sway"] = joints.sway(op["sway"])
                cover = op.get("cover")
                if cover is not None:
                    keys(cover, {"radius", "color"}, "cover")
                    radius, paint = integer(cover.get("radius", 4), 1, 24, "radius"), colour(cover.get("color", "#000000"))
                    if layers >= LIMITS["layers"]:
                        raise ValueError("A project holds up to 32 layers")
            except ValueError:
                if new:
                    layer.pop("joint")
                raise
            if op.get("spread"):                                      # one drawing for every frame that has none
                source = layer["cels"][self._at(op, "frame", frames)]
                if source is not None:
                    layer["cels"] = [source if c is None else c for c in layer["cels"]]
            if cover is not None:
                # a disc at the pivot on a layer of its own, below the part and hanging on it: it hides the gap
                # that opens at the joint when the part turns
                ys, xs = np.mgrid[0:self.height, 0:self.width]
                disc = (xs + 0.5 - joint["pivot"][0]) ** 2 + (ys + 0.5 - joint["pivot"][1]) ** 2 <= radius ** 2
                patch = self.blank()
                patch[disc] = paint
                under = self._layer(f"{layer['name']} joint"[:60])
                under["cels"] = [self.snap(patch) if disc.any() else None] * frames
                under["joint"] = {"pivot": list(joint["pivot"]), "parent": layer["id"], "wrap": True, "drag": None, "sway": None, "keys": [None] * frames}
                self.layers.insert(index, under)
                return f"covered the joint of layer {layer['name']} with a disc on a new layer below it"
            on = joints.parent_of(self.layers, index)
            return (f"gave layer {layer['name']} a joint" if new else f"changed the joint of layer {layer['name']}") + \
                (f", hanging on {self.layers[on]['name']}" if on is not None else "")
        if kind == "part":
            # a piece of a layer becomes a part of its own: its drawn cells move to a new layer above, on every
            # frame, and that layer gets a joint
            keys(op, {"op", "layer", "rect", "points", "cells", "name", "pivot"}, "part")
            index = self._at(op, "layer", layers)
            source = self.layers[index]
            if source["locked"]:
                raise ValueError(f"Layer {index} is locked")
            if not any(name in op for name in ("rect", "points", "cells")):
                raise ValueError("part needs an area: rect, points or cells")
            if layers >= LIMITS["layers"]:
                raise ValueError("A project holds up to 32 layers")
            mask = edits.area(op, self.width, self.height)
            split, reach = {}, np.zeros_like(mask)                    # a drawing held over several frames is split once
            for cel in source["cels"]:
                if cel is None or id(cel) in split:
                    continue
                taken = mask & (cel[..., 3] > 0)
                piece, rest = self.blank(), cel.copy()
                piece[taken], rest[taken] = cel[taken], 0
                split[id(cel)] = (rest if rest[..., 3].any() else None, piece if taken.any() else None)
                reach |= taken
            if not reach.any():
                raise ValueError(f"There is nothing drawn in that area on layer {source['name']}")
            ys, xs = np.nonzero(reach)
            middle = [(int(xs.min()) + int(xs.max()) + 1) / 2, (int(ys.min()) + int(ys.max()) + 1) / 2]
            pivot = joints.point(op["pivot"], self.width, self.height) if "pivot" in op else middle
            new = dict(self._layer(str(op.get("name") or f"{source['name']} part")[:60]), visible=source["visible"], opacity=source["opacity"])
            new["cels"] = [None if cel is None else split[id(cel)][1] for cel in source["cels"]]   # shown and faded like its layer: nothing looks different
            source["cels"] = [None if cel is None else split[id(cel)][0] for cel in source["cels"]]
            # a piece of a jointed layer hangs on it, so it still goes where the layer goes
            new["joint"] = {"pivot": pivot, "parent": source["id"] if source.get("joint") else None, "wrap": True, "drag": None,
                            "sway": None, "keys": [None] * frames}
            self.layers.insert(index + 1, new)
            return (f"made a part of {int(reach.sum())} cells of layer {source['name']}: they are on the new layer {new['name']} above it, "
                    f"which turns around {pivot[0]:g}, {pivot[1]:g}")
        if kind == "pose":
            # a key on one frame: how far the jointed layer is turned and moved there
            keys(op, {"op", "layer", "frame", "angle", "x", "y", "ease", "remove"}, "pose")
            layer, frame = self.layers[self._at(op, "layer", layers)], self._at(op, "frame", frames)
            joint = layer.get("joint")
            if not joint:
                raise ValueError(f'Layer {layer["name"]} has no joint. Give it one first: {{"op": "joint", "layer": ..., "pivot": [x, y]}}')
            if op.get("remove"):
                if joint["keys"][frame] is None:
                    raise ValueError(f"Frame {frame} has no key on layer {layer['name']}")
                joint["keys"][frame] = None
                return f"removed the key of layer {layer['name']} on frame {frame}"
            angle, x, y = joints.local(joint, frame, frames)          # what the frame shows now is where the key starts
            held = dict(joint["keys"][frame] or {"angle": angle, "x": x, "y": y, "ease": "smooth"})
            held.update({name: op[name] for name in ("angle", "x", "y", "ease") if name in op})
            joint["keys"][frame] = held = joints.key(held)
            return f"posed layer {layer['name']} on frame {frame}: turned {held['angle']:g} degrees, moved {held['x']:g}, {held['y']:g}"
        if kind == "bake":
            # the motion becomes plain frames, and the joints go
            keys(op, {"op", "layer"}, "bake")
            if "layer" in op:
                index = self._at(op, "layer", layers)
                if not self.layers[index].get("joint"):
                    raise ValueError(f"Layer {self.layers[index]['name']} has no joint")
                chosen = [index] + joints.hanging_on(self.layers, index)
            else:
                chosen = [i for i, layer in enumerate(self.layers) if layer.get("joint")]
                if not chosen:
                    raise ValueError("No layer has a joint")
            pictures = {i: [self.shown(i, f) for f in range(frames)] for i in chosen}
            for i, cels in pictures.items():
                self.layers[i].pop("joint")
                self.layers[i]["cels"] = [None if c is None or not c[..., 3].any() else self.snap(c) for c in cels]
            return f"baked the motion of {len(chosen)} layer{'s' if len(chosen) != 1 else ''} into {'their' if len(chosen) != 1 else 'its'} frames"
        if kind == "append":
            # another project after the last frame: its layers go onto ours of the same name, or become new ones
            keys(op, {"op", "project"}, "append")
            other = Project.from_json(op["project"])
            if any(layer.get("joint") for layer in other.layers):
                other.apply({"op": "bake"})                           # its motion comes as plain frames
            if frames + len(other.frames) > LIMITS["frames"]:
                raise ValueError("A project holds up to 256 frames")
            pinned = self._pin()                                      # what is here plays on as it did
            self._order(list(range(frames)) + [None] * len(other.frames))
            for at, frame in enumerate(other.frames):
                self.frames[frames + at]["duration"] = frame["duration"]
            taken, added = set(), 0
            alike = lambda mine, theirs: all(mine[name] == theirs[name] for name in ("name", "visible", "locked", "opacity"))   # noqa: E731
            for layer in other.layers:                                # onto a layer that shows the same way, or onto a new one
                home = next((i for i, mine in enumerate(self.layers) if alike(mine, layer) and not mine.get("joint") and i not in taken), None)
                if home is None:
                    if len(self.layers) >= LIMITS["layers"]:
                        raise ValueError("A project holds up to 32 layers")
                    self.layers.append(dict(self._layer(layer["name"]), visible=layer["visible"], locked=layer["locked"], opacity=layer["opacity"]))
                    home, added = len(self.layers) - 1, added + 1
                taken.add(home)
                for at, cel in enumerate(layer["cels"]):
                    if cel is not None:
                        canvas = self.blank()
                        h, w = min(self.height, other.height), min(self.width, other.width)
                        canvas[:h, :w] = cel[:h, :w]
                        self.layers[home]["cels"][frames + at] = self.snap(canvas) if canvas[..., 3].any() else None
            note = f"appended {len(other.frames)} frame{'s' if len(other.frames) != 1 else ''} from a project with {len(other.layers)} layer{'s' if len(other.layers) != 1 else ''}"
            if added:
                note += f", {added} of them new here"
            if pinned:
                note += f"; the motion of {pinned} part{'s' if pinned != 1 else ''} here is now a key on every frame, so {'they play' if pinned != 1 else 'it plays'} as before"
            if (other.width, other.height) != (self.width, self.height):
                note += f"; it is {other.width} by {other.height}, so its art sits at the top left"
            return note
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
                    for track in self._tracks(layer):
                        track.append(None)
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
                if layer.get("joint"):
                    layer["joint"]["pivot"] = [layer["joint"]["pivot"][0] + dx, layer["joint"]["pivot"][1] + dy]
            self.width, self.height = width, height
            return f"canvas is now {width} by {height}"
        raise ValueError(f"Unknown operation: {kind}")


OPERATIONS = ["draw", "clear", "set-cel", "add-layer", "remove-layer", "move-layer", "set-layer", "merge-down", "add-frame",
              "remove-frame", "move-frame", "set-frame", "palette", "palette-from-art", "edits", "hold", "joint", "part", "pose", "bake",
              "append", "place", "resize"]
