import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from raster_lab import engine as e, lettering, study


class LetteringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = {r['id']: r for r in json.loads(Path('raster_lab/experiments.json').read_text())}
        cls.preferences = json.loads(Path('raster_lab/preferences.json').read_text())

    def test_dots_short_junction_arms_and_closed_loops_survive(self):
        mask = np.zeros((80, 80), bool)
        mask[10, 10] = True  # Punctuation, not a move-only path.
        mask[20:45, 20] = True
        mask[30, 17:24] = True  # Short junction arms.
        mask[50:65, 50] = mask[50:65, 64] = True
        mask[50, 50:65] = mask[64, 50:65] = True
        d = e.Drawing(16, 16, .15, {})
        paths = lettering.component_paths(mask, d)
        d.paths['K'] = paths
        self.assertTrue(all(len(p) >= 2 and e.path_length(p) > 0 for p in paths))
        self.assertTrue(any(p[0] == p[-1] for p in paths))
        paint = e.stroke_mask(d, 'K', 800, 800, 1)
        # Every original skeleton center remains represented, including tiny arms.
        for y, x in zip(*np.nonzero(mask)):
            self.assertGreater(paint[y * 10 + 5, x * 10 + 5], 0)
        self.assertEqual(lettering.topology(paint > 64), dict(components=3, holes=1))

    def test_inclusive_threshold_connects_faint_ink_without_bridging_white(self):
        gray = np.full((80, 80), 255, np.uint8)
        gray[20:60, 40] = 30
        gray[35:45, 40] = 150
        image = Image.fromarray(gray).convert('RGB')
        dark = e.draw(image, dict(self.recipes['letter-dark'], long_edge_in=16/25.4))
        inclusive = e.draw(image, dict(self.recipes['letter-inclusive'], long_edge_in=16/25.4))
        self.assertEqual(len(dark.paths['K']), 2)
        self.assertEqual(len(inclusive.paths['K']), 1)
        gray[38:42, 40] = 255
        white_gap = e.draw(Image.fromarray(gray).convert('RGB'), dict(self.recipes['letter-inclusive'], long_edge_in=16/25.4))
        self.assertEqual(len(white_gap.paths['K']), 2)

    def test_thinning_does_not_erase_small_solid_dots(self):
        mask = np.zeros((80, 80), bool)
        mask[10:12, 10:12] = True
        mask[20, 20] = True
        mask[30:32, 30:33] = True
        d = e.Drawing(16, 16, .15, {})
        thin = lettering.narrow_skeleton(mask, d, self.recipes['letter-dark'])
        self.assertEqual(lettering.topology(thin)['components'], 3)
        d.paths['K'] = lettering.component_paths(thin, d)
        self.assertEqual(len(d.paths['K']), 3)
        self.assertEqual(lettering.topology(e.stroke_mask(d, 'K', 800, 800, 1) > 64)['components'], 3)

    def test_crossing_retains_the_junction_rejected_by_the_ridge_reference(self):
        rgb = np.full((100, 100, 3), 255, np.uint8)
        rgb[20:80, 48:51] = 0
        rgb[48:51, 30:70] = 0
        image = Image.fromarray(rgb)
        reference = e.draw(image, dict(self.recipes['letter-ridge'], long_edge_in=20/25.4))
        connected = e.draw(image, dict(self.recipes['letter-inclusive'], long_edge_in=20/25.4))
        self.assertEqual(e.stroke_mask(reference, 'K', 1000, 1000, 1)[495, 495], 0)
        paint = e.stroke_mask(connected, 'K', 1000, 1000, 1)
        self.assertEqual(paint[495, 495], 255)
        self.assertEqual(lettering.topology(paint > 64), dict(components=1, holes=0))

    def test_wide_fill_veto_keeps_attached_narrow_ink(self):
        mask = np.zeros((100, 100), bool)
        mask[10:65, 10:65] = True
        mask[64:90, 35:38] = True
        d = e.Drawing(20, 20, .15, {})
        thin = lettering.narrow_skeleton(mask, d, self.recipes['letter-dark'])
        self.assertFalse(thin[30:45, 30:45].any())
        self.assertGreater(thin[70:85, 35:38].sum(), 10)

    def test_clipped_border_dots_never_produce_empty_paths(self):
        mask = np.zeros((100, 100), bool)
        mask[0, 0] = mask[-1, -1] = mask[50, 50] = True
        d = e.Drawing(10, 10, .3, {})
        d.paths['K'] = lettering.component_paths(mask, d)
        self.assertEqual(len(d.paths['K']), 1)
        e.render(d)
        for p in d.paths['K']:
            self.assertGreaterEqual(len(p), 2)
            self.assertTrue(all(.151 <= x <= 9.849 and .151 <= y <= 9.849 for x, y in p))

    def test_diagnostic_masks_and_counts_are_reproducible(self):
        image = Image.new('RGB', (80, 80), 'white')
        image.putpixel((60, 20), (0, 0, 0))
        recipe = dict(self.recipes['letter-dark'], long_edge_in=16/25.4)
        drawing = e.draw(image, recipe)
        with tempfile.TemporaryDirectory() as tmp:
            report = lettering.diagnostics(image, drawing, recipe, Path(tmp))
            self.assertEqual(report['regions']['upper_right_quadrant']['isolated_skeleton_pixels'], 1)
            for name in ['threshold-mask', 'accepted-skeleton']:
                with Image.open(Path(tmp) / (name + '.png')) as mask:
                    self.assertEqual(mask.size, image.size)
                    self.assertEqual(mask.getpixel((60, 20)), 0)

    def test_per_ink_widths_reach_cairo_svg_and_ink_budget(self):
        d = e.Drawing(20, 20, .3, {'K': [[[2, 5], [18, 5]]], 'C': [[[2, 15], [18, 15]]]}, {'K': .15})
        masks = {ink: e.stroke_mask(d, ink, 2000, 2000, 1) for ink in 'KC'}
        widths = {ink: np.sum(mask[:, 1000].astype(float)/255) for ink, mask in masks.items()}
        self.assertAlmostEqual(widths['K'] / widths['C'], .5, delta=.03)
        svg = ET.fromstring(e.svg(d, 'Mixed pens'))
        groups = {el.get('id'): el for el in svg.iter() if el.get('id')}
        self.assertEqual(groups['ink-K'].get('stroke-width'), '0.15')
        self.assertIsNone(groups['ink-C'].get('stroke-width'))  # inherits 0.30
        metrics = e.metrics(d)
        self.assertAlmostEqual(metrics['ink_area_ratio'], (16*.15 + 16*.3)/400)
        self.assertEqual(metrics['by_ink']['K']['pen_mm'], .15)
        self.assertEqual(metrics['by_ink']['C']['pen_mm'], .3)

    def test_selected_fine_k_geometry_and_screen_sampling_are_independent(self):
        recipe = study.resolve_recovery(self.recipes['choice-full'], 'recovery-detail-fine', self.recipes)
        rgb = np.full((128, 128, 3), (130, 180, 200), np.uint8)
        rgb[15:115, 60:64] = 20
        image = Image.fromarray(rgb)
        with patch('raster_lab.study.engine.analysis_image', wraps=e.analysis_image) as analysis:
            drawing = e.draw(image, recipe)
        self.assertEqual([call.args[1] for call in analysis.call_args_list], [2048, 1000])
        chosen = study.resolve_recovery(self.recipes['choice-ink'], 'recovery-detail-fine', self.recipes)
        fine = e.draw(image, chosen)
        self.assertEqual(drawing.paths['K'], fine.paths['K'])
        self.assertEqual(drawing.ink_width('K'), .15)
        self.assertEqual(drawing.ink_width('C'), .3)
        self.assertEqual(fine, e.draw(image, self.recipes['recovery-detail-fine']))

    def test_votes_are_frozen_per_round(self):
        old = study.round_preferences(self.preferences, 3)
        new = study.round_preferences(self.preferences, 4)
        self.assertEqual(old['images']['happy-shreksgiving']['recovery'], 'stroke-linked')
        self.assertEqual(new['images']['happy-shreksgiving']['recovery'], 'recovery-detail-fine')
        for id in ['principals-office', 'game-night']:
            self.assertEqual(new['images'][id]['recovery'], 'stroke-wide')
        historical = study.resolve_recovery(self.recipes['recovery-reference'], old['images']['happy-shreksgiving']['recovery'], self.recipes)
        self.assertEqual(historical['pen_mm'], .3)
        self.assertEqual(historical['recovery_profile_id'], 'stroke-linked')

    def test_screen_sweep_keeps_selected_recovery_and_matched_peak(self):
        screens = [r for r in self.recipes.values() if r.get('round') == 4 and r['study'] == 'screens']
        self.assertEqual(len(screens), 4)
        self.assertEqual({r['density'] for r in screens}, {.7})
        self.assertTrue(all(r['screen_carrier'] and r['use_profile_pen'] for r in screens))
        self.assertEqual(self.recipes['choice-75-wide']['edge_fade_mm'], 12.7)
        self.assertEqual(self.recipes['choice-75-wide']['interior_ink'], .75)


if __name__ == '__main__':
    unittest.main()
