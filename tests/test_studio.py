"""Projects and the studio: one document, changed by small operations, by a person and an agent alike."""

import base64
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from pixel_cat import studio
from pixel_cat.jobs import execute
from pixel_cat.project import Project, png_bytes


class Projects(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.project = Project(16, 12)

    def test_drawing_fills_exactly_the_cells_asked_for(self):
        p = self.project
        p.apply({"op": "draw", "color": "#ff0000", "rect": [2, 2, 4, 3]})
        p.apply({"op": "draw", "color": "#00ff00", "line": [0, 11, 15, 11]})
        p.apply({"op": "draw", "color": "#0000ff", "points": [[15, 0], [99, 99]]})        # a point outside is ignored
        image = p.composite(0)
        self.assertEqual(int((image[..., 0] == 255).sum()), 12)
        self.assertTrue((image[11, :, 1] == 255).all())
        self.assertEqual(tuple(image[0, 15]), (0, 0, 255, 255))
        p.apply({"op": "draw", "color": "#ffffff", "fill": [0, 0]})                        # floods the empty area only
        image = p.composite(0)
        self.assertEqual(tuple(image[5, 10]), (255, 255, 255, 255))
        self.assertEqual(tuple(image[3, 3]), (255, 0, 0, 255))
        p.apply({"op": "draw", "color": None, "rect": [0, 0, 16, 12]})                     # no colour erases
        self.assertFalse(p.composite(0)[..., 3].any())
        self.assertIsNone(p.layers[0]["cels"][0])                                          # an empty cel is stored as none

    def test_layers_stack_hide_lock_and_merge(self):
        p = self.project
        p.apply({"op": "draw", "color": "#ff0000", "rect": [0, 0, 16, 12]})
        p.apply({"op": "add-layer", "name": "Ink"})
        p.apply({"op": "draw", "layer": 1, "color": "#000000", "rect": [4, 4, 2, 2]})
        self.assertEqual(tuple(p.composite(0)[4, 4]), (0, 0, 0, 255))
        p.apply({"op": "set-layer", "layer": 1, "visible": False})
        self.assertEqual(tuple(p.composite(0)[4, 4]), (255, 0, 0, 255))
        p.apply({"op": "set-layer", "layer": 1, "visible": True, "locked": True})
        with self.assertRaisesRegex(ValueError, "locked"):
            p.apply({"op": "draw", "layer": 1, "color": "#ffffff", "points": [[0, 0]]})
        p.apply({"op": "merge-down", "layer": 1})
        self.assertEqual(len(p.layers), 1)
        self.assertEqual(tuple(p.composite(0)[4, 4]), (0, 0, 0, 255))
        with self.assertRaisesRegex(ValueError, "at least one layer"):
            p.apply({"op": "remove-layer", "layer": 0})

    def test_frames_copy_move_and_keep_their_timing(self):
        p = self.project
        p.apply({"op": "draw", "color": "#ff0000", "points": [[1, 1]]})
        p.apply({"op": "set-frame", "frame": 0, "duration": 250})
        p.apply({"op": "add-frame", "copy": 0})
        p.apply({"op": "draw", "frame": 1, "color": "#00ff00", "points": [[2, 2]]})
        p.apply({"op": "add-frame", "at": 0})
        self.assertEqual([f["duration"] for f in p.frames], [120, 250, 250])
        self.assertFalse(p.composite(0)[..., 3].any())
        self.assertEqual(tuple(p.composite(2)[2, 2]), (0, 255, 0, 255))
        self.assertEqual(tuple(p.composite(1)[2, 2]), (0, 0, 0, 0))                        # the copy did not change its source
        p.apply({"op": "move-frame", "frame": 2, "to": 0})
        self.assertEqual(tuple(p.composite(0)[2, 2]), (0, 255, 0, 255))
        p.apply({"op": "remove-frame", "frame": 1})
        self.assertEqual(len(p.frames), 2)
        self.assertEqual(p.summary()["layers"][0]["cels"], [True, True])

    def test_an_area_of_one_frame_can_be_held_in_the_others(self):
        p = self.project
        for frame in range(3):
            if frame:
                p.apply({"op": "add-frame"})
            p.apply({"op": "draw", "frame": frame, "color": ["#ff0000", "#00ff00", "#0000ff"][frame], "rect": [0, 0, 16, 12]})
        note = p.apply({"op": "hold", "from": 1, "rect": [2, 2, 3, 3]})
        self.assertEqual(note, "held 9 cells of frame 1 still, 18 cells changed")
        for frame in range(3):
            image = p.composite(frame)
            self.assertEqual(tuple(image[3, 3]), (0, 255, 0, 255))
            self.assertEqual(tuple(image[8, 8]), [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)][frame])
        p.apply({"op": "hold", "from": 0, "frames": [2], "cells": [[10, 10], [11, 10]]})
        self.assertEqual((tuple(p.composite(2)[10, 10]), tuple(p.composite(1)[10, 10])), ((255, 0, 0, 255), (0, 255, 0, 255)))

    def test_a_locked_palette_takes_every_colour_that_is_drawn(self):
        p = self.project
        p.apply({"op": "draw", "color": "#f0e0d0", "rect": [0, 0, 4, 4]})
        with self.assertRaisesRegex(ValueError, "no palette to lock"):
            p.apply({"op": "palette", "lock": True})
        self.assertEqual(p.apply({"op": "palette", "colors": ["#000000", "#ffffff", "#ff0000"], "lock": True}), "locked the palette")
        self.assertEqual(tuple(p.composite(0)[1, 1]), (255, 255, 255, 255))               # what was there moved onto the palette
        p.apply({"op": "draw", "color": "#e02010", "points": [[8, 8]]})
        self.assertEqual(tuple(p.composite(0)[8, 8]), (255, 0, 0, 255))
        p.save(self.folder / "locked.pixelcat")
        self.assertTrue(Project.load(self.folder / "locked.pixelcat").locked)
        p.apply({"op": "palette", "lock": False})
        p.apply({"op": "draw", "color": "#e02010", "points": [[9, 9]]})
        self.assertEqual(tuple(p.composite(0)[9, 9]), (224, 32, 16, 255))

    def test_one_stroke_can_reach_every_frame_and_spares_what_moves(self):
        p = self.project
        for frame in range(3):
            if frame:
                p.apply({"op": "add-frame"})
            p.apply({"op": "draw", "frame": frame, "color": "#0000ff", "rect": [0, 0, 16, 12]})
        p.apply({"op": "draw", "frame": 2, "color": "#ffff00", "rect": [4, 4, 2, 2]})      # something that is only in frame 2
        stroke = p.cel(0, 0).copy()
        stroke[4, 3:7] = (255, 0, 0, 255)                                                 # a red line across four cells
        note = p.apply({"op": "set-cel", "layer": 0, "frame": 0, "png": base64.b64encode(png_bytes(stroke)).decode(), "spread": {"frames": "all"}})
        self.assertEqual(note, "drew on layer 0, frame 0 and 2 more frames")
        self.assertEqual([tuple(p.composite(f)[4, 3]) for f in range(3)], [(255, 0, 0, 255)] * 3)
        self.assertEqual(tuple(p.composite(2)[4, 4]), (255, 255, 0, 255))                 # the yellow part was not drawn over
        self.assertEqual(tuple(p.composite(1)[4, 4]), (255, 0, 0, 255))
        p.apply({"op": "draw", "frames": "all", "color": "#00ff00", "points": [[15, 11]]})
        self.assertEqual([tuple(p.composite(f)[11, 15]) for f in range(3)], [(0, 255, 0, 255)] * 3)

    def test_a_project_survives_its_file_and_reads_like_any_json(self):
        p = self.project
        p.apply({"op": "draw", "color": "#123456", "rect": [1, 1, 3, 3]})
        p.apply({"op": "add-frame", "copy": 0})
        p.apply({"op": "palette", "colors": ["#123456", "#ffffff"]})
        p.save(self.folder / "a.pixelcat")
        data = json.loads((self.folder / "a.pixelcat").read_text())
        self.assertEqual((data["format"], data["width"], data["height"], len(data["frames"])), ("pixel-cat project", 16, 12, 2))
        with Image.open(__import__("io").BytesIO(base64.b64decode(data["layers"][0]["cels"][0]))) as cel:
            self.assertEqual(cel.size, (16, 12))
        again = Project.load(self.folder / "a.pixelcat")
        self.assertTrue((again.composite(1) == p.composite(1)).all())
        self.assertEqual(again.palette, ["#123456", "#ffffff"])
        (self.folder / "b.pixelcat").write_text("{}")
        with self.assertRaisesRegex(ValueError, "not a pixel-cat project"):
            Project.load(self.folder / "b.pixelcat")

    def test_finishing_steps_and_resize_work_on_a_layer(self):
        p = self.project
        p.apply({"op": "draw", "color": "#ff0000", "rect": [5, 4, 4, 4]})
        p.apply({"op": "edits", "layer": 0, "steps": [{"op": "outline", "color": "#000000"}, {"op": "replace", "from": "#ff0000", "to": "#00ff00"}]})
        image = p.composite(0)
        self.assertEqual((tuple(image[5, 6]), tuple(image[3, 6]), image.shape[:2]), ((0, 255, 0, 255), (0, 0, 0, 255), (12, 16)))
        with self.assertRaisesRegex(ValueError, "change the size"):
            p.apply({"op": "edits", "layer": 0, "steps": [{"op": "trim"}]})
        p.apply({"op": "resize", "width": 20, "height": 20, "x": 2, "y": 4})
        self.assertEqual((p.width, p.height, tuple(p.composite(0)[9, 8])), (20, 20, (0, 255, 0, 255)))

    def test_conversions_read_and_write_projects(self):
        frames = [Image.fromarray(np.full((40, 60, 3), shade, dtype=np.uint8)) for shade in (40, 120, 200)]
        frames[0].save(self.folder / "in.gif", save_all=True, append_images=frames[1:], duration=[80, 90, 100], loop=0)
        result = execute({"input": "in.gif", "convert": {"mode": "grid", "grid": 30, "colors": 4}, "export": ["out.pixelcat"]}, self.folder)
        self.assertEqual((result["outputs"][0]["format"], result["frames"]), ("project", 3))
        p = Project.load(self.folder / "out.pixelcat")
        self.assertEqual((p.width, p.height, [f["duration"] for f in p.frames]), (30, 20, [80, 90, 100]))
        back = execute({"input": "out.pixelcat", "export": [{"path": "back.gif", "scale": 2}]}, self.folder)
        self.assertEqual((back["frames"], back["outputs"][0]["width"]), (3, 60))
        with self.assertRaisesRegex(ValueError, "already is pixel art"):
            execute({"input": "out.pixelcat", "convert": {"mode": "grid"}, "export": ["x.png"]}, self.folder)


