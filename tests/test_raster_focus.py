import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image

from raster_lab import engine as e
from raster_lab.focus import ink_focus, ridge_paths


class FocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = {r['id']: r for r in json.loads(Path('raster_lab/experiments.json').read_text())}
        cls.recipe = cls.recipes['focus-cmy-quarter']

    def test_flat_color_does_not_invent_edges_at_image_boundary(self):
        d = e.Drawing(80, 80, .3, {})
        rgb = np.full((240, 240, 3), (95, 120, 45), np.uint8)
        self.assertEqual(float(ink_focus(rgb, d, self.recipe).max()), 0)
        self.assertEqual(e.metrics(e.draw(Image.fromarray(rgb), self.recipe))['paths'], 0)

    def test_equal_lightness_hue_boundary_gets_two_sided_fade(self):
        lab = np.full((320, 320, 3), [55, -30, 20], np.float32)
        lab[:, 160:] = [55, 30, 20]
        rgb = np.round(cv2.cvtColor(lab, cv2.COLOR_Lab2RGB) * 255).astype(np.uint8)
        d = e.Drawing(80, 80, .3, {})
        field = ink_focus(rgb, d, self.recipe)
        self.assertGreater(field[160, 154], .8)
        self.assertGreater(field[160, 166], .8)
        self.assertLess(field[160, 140], .3)
        self.assertEqual(field[160, 125], 0)
        self.assertEqual(field[160, 195], 0)

    def test_weak_contrast_gets_less_ink_and_fade_distance_is_physical(self):
        d = e.Drawing(80, 80, .3, {})
        peaks = []
        for jump in [4, 12, 100]:
            rgb = np.full((320, 320, 3), 140, np.uint8)
            rgb[:, 160:] -= jump
            peaks.append(float(ink_focus(rgb, d, self.recipe).max()))
        self.assertEqual(peaks[0], 0)
        self.assertLess(peaks[1], peaks[2])
        self.assertGreater(peaks[2], .8)
        rgb = np.full((320, 320, 3), 220, np.uint8)
        rgb[:, 160:] = 40
        field = ink_focus(rgb, d, self.recipe)
        fine = ink_focus(np.repeat(np.repeat(rgb, 2, 0), 2, 1), d, self.recipe)
        self.assertLess(np.abs(field[160, :] - fine[320, 1::2]).max(), .09)

    def test_real_pen_coverage_fades_and_color_stays_on_its_own_side(self):
        rgb = np.full((320, 320, 3), [255, 0, 0], np.uint8)
        rgb[:, 160:] = [0, 255, 255]
        recipe = dict(self.recipe, long_edge_in=100 / 25.4, edge_fade_mm=12.7)
        drawing = e.draw(Image.fromarray(rgb), recipe)
        coverage = e.stroke_mask(drawing, 'C', 1000, 1000, 2).astype(float) / 255
        near, middle, far = [coverage[50:950, a:b].mean() for a, b in [(510, 540), (555, 580), (600, 625)]]
        self.assertGreater(near, middle)
        self.assertGreater(middle, far)
        self.assertEqual(coverage[:, :495].max(), 0)
        self.assertEqual(coverage[:, 635:].max(), 0)
        for ink in 'MY':
            other = e.stroke_mask(drawing, ink, 1000, 1000, 2)
            self.assertEqual(other[:, 505:].max(), 0)
        full = e.draw(Image.fromarray(rgb), dict(recipe, edge_fade_mm=0))
        narrow = e.draw(Image.fromarray(rgb), dict(recipe, edge_fade_mm=6.35))
        self.assertLess(e.metrics(drawing)['meters'], e.metrics(full)['meters'] * .35)
        self.assertLess(e.metrics(narrow)['meters'], e.metrics(drawing)['meters'] * .7)

    def test_dark_band_recovers_one_centerline_not_two_edges(self):
        d = e.Drawing(64, 64, .3, {})
        for width in [1, 2, 4, 6]:
            gray = np.full((256, 256), 245, np.uint8)
            gray[20:236, 128 - width // 2:128 - width // 2 + width] = 20
            paths = ridge_paths(gray, d, self.recipe)
            middle = [p for p in paths if min(q[1] for q in p) < 15 and max(q[1] for q in p) > 49]
            self.assertEqual(len(middle), 1, (width, len(paths)))
            self.assertGreater(e.path_length(middle[0]), 50)
            self.assertTrue(all(abs(x - 32) < .2 for x, y in middle[0] if 10 < y < 54))

    def test_step_edge_and_wide_flat_fill_do_not_become_dark_ridges(self):
        d = e.Drawing(64, 64, .3, {})
        step = np.full((256, 256), 245, np.uint8)
        step[:, 128:] = 20
        self.assertEqual(ridge_paths(step, d, self.recipe), [])
        fill = np.full_like(step, 245)
        fill[30:226, 30:226] = 20
        self.assertEqual(ridge_paths(fill, d, self.recipe), [])

    def test_two_nearby_ink_marks_remain_separate(self):
        d = e.Drawing(64, 64, .3, {})
        gray = np.full((256, 256), 245, np.uint8)
        gray[20:236, 124:126] = 20
        gray[20:236, 131:133] = 20
        paths = ridge_paths(gray, d, self.recipe)
        middle = [p for p in paths if min(q[1] for q in p) < 15 and max(q[1] for q in p) > 49]
        self.assertEqual(len(middle), 2)

    def test_diagonal_and_curved_ink_do_not_double_or_fragment(self):
        d = e.Drawing(64, 64, .3, {})
        diagonal = np.full((256, 256), 245, np.uint8)
        cv2.line(diagonal, (30, 30), (226, 226), 20, 3)
        paths = ridge_paths(diagonal, d, self.recipe)
        self.assertEqual(len(paths), 1)
        self.assertAlmostEqual(e.path_length(paths[0]), 49 * np.sqrt(2), delta=.5)
        circle = np.full_like(diagonal, 245)
        cv2.circle(circle, (128, 128), 70, 20, 3)
        paths = ridge_paths(circle, d, self.recipe)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1])
        self.assertAlmostEqual(e.path_length(paths[0]), 2 * np.pi * 17.5, delta=2)

    def test_new_variants_are_deterministic_and_white_is_blank(self):
        rgb = np.full((100, 100, 3), 255, np.uint8)
        for recipe in self.recipes.values():
            if recipe['method'] not in ('ridges', 'edge_wave'):
                continue
            small = dict(recipe, long_edge_in=1)
            self.assertEqual(e.metrics(e.draw(Image.fromarray(rgb), small))['paths'], 0)
        rgb[15:85, 49:51] = 0
        small = dict(self.recipes['focus-ink-quarter'], long_edge_in=1)
        first = e.draw(Image.fromarray(rgb), small)
        self.assertEqual(first, e.draw(Image.fromarray(rgb), small))
        for paths in first.paths.values():
            for path in paths:
                for x, y in path:
                    self.assertTrue(.15 <= x <= 25.25 and .15 <= y <= 25.25)


if __name__ == '__main__':
    unittest.main()
