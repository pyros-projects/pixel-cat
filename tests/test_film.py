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


class PaintedProps(unittest.TestCase):
    """A prop that is lifted off its plate must leave nothing of itself behind."""

    def setUp(self):
        y, x = np.mgrid[0:60, 0:80]
        plate = np.zeros((60, 80, 3), dtype=np.uint8)
        plate[...] = (20, 40, 60)
        plate[:, :30] = (120, 70, 40)                               # two grounds meet behind the prop
        reach = np.hypot(x - 40, y - 30)
        plate[reach < 12] = (255, 190, 80)                          # a bright rim ...
        plate[reach < 10] = (200, 110, 30)                          # ... around the body
        self.plate = Image.fromarray(plate)
        self.wide = [[40 + round(13 * np.cos(a)), 30 + round(13 * np.sin(a))] for a in np.linspace(0, 2 * np.pi, 24, endpoint=False)]
        self.tight = [[40 + round(10 * np.cos(a)), 30 + round(10 * np.sin(a))] for a in np.linspace(0, 2 * np.pi, 24, endpoint=False)]

    def prop_colours_left(self, plate):
        art = np.asarray(plate).astype(int)
        return int(((art[..., 0] > 180) & (art[..., 1] > 90) & (art[..., 2] < 100)).sum())

    def test_the_hole_gets_the_ground_behind_it_and_no_trace_of_the_prop(self):
        plate = self.plate.copy()
        prop, bounds = render.lift(plate, self.wide)
        self.assertEqual(self.prop_colours_left(plate), 0)
        art = np.asarray(plate)
        self.assertEqual(tuple(int(v) for v in art[30, 28]), (120, 70, 40))    # each edge of the hole continues its own ground
        self.assertEqual(tuple(int(v) for v in art[30, 52]), (20, 40, 60))
        self.assertEqual(prop.size, (bounds[2] - bounds[0], bounds[3] - bounds[1]))

    def test_an_outline_that_cuts_through_the_rim_snaps_to_the_edge(self):
        from PIL import ImageDraw
        mask = Image.new("L", (80, 60))
        ImageDraw.Draw(mask).polygon([tuple(p) for p in self.tight], fill=255)
        leaked, ring = render.outline_leak(np.asarray(self.plate), np.asarray(mask) > 0)
        self.assertGreater(leaked.sum(), 0.5 * ring)                # nearly the whole ring is rim
        plate = self.plate.copy()
        render.lift(plate, self.tight)                              # snapped: the rim leaves with the prop
        self.assertEqual(self.prop_colours_left(plate), 0)
        with self.assertRaisesRegex(ValueError, "cuts through the prop"):
            render.lift(self.plate.copy(), self.tight, snap=False)

    def test_a_prop_starts_exactly_where_it_was_painted(self):
        layer = {"cutout": self.wide, "pivot": [0.5, 0], "pose": [[0, 41, 17, 1, 1, 0, 1], [1, 41, 17, 1, 1, 12, 1]]}
        plate = self.plate.copy()
        cfg = render.painted_prop(plate, layer, "plate.png")
        art, where = render.scale_layer((cfg.pop("_art"), cfg), 0)
        plate.paste(art, where, art)
        self.assertTrue((np.asarray(plate) == np.asarray(self.plate)).all())
        off = dict(layer, pose=[[0, 40, 17, 1, 1, 0, 1], [1, 40, 17, 1, 1, 12, 1]])
        with self.assertRaisesRegex(ValueError, "does not start where it was painted"):
            render.painted_prop(self.plate.copy(), off, "plate.png")

    def test_turning_keeps_a_thin_line_whole(self):
        art = Image.new("RGBA", (41, 41))
        for i in range(5, 36):
            art.putpixel((20, i), (255 - 5 * i, 200, 40 + 5 * i, 255))   # one cell wide, every cell another shade
        for degrees in (3, 7, 20, 33):
            turned = np.asarray(render.rotate_cells(art, degrees, (20, 20)))[..., 3] > 0
            rows = np.flatnonzero(turned.any(axis=1))
            self.assertEqual(len(rows), rows[-1] - rows[0] + 1)     # no gaps along its length
            self.assertGreaterEqual(len(rows), 25)
            self.assertLessEqual(turned.sum(axis=1).max(), 3)       # and it stays thin

    def test_turning_keeps_a_thin_line_whole_on_its_ground(self):
        art = Image.new("RGBA", (41, 41), (10, 20, 30, 255))        # the line sits on an opaque ground this time
        for i in range(5, 36):
            art.putpixel((20, i), (250, 220, 120, 255))
        for degrees in (3, 7, 20, 33):
            turned = np.asarray(render.rotate_cells(art, degrees, (20, 20)))
            bright = turned[..., 0] > 200
            rows = np.flatnonzero(bright.any(axis=1))
            self.assertEqual(len(rows), rows[-1] - rows[0] + 1, degrees)
            self.assertGreaterEqual(len(rows), 25)


if __name__ == "__main__":
    unittest.main()
