"""The studio page under a real pointer: what a hand presses is what answers.

These tests start a browser without a window (see browser.py) and are skipped where there is none.
"""

import base64
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from browser import Browser
from pixel_cat import studio
from pixel_cat.project import Project, png_bytes

SKY, BLOCK = (120, 170, 230, 255), (200, 60, 60, 255)


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser = Browser()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def setUp(self):
        """A sky with a red block on it, two frames, open in the studio."""
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        scene = np.zeros((24, 32, 4), dtype=np.uint8)
        scene[...] = SKY
        scene[8:16, 12:20] = BLOCK
        Project.from_frames([scene, scene], [100, 100], "Scene").save(self.folder / "s.pixelcat")
        self.scene = scene
        self.server, self.room = studio.serve(self.folder / "s.pixelcat", port=0)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.browser.open("about:blank")                                            # the page of the test before is gone first,
        self.browser.until("window.studio ? 0 : 1")                                 # so that it is not taken for this one
        self.browser.open(self.url)
        self.browser.until("window.studio && window.studio.doc && document.querySelector('.cel') ? 1 : 0")

    # ------------------------------------------------------------ where things are on the screen

    def spot(self, selector):
        """The middle of a control."""
        return self.browser.run(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()")

    def cell(self, x, y):
        """The middle of a cell of the picture."""
        return self.browser.run(f"(() => {{ const s = window.studio, r = document.getElementById('stage').getBoundingClientRect(); return [r.left + s.panX + ({x} + 0.5) * s.zoom, r.top + s.panY + ({y} + 0.5) * s.zoom]; }})()")

    def saved(self):
        """The project as it stands in its file, once the page has nothing more to send."""
        self.browser.until("window.studio.pending === 0 && !window.studio.drawing ? 1 : 0")
        return Project.load(self.folder / "s.pixelcat")

    def changes(self):
        return self.room.state()["meta"]["rev"]

    # ------------------------------------------------------------ the tests

    def test_a_stroke_lands_under_the_pointer_on_one_frame(self):
        self.browser.drag(self.cell(3, 3), self.cell(9, 3))
        self.browser.until("window.studio.meta.undo === 1 ? 1 : 0")
        project = self.saved()
        drawn = (project.cel(0, 0) != self.scene).any(axis=2)
        self.assertEqual({(int(x), int(y)) for y, x in np.argwhere(drawn)}, {(x, 3) for x in range(3, 10)})
        self.assertEqual(tuple(project.cel(0, 0)[3, 5]), (242, 166, 90, 255))       # the colour in hand
        self.assertTrue((project.cel(0, 1) == self.scene).all())                    # the other frame is another picture

    def soft(self):
        """Put a layer on top that one can see through: two swatches of nearly the same colour and strength."""
        swatches = np.zeros((24, 32, 4), dtype=np.uint8)
        swatches[2:6, 2:6] = (42, 36, 32, 128)
        swatches[2:6, 12:16] = (40, 36, 32, 128)
        swatches[10:14, 2:30, :3] = np.random.default_rng(2).integers(0, 256, (4, 28, 3))
        swatches[10:14, 2:30, 3] = np.random.default_rng(3).integers(20, 236, (4, 28))
        self.room.change({"op": "place", "name": "Soft", "frames": [base64.b64encode(png_bytes(swatches)).decode()]}, "you")
        self.browser.until("window.studio.doc.layers.length === 2 && window.studio.pending === 0 ? 1 : 0")
        self.browser.run("window.studio.layer = 1")
        return swatches

    def test_a_stroke_changes_its_cell_and_no_soft_cell_beside_it(self):
        swatches = self.soft()
        self.browser.press(*self.cell(20, 20))                                      # one cell, with the pencil
        self.browser.until("window.studio.meta.undo === 2 ? 1 : 0")
        now = self.saved().cel(1, 0)
        self.assertEqual({(int(x), int(y)) for y, x in np.argwhere((now != swatches).any(axis=2))}, {(20, 20)})

    def test_a_moved_piece_arrives_exactly_as_it_was(self):
        swatches = self.soft()
        self.browser.key("v")
        self.browser.drag(self.cell(3, 3), self.cell(3, 8))                         # the whole layer, five cells down
        self.browser.until("window.studio.meta.undo === 2 && window.studio.pending === 0 ? 1 : 0")
        down = np.zeros_like(swatches)
        down[5:] = swatches[:-5]
        self.assertTrue((self.saved().cel(1, 0) == down).all())                     # every cell holds what it held, the soft ones too
        self.browser.drag(self.cell(3, 8), self.cell(3, 3))                         # and back up
        self.browser.until("window.studio.meta.undo === 3 && window.studio.pending === 0 ? 1 : 0")
        self.assertTrue((self.saved().cel(1, 0) == swatches).all())                 # there and back: the picture it was
        self.browser.drag(self.cell(3, 3), self.cell(13, 3))                        # ten cells to the right
        self.browser.until("window.studio.meta.undo === 4 && window.studio.pending === 0 ? 1 : 0")
        now = self.saved().cel(1, 0)
        self.assertTrue((now[2:6, 12:16] == (42, 36, 32, 128)).all())               # where the other swatch was: this one's colour, two steps off
        self.assertTrue((now[2:6, 22:26] == (40, 36, 32, 128)).all() and not now[2:6, 2:6, 3].any())

    def test_the_layer_in_hand_stays_in_hand_when_the_list_shifts(self):
        self.room.change({"op": "add-layer", "name": "Rail"}, "you")
        self.browser.until("window.studio.doc.layers.length === 2 && window.studio.pending === 0 ? 1 : 0")
        self.browser.press(*self.spot("#layerList .layer"))                          # the top row: the rail
        self.browser.until("window.studio.doc.layers[window.studio.layer].name === 'Rail' ? 1 : 0")
        self.room.change({"op": "add-layer", "name": "Came in below", "at": 0}, "agent")   # as an appended shot does
        self.browser.until("window.studio.doc.layers.length === 3 && window.studio.pending === 0 ? 1 : 0")
        self.assertEqual(self.browser.run("window.studio.doc.layers[window.studio.layer].name"), "Rail")
        self.browser.press(*self.cell(5, 5))                                         # so the next stroke goes where it was meant to go
        self.browser.until("window.studio.meta.undo === 3 ? 1 : 0")
        project = self.saved()
        self.assertEqual([(layer["name"], layer["cels"][0] is not None and bool(layer["cels"][0][5, 5, 3])) for layer in project.layers],
                         [("Came in below", False), ("Scene", True), ("Rail", True)])
        self.assertTrue((project.cel(1, 0) == self.scene).all())                     # the scene below is untouched
        self.browser.press(*self.spot("#downLayer"))                                 # moving it keeps it in hand as well
        self.browser.until("window.studio.doc.layers[1].name === 'Rail' && window.studio.pending === 0 ? 1 : 0")
        self.assertEqual(self.browser.run("window.studio.doc.layers[window.studio.layer].name"), "Rail")

    def test_a_change_made_while_the_page_starts_is_not_missed(self):
        self.browser.open("about:blank")                                            # the page of setUp is closed, so the next one to read is the new one
        self.browser.until("window.studio ? 0 : 1")
        self.room.change({"op": "add-layer", "name": "Rail"}, "you")
        state = self.room.state

        def old_news():                                                             # the page reads the project, and before it listens
            answer = state()                                                        # an agent puts a layer below the others
            self.room.state = state
            self.room.change({"op": "add-layer", "name": "Agent layer", "at": 0}, "agent")
            return answer

        self.room.state = old_news
        self.browser.open(self.url)
        # told where the project stands as soon as it listens, the page looks again
        self.browser.until("window.studio && window.studio.doc && window.studio.doc.layers.length === 3 && window.studio.pending === 0 ? 1 : 0")
        self.assertEqual(self.browser.run("window.studio.doc.layers[window.studio.layer].name"), "Scene")   # in hand: the layer it showed in hand
        self.browser.press(*self.cell(5, 5))
        self.browser.until("window.studio.meta.undo === 3 ? 1 : 0")
        project = self.saved()
        self.assertEqual([(layer["name"], layer["cels"][0] is not None and tuple(layer["cels"][0][5, 5]) == (242, 166, 90, 255)) for layer in project.layers],
                         [("Agent layer", False), ("Scene", True), ("Rail", False)])

    def test_a_stroke_goes_to_its_layer_when_the_list_shifts_under_the_pencil(self):
        start, end = self.cell(3, 3), self.cell(9, 3)
        self.browser.down(*start)
        self.browser.pull(start, end)                                               # the pencil is on the picture
        self.room.change({"op": "add-layer", "name": "Came in below", "at": 0}, "agent")   # and an agent puts a layer below the one it draws on
        self.browser.up(*end)
        self.browser.until("window.studio.doc.layers.length === 2 && window.studio.pending === 0 && !window.studio.drawing ? 1 : 0")
        project = self.saved()
        below, scene = project.layers
        self.assertEqual((below["name"], scene["name"]), ("Came in below", "Scene"))
        self.assertTrue(below["cels"][0] is None or not below["cels"][0][..., 3].any())   # nothing on the layer that came in
        drawn = (scene["cels"][0] != self.scene).any(axis=2)
        self.assertEqual({(int(x), int(y)) for y, x in np.argwhere(drawn)}, {(x, 3) for x in range(3, 10)})   # the stroke is on the layer it was drawn on
        self.assertEqual(self.browser.run("window.studio.doc.layers[window.studio.layer].name"), "Scene")

    def test_a_page_that_outlived_its_studio_does_not_draw_into_the_next_one(self):
        elsewhere = np.zeros((24, 32, 4), dtype=np.uint8)
        elsewhere[...] = (30, 40, 50, 255)
        other = Project.from_frames([elsewhere], [100], "Elsewhere")
        with self.room.lock:                                                         # the studio stops and one with another project starts in its place:
            self.room.project, self.room.run, self.room.rev = other, "the next run", 0   # for the page the same address, with another run behind it
            self.room.undo.clear()
            other.save(self.room.path)
        self.browser.press(*self.cell(5, 5))                                         # the page still shows the old project, and its pencil comes down
        self.browser.until("window.studio.doc.layers[0].name === 'Elsewhere' && window.studio.pending === 0 ? 1 : 0")   # turned away, it looks again
        self.assertEqual(self.room.rev, 0)                                           # and nothing was drawn into the project it had never shown
        self.assertTrue((Project.load(self.folder / "s.pixelcat").composite(0) == elsewhere).all())
        self.browser.press(*self.cell(5, 5))                                         # now it draws on what it shows
        self.browser.until("window.studio.meta.undo === 1 && window.studio.pending === 0 ? 1 : 0")
        self.assertEqual(tuple(Project.load(self.folder / "s.pixelcat").composite(0)[5, 5]), (242, 166, 90, 255))

    def test_a_page_looks_again_when_the_studio_it_hears_is_another_run(self):
        self.room.project.apply({"op": "add-layer", "name": "Unheard"})              # something this page was never told
        self.room.publish({"type": "hello", "run": "another run", "rev": 0})         # the line is back, and at its end is a studio that counts from nothing
        self.browser.until("window.studio.doc.layers.length === 2 && window.studio.pending === 0 ? 1 : 0")
        self.assertEqual(self.browser.run("window.studio.doc.layers[window.studio.layer].name"), "Scene")

    def test_a_panel_takes_its_own_presses(self):
        self.browser.press(*self.spot("#tidy"))
        self.browser.until("!document.getElementById('cleanup').hidden ? 1 : 0")
        # the pencil is in hand and the panel lies on the canvas: a press on its button belongs to the button
        self.browser.press(*self.spot("#tidyGo"))
        self.browser.until("!document.getElementById('proposal').hidden ? 1 : 0")
        self.browser.press(*self.spot("#proposalCancel"))
        self.browser.until("document.getElementById('proposal').hidden ? 1 : 0")
        zoom = self.browser.run("window.studio.zoom")
        self.browser.wheel(*self.spot("#cleanup h3"), -300)                          # the wheel on a panel is the panel's too
        self.assertEqual(self.browser.run("window.studio.zoom"), zoom)
        self.browser.wheel(*self.cell(28, 20), -300)
        self.browser.until(f"window.studio.zoom > {zoom} ? 1 : 0")                   # on the picture it zooms
        self.browser.press(*self.spot("#cleanupClose"))
        self.browser.until("document.getElementById('cleanup').hidden ? 1 : 0")
        self.assertEqual(self.changes(), 0)                                         # and nothing was drawn beneath the panel
        self.assertTrue((self.saved().cel(0, 0) == self.scene).all())

    def test_a_selection_stays_while_a_change_is_looked_at(self):
        self.browser.key("m")
        self.browser.drag(self.cell(2, 2), self.cell(9, 9))
        self.assertEqual(self.browser.run("JSON.stringify(window.studio.sel)"), '{"x":2,"y":2,"w":8,"h":8}')
        self.browser.press(*self.spot("#tidy"))
        self.browser.until("!document.getElementById('cleanup').hidden ? 1 : 0")
        self.browser.press(*self.spot("#tidyGo"))                                    # with the box tool in hand
        self.browser.until("!document.getElementById('proposal').hidden ? 1 : 0")
        self.assertEqual(self.browser.run("JSON.stringify(window.studio.sel)"), '{"x":2,"y":2,"w":8,"h":8}')

    def test_a_selected_piece_becomes_a_part_and_its_place_is_filled(self):
        self.browser.key("m")
        self.browser.drag(self.cell(12, 8), self.cell(19, 15))                      # around the block
        self.browser.press(*self.spot("#partLayer"))
        self.browser.until("!document.getElementById('partMenu').hidden ? 1 : 0")
        self.browser.press(*self.spot('#partMenu [data-fill="1"]'))
        self.browser.until("window.studio.doc.layers.length === 2 && window.studio.pivoting ? 1 : 0")
        self.browser.press(*self.cell(12, 8))                                       # the next press says where it turns
        self.browser.until("window.studio.doc.layers[1].joint.pivot[0] === 12.5 && !window.studio.pivoting ? 1 : 0")
        project = self.saved()
        below, part = project.layers
        self.assertEqual((part["name"], part["joint"]["pivot"]), ("Scene part", [12.5, 8.5]))
        for frame in range(2):
            self.assertTrue((part["cels"][frame][8:16, 12:20] == BLOCK).all())
            self.assertTrue((below["cels"][frame] == SKY).all())                    # the sky closed behind the block
            self.assertTrue((project.composite(frame) == self.scene).all())


if __name__ == "__main__":
    unittest.main()
