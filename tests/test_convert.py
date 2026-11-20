"""What a conversion promises. Every test builds its own input.

    uv run python -m unittest discover -s tests -v
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from pixel_cat.jobs import execute
from pixel_cat.palettes import PALETTES

ROOT = Path(__file__).resolve().parents[1]


def colours(image):
    """The distinct opaque colours of an RGBA array, as hex strings."""
    return {"#%02x%02x%02x" % tuple(c) for c in image[..., :3][image[..., 3] >= 128]}


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.rng = np.random.default_rng(11)

    def save(self, array, name="in.png"):
        Image.fromarray(np.asarray(array, dtype=np.uint8)).save(self.folder / name)
        return name

    def run_job(self, **job):
        job.setdefault("export", [{"path": "out.png", "scale": 1}])
        return execute(job, self.folder)

    def out(self, name="out.png"):
        return np.array(Image.open(self.folder / name).convert("RGBA"))

    def painting(self, w=320, h=200):
        """Smooth colour fields with a little texture, like a painted picture."""
        y, x = np.mgrid[0:h, 0:w]
        rgb = np.stack([120 + 100 * np.sin(x / 40), 110 + 90 * np.cos(y / 30), 140 + 80 * np.sin((x + y) / 55)], axis=2)
        rgb += self.rng.normal(0, 6, rgb.shape)
        return np.dstack([np.clip(rgb, 0, 255), np.full((h, w), 255)])


class Stills(Case):
    def test_grid_fits_the_longest_side_and_keeps_the_shape(self):
        result = self.run_job(input=self.save(self.painting()), convert={"mode": "grid", "grid": 64})
        self.assertEqual((result["width"], result["height"]), (64, 40))
        self.assertEqual(self.out().shape[:2], (40, 64))

    def test_the_colour_limit_holds(self):
        for limit in (2, 5, 16):
            result = self.run_job(input=self.save(self.painting()), convert={"mode": "grid", "grid": 80, "sampling": "area", "colors": limit})
            self.assertLessEqual(len(colours(self.out())), limit)
            self.assertEqual(result["colors"], len(colours(self.out())))

    def test_the_whole_colour_budget_is_spent(self):
        result = execute({"input": str(ROOT / "examples/images/potion.png"), "convert": {"mode": "grid", "grid": 128, "sampling": "crisp", "colors": 16},
                          "export": [{"path": "out.png", "scale": 1}]}, self.folder)
        self.assertEqual(result["colors"], 15)                      # fifteen colours and transparency make sixteen
        self.assertEqual(len(colours(self.out())), 15)

    def test_a_custom_palette_is_the_only_source_of_colour(self):
        palette = ["#102030", "#f0e0d0", "#c04040", "#40a060"]
        self.run_job(input=self.save(self.painting()), convert={"mode": "grid", "grid": 48, "palette": palette})
        self.assertLessEqual(colours(self.out()), set(palette))

    def test_exact_sampling_with_a_fixed_palette_is_the_same_on_every_machine(self):
        y, x = np.mgrid[0:96, 0:160]
        image = np.dstack([x * 255 // 159, y * 255 // 95, (x + y) % 256, np.full((96, 160), 255)])
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": 40, "palette": PALETTES["pico8"]})
        self.assertEqual(hashlib.sha256(self.out().tobytes()).hexdigest()[:16], "dc93498b4a5236d7")

    def test_area_sampling_averages_and_exact_sampling_does_not(self):
        checker = np.indices((64, 64)).sum(axis=0) % 2 * 255
        image = np.dstack([checker] * 3 + [np.full((64, 64), 255)])
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": 8, "sampling": "area", "colors": 256})
        self.assertTrue((np.abs(self.out()[..., :3].astype(int) - 128) <= 1).all())
        self.run_job(input="in.png", convert={"mode": "grid", "grid": 8, "sampling": "exact", "colors": 256})
        self.assertLessEqual(colours(self.out()), {"#000000", "#ffffff"})

    def test_crisp_sampling_never_blends_and_ink_keeps_a_thin_outline(self):
        image = np.full((80, 80, 4), 255, dtype=np.uint8)
        image[:, :, :3] = (230, 200, 60)
        image[:, 36:39, :3] = (20, 20, 30)                         # a line three pixels wide in cells ten wide
        name = self.save(image)
        self.run_job(input=name, convert={"mode": "grid", "grid": 8, "sampling": "crisp", "colors": 256})
        self.assertEqual(colours(self.out()), {"#e6c83c"})
        self.run_job(input=name, convert={"mode": "grid", "grid": 8, "sampling": "ink", "colors": 256})
        self.assertEqual(colours(self.out()), {"#e6c83c", "#14141e"})
        self.assertTrue((self.out()[:, 3, :3] == (20, 20, 30)).all())

    def test_transparency_survives(self):
        image = self.painting(120, 120)
        image[:, :40, 3] = 0
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": 30, "sampling": "crisp", "colors": 8})
        out = self.out()
        self.assertTrue((out[:, :10, 3] == 0).all() and (out[:, 10:, 3] == 255).all())
        self.assertLessEqual(len(colours(out)), 7)                 # transparency takes one of the eight

    def test_a_soft_edge_becomes_a_hard_one_and_stays_inside_the_colour_limit(self):
        y, x = np.mgrid[0:120, 0:120]
        reach = np.hypot(x - 60, y - 60)
        image = np.zeros((120, 120, 4), dtype=np.uint8)
        image[..., 0], image[..., 1], image[..., 2] = 60 + x, 200 - y, 90
        image[..., 3] = np.clip((52 - reach) * 20, 0, 254)          # a disc with a soft rim that never reaches full opacity
        for mode in ({"mode": "grid", "grid": 40, "sampling": "area", "colors": 8}, {"mode": "grid", "grid": 40, "sampling": "crisp", "colors": 8},
                     {"mode": "faithful", "width": 240, "pixel": 6, "palette": "source", "colors": 8}, {"mode": "artist", "width": 240, "pixel": 6, "palette": "source", "colors": 8}):
            self.run_job(input=self.save(image), convert=mode)
            out = self.out()
            self.assertEqual(set(np.unique(out[..., 3]).tolist()), {0, 255}, mode)
            visible = out[out[..., 3] > 0]
            self.assertLessEqual(len(np.unique(visible[:, :3], axis=0)), 7, mode)       # transparency takes the eighth
            self.assertFalse(out[out[..., 3] == 0].any(), mode)                         # empty cells carry no colour
        # soft keeps the rim's transparency, and its colours still come from the palette
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": 40, "sampling": "area", "colors": 8, "alpha": "soft"})
        out = self.out()
        self.assertGreater(len(set(np.unique(out[..., 3]).tolist()) - {0, 255}), 3)
        self.assertLessEqual(len(np.unique(out[out[..., 3] > 0][:, :3], axis=0)), 7)

    def test_a_small_area_with_its_own_colour_keeps_it(self):
        y, x = np.mgrid[0:200, 0:200]
        image = np.dstack([20 + x // 8, 40 + y // 6, 150 + (x + y) // 8, np.full((200, 200), 255)]).astype(np.uint8)
        image[90:110, 90:110, :3] = (240, 130, 20)                 # one percent of the picture
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": 100, "sampling": "area", "colors": 6})
        centre = self.out()[50, 50, :3].astype(int)
        self.assertTrue(centre[0] > 200 and centre[2] < 80, centre)

    def test_symmetry_mirrors_one_half(self):
        for mode in ({"mode": "grid", "grid": 50}, {"mode": "faithful", "width": 200, "pixel": 4, "palette": "source"}):
            self.run_job(input=self.save(self.painting()), convert=mode | {"symmetry": "left"})
            out = self.out()
            self.assertTrue((out == out[:, ::-1]).all())

    def test_faithful_and_artist_stay_inside_a_named_palette(self):
        for mode in ("faithful", "artist"):
            self.run_job(input=self.save(self.painting()), convert={"mode": mode, "width": 240, "pixel": 6, "palette": "dawn16"})
            self.assertLessEqual(colours(self.out()), set(PALETTES["dawn16"]))
            self.assertEqual(self.out().shape[1], 40)

    def test_a_short_palette_keeps_its_darkest_and_lightest_colour(self):
        self.run_job(input=self.save(self.painting()), convert={"mode": "faithful", "width": 240, "pixel": 6, "palette": "sepia", "colors": 2})
        self.assertEqual(colours(self.out()), {PALETTES["sepia"][0], PALETTES["sepia"][-1]})

    def test_display_scale_enlarges_whole_cells(self):
        result = self.run_job(input=self.save(self.painting()), convert={"mode": "grid", "grid": 32}, export=["big.png", {"path": "out.png", "scale": 1}])
        big, small = self.out("big.png"), self.out()
        k = result["displayScale"]
        self.assertGreater(k, 1)
        self.assertTrue((big == np.repeat(np.repeat(small, k, axis=0), k, axis=1)).all())


class Recommended(Case):
    def test_enlarged_pixel_art_gets_its_grid_back(self):
        art = self.rng.integers(0, 6, (24, 32))
        palette = np.array([[20, 20, 40], [230, 220, 200], [200, 60, 60], [60, 160, 90], [60, 90, 200], [240, 200, 60]])
        small = np.dstack([palette[art], np.full((24, 32), 255)]).astype(np.uint8)
        big = np.repeat(np.repeat(small, 8, axis=0), 8, axis=1)
        result = self.run_job(input=self.save(big))
        self.assertEqual(result["recommendation"]["sampling"], "exact")
        self.assertTrue((self.out() == small).all())

    def test_a_painting_gets_averaged_cells_and_a_cartoon_does_not(self):
        painted = self.run_job(input=self.save(self.painting(600, 400)))["recommendation"]
        self.assertEqual((painted["sampling"], painted["width"]), ("area", 256))
        y, x = np.mgrid[0:400, 0:400]
        reach = np.hypot(x - 201, y - 203)                          # a disc with a dark rim, on no grid
        flat = np.full((400, 400, 4), 255, dtype=np.uint8)
        flat[..., :3] = (70, 110, 200)
        flat[reach < 110, :3] = (15, 15, 25)
        flat[reach < 103, :3] = (250, 210, 70)
        drawn = self.run_job(input=self.save(flat))["recommendation"]
        self.assertEqual((drawn["sampling"], drawn["width"]), ("ink", 128))
        self.assertEqual(colours(self.out()), {"#466ec8", "#fad246", "#0f0f19"})

    def test_contrast_leaves_a_handful_of_colours(self):
        result = self.run_job(input=self.save(self.painting(400, 300)), convert={"mode": "recommended", "contrast": True})
        self.assertLessEqual(result["colors"], 6)


class Animations(Case):
    def gif(self, frames=6, noise=4):
        out = []
        for i in range(frames):
            image = np.zeros((120, 160, 3), dtype=np.float64)
            image[...] = (40, 70, 120)
            image[40:80, 10 + i * 20:50 + i * 20] = (240, 200, 60)   # a block that moves
            out.append(Image.fromarray(np.clip(image + self.rng.normal(0, noise, image.shape), 0, 255).astype(np.uint8)))
        out[0].save(self.folder / "in.gif", save_all=True, append_images=out[1:], duration=[80, 80, 120, 80, 80, 200][:frames], loop=0)
        return "in.gif"

    def frames_of(self, name):
        with Image.open(self.folder / name) as gif:
            out = []
            for i in range(gif.n_frames):
                gif.seek(i)
                out.append((np.array(gif.convert("RGB")), gif.info["duration"]))
        return out

    def test_a_gif_keeps_its_frames_and_timing(self):
        result = self.run_job(input=self.gif(), convert={"mode": "grid", "grid": 40, "sampling": "area", "colors": 8}, export=["out.gif"])
        self.assertEqual((result["frames"], result["durations"]), (6, [80, 80, 120, 80, 80, 200]))
        self.assertEqual([d for _, d in self.frames_of("out.gif")], [80, 80, 120, 80, 80, 200])

    def test_still_areas_hold_one_colour_and_motion_stays(self):
        job = dict(input=self.gif(noise=5), convert={"mode": "grid", "grid": 40, "sampling": "area", "colors": 32}, export=["out.gif"])
        steady = self.run_job(**job)
        frames = [f for f, _ in self.frames_of("out.gif")]
        corner = np.stack([f[20:, :] for f in frames])              # the block never comes here
        self.assertTrue((corner == corner[0]).all())
        self.assertFalse((frames[0] == frames[3]).all())
        self.assertGreater(steady["stabilized"], 0)
        loose = self.run_job(**job, media={"stability": 0})
        self.assertEqual(loose["stabilized"], 0)

    def blinkers(self):
        """Two small rectangles that swap between the same two colours on every frame, in opposite phase."""
        frames = []
        for i in range(8):
            image = np.zeros((32, 32, 3), dtype=np.uint8)
            image[...] = (20, 30, 60)
            image[10:16, 6:10] = (250, 220, 60) if i % 2 == 0 else (200, 60, 60)
            image[10:16, 22:26] = (200, 60, 60) if i % 2 == 0 else (250, 220, 60)
            frames.append(Image.fromarray(image))
        frames[0].save(self.folder / "blink.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
        return "blink.gif"

    def test_one_area_is_held_still_and_its_neighbour_keeps_moving(self):
        job = dict(input=self.blinkers(), convert={"mode": "grid", "grid": "native", "sampling": "exact", "colors": 3}, media={"stability": 0})
        self.run_job(**job, export=["plain.gif"])
        self.run_job(**job, edits=[{"op": "hold", "rect": [22, 10, 4, 6], "from": 0}], export=["held.gif"])
        plain, held = [f for f, _ in self.frames_of("plain.gif")], [f for f, _ in self.frames_of("held.gif")]
        right, left = np.stack([f[10:16, 22:26] for f in held]), np.stack([f[10:16, 6:10] for f in held])
        self.assertTrue((right == right[0]).all())                  # the right one stands still
        self.assertEqual(len({tuple(f[12, 7]) for f in held}), 2)   # the left one still shows both colours
        changed = sum(int((a != b).any(axis=2).sum()) for a, b in zip(plain, held))
        self.assertEqual(changed, 24 * 4)                           # exactly its 24 cells, in the four frames that differed
        outside = np.ones((32, 32), dtype=bool)
        outside[10:16, 22:26] = False
        self.assertTrue(all((a[outside] == b[outside]).all() for a, b in zip(plain, held)))
        # the same with a polygon and a chosen set of frames
        self.run_job(**job, edits=[{"op": "hold", "points": [[22, 10], [26, 10], [26, 16], [22, 16]], "from": 1, "frames": [0, 1, 2, 3]}], export=["some.gif"])
        some = [f for f, _ in self.frames_of("some.gif")]
        self.assertTrue(all((some[i][10:16, 22:26] == some[1][10:16, 22:26]).all() for i in range(4)))
        self.assertFalse((some[4][10:16, 22:26] == some[5][10:16, 22:26]).all())

    def test_the_check_finds_flicker_and_names_the_area_to_hold(self):
        from pixel_cat import check, frames as media
        images, _ = media.read(self.folder / self.blinkers())
        found = check.report(check.inspect(images))
        self.assertEqual(found["total"], {"flicker": 48 * 8, "fragments": 0, "holes": 0, "palette": 0})
        self.assertEqual(sorted(a["rect"] for a in found["areas"]), [[6, 10, 4, 6], [22, 10, 4, 6]])
        self.assertEqual(found["areas"][0]["frames"], list(range(8)))
        self.run_job(input="blink.gif", convert={"mode": "grid", "grid": "native", "sampling": "exact", "colors": 3}, media={"stability": 0},
                     edits=[{"op": "hold", "rect": found["areas"][0]["rect"]}, {"op": "hold", "rect": found["areas"][1]["rect"]}], export=["calm.gif"])
        calm, _ = media.read(self.folder / "calm.gif")
        self.assertEqual(check.report(check.inspect(calm))["total"]["flicker"], 0)

    def test_the_check_finds_fragments_holes_and_colours_off_the_palette(self):
        from pixel_cat import check
        image = np.zeros((20, 20, 4), dtype=np.uint8)
        image[2:18, 2:18] = (200, 60, 60, 255)
        image[8, 8] = 0                                             # a hole
        image[12, 12] = (250, 250, 250, 255)                        # a one-cell fragment
        marks = check.inspect([image], palette=["#c83c3c"], fragment=1)
        self.assertEqual({k: int(v.sum()) for k, v in marks.items()}, {"flicker": 0, "fragments": 1, "holes": 1, "palette": 1})
        self.assertTrue(marks["holes"][0, 8, 8] and marks["fragments"][0, 12, 12] and marks["palette"][0, 12, 12])

    def test_frames_can_be_thinned_and_sped_up(self):
        result = self.run_job(input=self.gif(), media={"frames": 3, "speed": 200}, convert={"mode": "grid", "grid": 40}, export=["out.gif"])
        self.assertEqual((result["frames"], result["durations"]), (3, [80, 100, 140]))

    def test_one_frame_as_png(self):
        self.run_job(input=self.gif(), convert={"mode": "grid", "grid": 40, "colors": 4}, export=[{"path": "out.png", "frame": 4, "scale": 1}])
        self.assertEqual(self.out().shape[:2], (30, 40))
        self.assertTrue(self.out()[15, 28, 0] > 150)               # the block has reached x = 90..130

    def test_a_sprite_sheet_becomes_a_loop(self):
        sheet = np.zeros((40, 120, 4), dtype=np.uint8)
        for i in range(3):
            sheet[10:30, i * 40 + 5 + i * 5:i * 40 + 20 + i * 5] = (250, 80, 80, 255)
        result = self.run_job(input=self.save(sheet), sheet={"columns": 3, "rows": 1, "delay": 150}, convert={"mode": "grid", "grid": 20, "colors": 4}, export=["out.gif"])
        self.assertEqual((result["frames"], result["durations"], result["width"], result["height"]), (3, [150] * 3, 20, 20))
        with Image.open(self.folder / "out.gif") as gif:
            self.assertEqual(gif.n_frames, 3)
            self.assertIn("transparency", gif.info)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_video_out_and_back_in(self):
        self.run_job(input=self.gif(), convert={"mode": "grid", "grid": 40, "colors": 8}, export=[{"path": "clip.mp4", "scale": 4}, {"path": "clip.webm", "scale": 4}])
        self.assertGreater((self.folder / "clip.webm").stat().st_size, 500)
        result = self.run_job(input="clip.mp4", media={"frames": 5}, convert={"mode": "grid", "grid": 40, "colors": 8}, export=["again.gif"])
        self.assertEqual((result["frames"], result["width"], result["height"]), (5, 40, 30))


class Loops(Case):
    def scene(self):
        """A bright sky with a cloud over a dark hill, and a striped lake below."""
        image = np.full((120, 160, 4), 255, dtype=np.uint8)
        image[:60, :, :3] = (120, 170, 230)
        image[10:22, 30:70, :3] = (250, 250, 250)                   # the cloud
        image[40:60, :, :3] = (30, 60, 40)                          # the hill
        image[60:, :, :3] = (40, 90, 160)
        image[60:, ::8, :3] = (90, 150, 210)                        # glints on the water
        return self.save(image)

    def frames_of(self, name):
        with Image.open(self.folder / name) as gif:
            out = []
            for i in range(gif.n_frames):
                gif.seek(i)
                out.append(np.array(gif.convert("RGB")))
        return out

    def test_a_still_becomes_a_loop_that_only_moves_its_regions(self):
        job = dict(input=self.scene(), convert={"mode": "grid", "grid": 160, "colors": 8}, export=["out.gif"],
                   loop={"seconds": 3, "fps": 8, "regions": [{"kind": "clouds", "amount": 2}, {"kind": "water"}]})
        result = self.run_job(**job)
        self.assertEqual((result["frames"], result["durations"]), (24, [125] * 24))
        frames = self.frames_of("out.gif")
        hill = np.stack([f[40:60] for f in frames])
        self.assertTrue((hill == hill[0]).all())                    # guessed areas: sky above the hill, water below
        self.assertFalse((frames[0][:40] == frames[8][:40]).all())  # the cloud moved
        self.assertFalse((frames[0][60:] == frames[5][60:]).all())  # the water moved
        colours = {tuple(c) for f in frames for c in np.unique(f.reshape(-1, 3), axis=0)}
        self.assertEqual(colours, {(120, 170, 230), (250, 250, 250), (30, 60, 40), (40, 90, 160), (90, 150, 210)})

    def test_the_loop_closes_without_a_jump(self):
        self.run_job(input=self.scene(), convert={"mode": "grid", "grid": 160, "colors": 8}, export=["out.gif"],
                     loop={"seconds": 4, "regions": [{"kind": "clouds"}, {"kind": "water"}, {"kind": "sway", "area": [0, 0.33, 1, 0.5]}]})
        frames = self.frames_of("out.gif")
        steps = [int((frames[i] != frames[(i + 1) % len(frames)]).any(axis=2).sum()) for i in range(len(frames))]
        self.assertLessEqual(steps[-1], max(steps[:-1]))

    def test_an_area_can_be_a_box_or_a_polygon(self):
        for area in ([0, 0, 0.5, 0.3], [[0, 0], [0.5, 0], [0.5, 0.3], [0, 0.3]]):
            self.run_job(input=self.scene(), convert={"mode": "grid", "grid": 160, "colors": 8}, export=["out.gif"],
                         loop={"seconds": 2, "regions": [{"kind": "clouds", "amount": 3, "area": area}]})
            frames = np.stack(self.frames_of("out.gif"))
            self.assertTrue((frames[:, :, 80:] == frames[0, :, 80:]).all())
            self.assertTrue((frames[:, 36:] == frames[0, 36:]).all())
            self.assertFalse((frames[:, :36, :80] == frames[0, :36, :80]).all())

    def test_fireflies_leaves_and_birds_stay_in_their_region_and_come_round(self):
        job = dict(input=self.scene(), convert={"mode": "grid", "grid": 160, "colors": 8}, export=["out.gif"])
        regions = [{"kind": "fireflies", "area": [0, 0.35, 1, 0.5], "amount": 4, "color": "#ffe680"},
                   {"kind": "leaves", "area": [0, 0, 0.5, 0.33], "amount": 4, "colors": ["#c9682c"], "wind": "left"},
                   {"kind": "birds", "area": [0.5, 0, 1, 0.3], "amount": 1, "color": "#101018"}]
        self.run_job(**job, loop={"seconds": 3, "fps": 8, "regions": regions})
        frames = np.stack(self.frames_of("out.gif"))
        new = {"flies": (255, 230, 128), "leaves": (201, 104, 44), "birds": (16, 16, 24)}
        where = {name: (frames == np.array(colour)).all(axis=3) for name, colour in new.items()}
        self.assertTrue(where["flies"].any() and where["leaves"].any() and where["birds"].any())
        self.assertFalse(where["flies"][:, :42].any() or where["flies"][:, 60:].any())      # only on the hill
        self.assertFalse(where["leaves"][:, :, 80:].any() or where["leaves"][:, 40:].any()) # only in the upper left
        self.assertFalse(where["birds"][:, :, :80].any() or where["birds"][:, 36:].any())   # only in the upper right
        spots = [np.argwhere(where["birds"][i]) for i in range(len(frames))]
        self.assertTrue(any(len(a) and len(b) and a[:, 1].mean() != b[:, 1].mean() for a, b in zip(spots, spots[1:])))   # they fly
        water = frames[:, 60:]
        self.assertTrue((water == water[0]).all())                                         # and nothing else moved
        with self.assertRaisesRegex(ValueError, "wind is"):
            self.run_job(**job, loop={"regions": [{"kind": "leaves", "wind": "up"}]})

    def test_a_painted_area_is_a_list_of_cells(self):
        cells = [[x, y] for y in range(5, 30) for x in range(20, 90)]
        self.run_job(input=self.scene(), convert={"mode": "grid", "grid": 160, "colors": 8}, export=["out.gif"],
                     loop={"seconds": 2, "regions": [{"kind": "clouds", "amount": 3, "area": {"cells": cells}}]})
        frames = np.stack(self.frames_of("out.gif"))
        self.assertFalse((frames[:, 5:30, 20:90] == frames[0, 5:30, 20:90]).all())
        outside = np.ones((120, 160), dtype=bool)
        outside[5:30, 20:90] = False
        self.assertTrue((frames[:, outside] == frames[0, outside]).all())

    def test_water_in_a_painted_area_never_jumps_a_gap(self):
        from pixel_cat import loops

        image = np.zeros((6, 40, 4), dtype=np.uint8)
        image[..., 3] = 255
        image[:, :18, 0] = 200                                      # red on the left of a gap
        image[:, 22:, 2] = 200                                      # blue on the right of it
        mask = np.ones((6, 40), dtype=bool)
        mask[:, 18:22] = False
        for shift in (-3, 2, 7):
            out = loops._shift_rows(image, mask, np.full(6, shift))
            self.assertTrue((out[:, :18, 2] == 0).all() and (out[:, 22:, 0] == 0).all())
            self.assertTrue((out[:, 18:22] == image[:, 18:22]).all())

    def test_a_loop_needs_a_still_and_known_kinds(self):
        with self.assertRaises(ValueError):
            self.run_job(input=self.scene(), loop={"regions": [{"kind": "fog"}]}, export=["out.gif"])
        with self.assertRaises(ValueError):
            self.run_job(input=self.scene(), loop={"regions": [{"kind": "water", "area": [0, 0, 2, 1]}]}, export=["out.gif"])


class Edits(Case):
    def sprite(self):
        """A red disc with a dark rim on a paper-coloured ground."""
        y, x = np.mgrid[0:96, 0:128]
        reach = np.hypot(x - 70, y - 50)
        image = np.full((96, 128, 4), 255, dtype=np.uint8)
        image[..., :3] = (236, 232, 220)
        image[reach < 30, :3] = (30, 20, 40)
        image[reach < 26, :3] = (220, 40, 50)
        return self.save(image)

    def edit(self, *steps, **convert):
        return self.run_job(input=self.sprite(), convert={"mode": "grid", "grid": 64, "colors": 8} | convert, edits=list(steps))

    def test_cutout_trim_and_outline_make_a_sprite(self):
        result = self.edit({"op": "cutout"}, {"op": "trim"}, {"op": "outline", "color": "#ffffff"})
        out = self.out()
        self.assertEqual((result["width"], result["height"]), (out.shape[1], out.shape[0]))
        self.assertEqual(out.shape[0], out.shape[1])                # a disc in a tight box, plus the outline
        self.assertEqual(colours(out), {"#1e1428", "#dc2832", "#ffffff"})
        self.assertTrue((out[0, 0] == 0).all() and out[out.shape[0] // 2, 0, 3] == 255)
        solid = out[..., 3] == 255
        self.assertTrue((out[..., :3][solid & ~np.roll(solid, 1, axis=1)] == 255).all())   # every left edge is outline

    def test_trim_keeps_a_margin_and_pad_adds_cells(self):
        self.edit({"op": "cutout"}, {"op": "trim", "margin": 2})
        tight = self.out().shape
        self.edit({"op": "cutout"}, {"op": "trim"}, {"op": "pad", "cells": [1, 2, 3, 4], "color": "#00ff00"})
        padded = self.out()
        self.assertEqual(padded.shape[:2], (tight[0] - 4 + 6, tight[1] - 4 + 4))
        self.assertEqual(tuple(padded[0, 0]), (0, 255, 0, 255))

    def test_replace_flip_and_shift(self):
        self.edit()
        plain = self.out()
        self.edit({"op": "replace", "from": "#dc2832", "to": "#2040e0"})
        self.assertEqual(colours(self.out()), {"#ece8dc", "#1e1428", "#2040e0"})
        self.edit({"op": "flip"})
        self.assertTrue((self.out() == plain[:, ::-1]).all())
        self.edit({"op": "shift", "x": 5, "y": -3})
        self.assertTrue((self.out() == np.roll(plain, (-3, 5), axis=(0, 1))).all())

    def test_clean_removes_a_near_miss_speck_and_keeps_a_contrasting_one(self):
        image = np.full((64, 64, 4), 255, dtype=np.uint8)
        image[..., :3] = (80, 120, 200)
        image[20, 20, :3] = (86, 126, 206)                          # almost the ground colour
        image[40, 40, :3] = (250, 240, 120)                         # a star
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": "native", "colors": 8}, edits=[{"op": "clean"}])
        self.assertEqual(colours(self.out()), {"#5078c8", "#faf078"})

    def drawing(self):
        """A light field with a dark line three cells thick, as a drawn outline often arrives."""
        image = np.full((40, 60, 4), 255, dtype=np.uint8)
        image[..., :3] = (230, 200, 120)
        image[18:21, 5:55, :3] = (20, 20, 30)
        return image

    def lines_of(self, image):
        return (image[..., :3] == (20, 20, 30)).all(axis=2)

    def native(self, image, *steps):
        self.run_job(input=self.save(image), convert={"mode": "grid", "grid": "native", "colors": 256}, edits=list(steps))
        return self.out()

    def test_a_thick_line_is_thinned_to_one_cell_and_stays_whole(self):
        out = self.native(self.drawing(), {"op": "thin"})
        line = self.lines_of(out)
        self.assertTrue((line.sum(axis=0)[8:52] == 1).all())        # one cell thick along its length
        self.assertEqual(colours(out), {"#e6c878", "#14141e"})      # what was peeled off took the field's colour
        columns = np.flatnonzero(line.any(axis=0))
        self.assertEqual(len(columns), columns[-1] - columns[0] + 1)   # and no break in it
        broad = self.drawing()
        broad[5:16, 5:20, :3] = (20, 20, 30)                        # a dark area is no line: it stays as it is
        broad[30:32, 40:43, :3] = (20, 20, 30)                      # and neither is an eye
        kept = self.lines_of(self.native(broad, {"op": "thin"}))
        self.assertEqual((int(kept[5:16, 5:20].sum()), int(kept[30:32, 40:43].sum())), (11 * 15, 6))

    def test_a_break_in_a_line_is_closed_up_to_the_gap_allowed(self):
        image = np.full((20, 40, 4), 255, dtype=np.uint8)
        image[..., :3] = (230, 200, 120)
        image[10, 4:36, :3] = (20, 20, 30)
        image[10, 18:20, :3] = (230, 200, 120)                      # two cells are missing
        self.assertEqual(int(self.lines_of(self.native(image, {"op": "thin", "gap": 1}))[10, 4:36].sum()), 30)
        self.assertEqual(int(self.lines_of(self.native(image, {"op": "thin", "gap": 2}))[10, 4:36].sum()), 32)

    def test_the_rim_beside_a_line_goes_and_the_area_behind_stays(self):
        image = np.full((30, 30, 4), 255, dtype=np.uint8)
        image[..., :3] = (240, 220, 160)
        image[:, 14, :3] = (20, 20, 30)
        image[:, 13, :3] = (130, 120, 95)                           # halfway between line and field
        image[:, 15, :3] = (130, 120, 95)
        out = self.native(image, {"op": "halo"})
        self.assertEqual(colours(out), {"#f0dca0", "#14141e"})
        self.assertTrue(self.lines_of(out)[:, 14].all())

    def test_loose_bits_at_the_edge_go_and_details_inside_stay(self):
        image = np.zeros((30, 30, 4), dtype=np.uint8)
        image[8:22, 8:22] = (200, 60, 60, 255)
        image[4, 4:6] = (90, 90, 90, 255)                           # a loose bit out in the open
        image[14, 14] = (250, 250, 250, 255)                        # a highlight inside the drawing
        out = self.native(image, {"op": "fringe"})
        self.assertEqual(colours(out), {"#c83c3c", "#fafafa"})
        self.assertEqual(int((out[..., 3] > 0).sum()), 14 * 14)

    def test_a_cleanup_step_can_be_kept_to_an_area(self):
        out = self.native(self.drawing(), {"op": "thin", "rect": [0, 0, 30, 40]})
        line = self.lines_of(out)
        self.assertTrue((line.sum(axis=0)[8:28] == 1).all())        # thinned on the left
        self.assertTrue((line.sum(axis=0)[32:52] == 3).all())       # untouched on the right

    def test_a_wrong_step_names_itself(self):
        with self.assertRaisesRegex(ValueError, "Edit 2 .outline.: color"):
            self.edit({"op": "cutout"}, {"op": "outline", "color": "red"})
        with self.assertRaisesRegex(ValueError, "Edit 1: op is one of"):
            self.edit({"op": "sharpen"})


class CommandLine(Case):
    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "pixel_cat", *args], capture_output=True, text=True, cwd=self.folder, env={"PYTHONPATH": str(ROOT), "PATH": ""})

    def test_results_and_errors_are_json(self):
        self.save(self.painting())
        done = self.cli("convert", "in.png", "-o", "out.png", "--mode", "grid", "--grid", "20", "--scale", "1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["outputs"][0]["width"], 20)
        failed = self.cli("convert", "in.png", "-o", "out.png", "--mode", "grid", "--palette", "no-such-palette")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("Unknown palette", json.loads(failed.stderr)["error"])
        self.assertIn("pico8", json.loads(self.cli("catalog").stdout)["palettes"])
        checked = json.loads(self.cli("check", "in.png", "-o", "marked.png").stdout)
        self.assertEqual((checked["width"], set(checked["total"])), (320, {"flicker", "fragments", "holes", "palette"}))
        self.assertTrue((self.folder / "marked.png").exists())

    def test_a_failed_job_writes_nothing(self):
        self.save(self.painting())
        with self.assertRaises(ValueError):
            self.run_job(input="in.png", convert={"mode": "grid", "grid": 20}, export=["kept.png", {"path": "bad.png", "frame": 3}])
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["in.png"])


if __name__ == "__main__":
    unittest.main()
