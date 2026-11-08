import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image

from raster_lab import engine as e
from raster_lab.study import emphasis, resolve_recovery


class FeedbackRoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = {r['id']: r for r in json.loads(Path('raster_lab/experiments.json').read_text())}
        cls.preferences = json.loads(Path('raster_lab/preferences.json').read_text())

    def test_chosen_reference_reproduces_each_profile_and_removes_stale_seeds(self):
        rgb = np.full((128, 128, 3), 245, np.uint8)
        rgb[10:118, 62:65] = 70
        image = Image.fromarray(rgb)
        prototype = dict(self.recipes['recovery-reference'], ridge_seed_contrast=.9)
        for profile in ['stroke-sensitive', 'stroke-wide', 'stroke-linked']:
            resolved = resolve_recovery(prototype, profile, self.recipes)
            self.assertEqual(e.draw(image, resolved), e.draw(image, self.recipes[profile]))
            self.assertEqual(resolved['recovery_profile_id'], profile)
            self.assertEqual('ridge_seed_contrast' in resolved, profile == 'stroke-linked')
        self.assertEqual(prototype['ridge_seed_contrast'], .9)

    def test_all_new_screen_columns_have_matched_peak_and_black_geometry(self):
        screens = [r for r in self.recipes.values() if r.get('round') == 3 and r['study'] == 'screens']
        self.assertEqual(len(screens), 5)
        for image, preference in self.preferences['images'].items():
            resolved = [resolve_recovery(r, preference['recovery'], self.recipes) for r in screens]
            recovery = [{k:v for k,v in r.items() if k.startswith('ridge_') or k in ['min_length_mm','pen_mm','analysis_long_edge_px']} for r in resolved]
            self.assertTrue(all(r == recovery[0] for r in recovery))
            self.assertEqual({r['density'] for r in resolved}, {.7})
            self.assertEqual({r['screen_carrier'] for r in resolved}, {True})
        self.assertFalse(self.preferences['images']['the-visitor']['screen_enabled'])
        self.assertFalse(self.preferences['images']['principals-office']['screen_enabled'])

    def test_increasing_interior_preserves_peak_and_raises_only_the_interior(self):
        rgb = np.full((200, 200, 3), [160, 90, 60], np.uint8)
        rgb[:, 100:] = [40, 200, 90]
        drawing = e.Drawing(100, 100, .3, {})
        fields = [emphasis(rgb, drawing, self.recipes[r]) for r in ['tone-25', 'tone-50', 'tone-75']]
        for field, floor in zip(fields, [.25, .5, .75]):
            self.assertAlmostEqual(float(field[100, 20]), floor)
            self.assertGreater(field.max(), .99)
        self.assertTrue(np.all(fields[0] <= fields[1]))
        self.assertTrue(np.all(fields[1] <= fields[2]))
        wider = emphasis(rgb, drawing, self.recipes['tone-25-wide'])
        self.assertGreater(float(wider[100, 85]), float(fields[0][100, 85]))

    def test_open_recovery_retains_faint_isolated_marks_without_a_seed(self):
        rgb = np.full((256, 256, 3), 250, np.uint8)
        rgb[30:226, 127:130] = 239
        image = Image.fromarray(rgb)
        settings = dict(long_edge_in=64 / 25.4)
        linked = e.draw(image, dict(self.recipes['stroke-linked'], **settings))
        opened = e.draw(image, dict(self.recipes['recovery-open'], **settings))
        self.assertEqual(e.metrics(linked)['meters'], 0)
        self.assertGreater(e.metrics(opened)['meters'], .04)

    def test_native_detail_keeps_source_pixels_without_upscaling(self):
        image = Image.new('RGB', (828, 1071), 'white')
        self.assertEqual(e.analysis_image(image).shape[:2], (1000, 773))
        self.assertEqual(e.analysis_image(image, 2048).shape[:2], (1071, 828))
        self.assertEqual(e.analysis_image(Image.new('RGB', (100, 60)), 2048).shape[:2], (60, 100))

    def test_native_fine_pen_changes_width_without_changing_interior_geometry(self):
        rgb = np.full((120, 120, 3), 250, np.uint8)
        rgb[20:100, 58:62] = 80
        image = Image.fromarray(rgb)
        a = e.draw(image, self.recipes['recovery-detail'])
        b = e.draw(image, self.recipes['recovery-detail-fine'])
        self.assertEqual(a.paths, b.paths)
        self.assertTrue(a.paths['K'])
        self.assertAlmostEqual(e.metrics(b)['ink_area_ratio'] / e.metrics(a)['ink_area_ratio'], .5)

    def test_extra_sensitivity_does_not_restore_flat_fill_skeletons_or_double_lines(self):
        for recipe_id in ['recovery-open', 'recovery-detail']:
            recipe = dict(self.recipes[recipe_id], long_edge_in=64 / 25.4)
            rgb = np.full((256, 256, 3), 245, np.uint8)
            rgb[30:226, 30:226] = 20
            self.assertEqual(e.metrics(e.draw(Image.fromarray(rgb), recipe))['paths'], 0)
            rgb[:] = 245
            rgb[20:236, 126:130] = 20
            self.assertEqual(e.metrics(e.draw(Image.fromarray(rgb), recipe))['paths'], 1)
            rgb[:] = 245
            cv2.circle(rgb, (128, 128), 70, (20, 20, 20), 3)
            paths = e.draw(Image.fromarray(rgb), recipe).paths['K']
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0][0], paths[0][-1])


if __name__ == '__main__':
    unittest.main()
