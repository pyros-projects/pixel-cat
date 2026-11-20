"""The studio page under a real pointer: what a hand presses is what answers.

These tests start a browser without a window (see browser.py) and are skipped where there is none.
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from browser import Browser
from pixel_cat import studio
from pixel_cat.project import Project

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