class Studios(unittest.TestCase):
    """The server owns the project. The page and the agent change it through the same door."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.server, self.room = studio.serve(self.folder / "s.pixelcat", port=0, agent=True, size=(8, 8))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def call(self, path, payload=None):
        return studio.call(path, payload, url=self.url)

    def test_a_change_is_saved_at_once_and_can_be_taken_back_by_either_side(self):
        done = self.call("/api/op", {"op": {"op": "draw", "color": "#ff0000", "points": [[1, 1]]}, "by": "agent"})
        self.assertEqual((done["rev"], done["note"]), (1, "drew on layer 0, frame 0"))
        self.assertEqual(tuple(Project.load(self.folder / "s.pixelcat").composite(0)[1, 1]), (255, 0, 0, 255))
        state = self.call("/api/state")
        self.assertEqual((state["meta"]["undo"], state["meta"]["last"]), (1, ["drew on layer 0, frame 0", "agent"]))
        self.call("/api/undo", {})                                                        # the person takes back what the agent did
        self.assertFalse(Project.load(self.folder / "s.pixelcat").composite(0)[..., 3].any())
        self.call("/api/redo", {})
        self.assertTrue(Project.load(self.folder / "s.pixelcat").composite(0)[..., 3].any())

    def test_a_frame_becomes_a_loop_over_a_painted_area_and_undo_brings_it_back(self):
        self.call("/api/op", {"op": {"op": "resize", "width": 24, "height": 24}})
        self.call("/api/op", {"op": {"op": "draw", "color": "#2060c0", "rect": [0, 0, 24, 24]}})
        self.call("/api/op", {"op": {"op": "draw", "color": "#80c0ff", "points": [[x, y] for y in range(12, 24) for x in range(0, 24, 4)]}})
        water = [[x, y] for y in range(12, 24) for x in range(24)]
        sky = [[x, y] for y in range(6) for x in range(24)]
        done = self.call("/api/loop", {"frame": 0, "loop": {"seconds": 2, "fps": 8, "regions": [
            {"kind": "water", "amount": 2, "area": {"cells": water}}, {"kind": "fireflies", "amount": 4, "area": {"cells": sky}}]}})
        self.assertIn("made a loop of 16 frames", done["note"])
        project = Project.load(self.folder / "s.pixelcat")
        frames = np.stack([project.composite(i) for i in range(16)])
        self.assertEqual(frames.shape, (16, 24, 24, 4))
        self.assertFalse((frames[:, 12:] == frames[0, 12:]).all())             # the water moves
        self.assertTrue((frames[:, 6:12] == frames[0, 6:12]).all())            # what was not painted stands still
        self.assertTrue((frames[:, :6, :, :3] == (255, 230, 128)).all(axis=3).any())   # a firefly is out
        self.call("/api/undo", {})
        self.assertEqual(len(Project.load(self.folder / "s.pixelcat").frames), 1)
        with self.assertRaisesRegex(Exception, "at least one region"):
            self.call("/api/loop", {"frame": 0, "loop": {"regions": []}})

    def test_a_preview_shows_what_would_change_and_changes_nothing(self):
        self.call("/api/op", {"op": {"op": "draw", "color": "#ff0000", "rect": [0, 0, 8, 8]}})
        self.call("/api/op", {"op": {"op": "add-frame", "copy": 0}})
        self.call("/api/op", {"op": {"op": "draw", "frame": 1, "color": "#00ff00", "rect": [2, 2, 2, 2]}})
        seen = self.call("/api/preview", {"op": {"op": "hold", "layer": 0, "from": 0, "rect": [0, 0, 8, 8]}})
        self.assertEqual((len(seen["marks"][0]), len(seen["marks"][1]), sorted(seen["after"])), (0, 4, ["1"]))
        self.assertEqual(self.call("/api/state")["meta"]["rev"], 3)                       # nothing was done
        self.assertEqual(tuple(Project.load(self.folder / "s.pixelcat").composite(1)[2, 2]), (0, 255, 0, 255))

    def test_a_failed_operation_changes_nothing_and_says_why(self):
        with self.assertRaisesRegex(ValueError, "Unknown operation"):
            self.call("/api/op", {"op": {"op": "sharpen"}})
        with self.assertRaisesRegex(ValueError, "layer must be an integer"):
            self.call("/api/op", {"op": {"op": "draw", "layer": 7, "color": "#ffffff", "points": [[0, 0]]}})
        self.assertEqual(self.call("/api/state")["meta"]["rev"], 0)

    def test_the_chat_carries_what_the_person_is_looking_at(self):
        heard = {}
        waiter = threading.Thread(target=lambda: heard.update(self.call("/api/wait?slice=5")))
        waiter.start()
        for _ in range(50):
            if self.call("/api/state")["meta"]["listening"]:
                break
            threading.Event().wait(0.05)
        self.assertTrue(self.call("/api/state")["meta"]["listening"])
        self.call("/api/chat", {"text": "make it blue", "context": {"frame": 0, "layer": 0, "layerName": "Layer 1", "selection": [1, 2, 3, 4], "color": "#ff0000"}})
        waiter.join(8)
        self.assertEqual(studio.describe(heard["messages"][0], heard["project"]),
                         'YOU [s.pixelcat, frame 0, layer 0 "Layer 1", selection x 1 y 2, 3 by 4, colour #ff0000]: make it blue')
        self.call("/api/say", {"text": "Done."})
        self.assertEqual([(m["from"], m["text"]) for m in self.call("/api/state")["chat"]], [("you", "make it blue"), ("agent", "Done.")])
        self.assertEqual(self.call("/api/look?as=json")["viewing"]["selection"], [1, 2, 3, 4])

    def test_looking_shows_the_frame_enlarged(self):
        self.call("/api/op", {"op": {"op": "draw", "color": "#00ff00", "rect": [0, 0, 8, 8]}})
        with urllib.request.urlopen(self.url + "/api/look?scale=4") as response:
            image = Image.open(response)
            image.load()
        self.assertEqual((image.size, image.convert("RGB").getpixel((5, 5))), ((32, 32), (0, 255, 0)))

    def test_a_picture_comes_in_converted_and_goes_out_as_a_file(self):
        y, x = np.mgrid[0:64, 0:64]
        picture = np.dstack([x * 4, y * 4, np.full((64, 64), 90), np.full((64, 64), 255)]).astype(np.uint8)
        settings = urllib.request.quote(json.dumps({"convert": {"mode": "grid", "grid": 16, "colors": 8}}))
        request = urllib.request.Request(f"{self.url}/api/import?name=sky.png&how=project&settings={settings}", data=png_bytes(picture), method="POST")
        with urllib.request.urlopen(request) as response:
            done = json.loads(response.read())
        self.assertEqual((done["summary"]["width"], done["summary"]["height"]), (16, 16))
        state = self.call("/api/state")
        self.assertEqual((state["doc"]["width"], state["doc"]["layers"][0]["name"]), (16, "sky"))
        with urllib.request.urlopen(self.url + "/api/export?format=gif&scale=2") as response:
            self.assertEqual((response.headers["Content-Disposition"], response.read()[:6]), ('attachment; filename="s.gif"', b"GIF89a"))

    def test_the_page_is_served_and_nothing_outside_it(self):
        with urllib.request.urlopen(self.url + "/") as response:
            self.assertIn(b"pixel-cat studio", response.read())
        with urllib.request.urlopen(self.url + "/app.js") as response:
            self.assertIn("javascript", response.headers["Content-Type"])
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.url + "/../project.py")


if __name__ == "__main__":
    unittest.main()
