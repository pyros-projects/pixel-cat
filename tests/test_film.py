"""The film renderer's camera."""

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".agents/skills/pixel-cat-film/scripts"))
import render  # noqa: E402


class Camera(unittest.TestCase):
    def setUp(self):
        cells = np.zeros((6, 8, 3), dtype=np.uint8)
        cells[:, ::2] = (200, 40, 40)                              # stripes one cell wide
        cells[:, 1::2] = (20, 60, 220)
        self.drawing = Image.fromarray(cells)

    def test_a_resting_camera_enlarges_whole_cells(self):
        for smooth in (True, False):
            out = render.look(self.drawing, render.window({}, 0, (8, 6), smooth), 4, smooth)
            self.assertEqual(out.shape, (24, 32, 3))
            self.assertTrue((out == np.repeat(np.repeat(np.asarray(self.drawing), 4, axis=0), 4, axis=1)).all())

    def test_a_smooth_camera_rests_between_cells_and_blends_only_their_edges(self):
        shot = {"camera": [[0, 4.375, 3, 2]]}                      # shows 4 cells from x = 2.375: 8 pixels a cell
        out = render.look(self.drawing, render.window(shot, 0, (8, 6), True), 4, True)
        row = out[0].astype(int)
        pure = {(200, 40, 40), (20, 60, 220)}
        mixed = [x for x in range(32) if tuple(row[x]) not in pure]
        self.assertEqual(mixed, [])                                # 0.375 cells is exactly 3 pixels: no straddling
        self.assertEqual([tuple(row[x]) for x in (0, 4, 5, 12, 13)], [(200, 40, 40), (200, 40, 40), (20, 60, 220), (20, 60, 220), (200, 40, 40)])
        shot = {"camera": [[0, 4.4, 3, 2]]}                        # 0.4 cells is 3.2 pixels: one blended pixel per edge
        row = render.look(self.drawing, render.window(shot, 0, (8, 6), True), 4, True)[0].astype(int)
        mixed = [x for x in range(32) if tuple(row[x]) not in pure]
        self.assertEqual(len(mixed), 4)
        self.assertTrue(all(b - a == 8 for a, b in zip(mixed, mixed[1:])))

    def test_a_grid_camera_shows_only_colours_of_the_drawing(self):
        shot = {"camera": [[0, 4.4, 3, 1.37]]}
        out = render.look(self.drawing, render.window(shot, 0, (8, 6), False), 4, False)
        self.assertEqual({tuple(c) for c in out.reshape(-1, 3)}, {(200, 40, 40), (20, 60, 220)})


if __name__ == "__main__":
    unittest.main()
