import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from raster_lab import engine as e, lettering
from raster_lab.build import build
from raster_lab.highlights import highlight_screen
from raster_lab.waves import wave_paths


class HighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = {r['id']: r for r in json.loads(Path('raster_lab/experiments.json').read_text())}

    def test_ink_error_stays_bounded_including_round_caps(self):
        t = np.arange(0, 2000, .04)
        for id in ['highlight-short', 'highlight-long']:
            recipe = self.recipes[id]
            for ratio in [.01, .08, .3, .65, .95]:
                gate = highlight_screen(t, np.full(t.size, ratio), np.ones(t.size, bool), 7, 60, recipe, .3)
                edges = np.diff(np.r_[False, gate, False].astype(int))
                lengths = (np.where(edges == -1)[0] - np.where(edges == 1)[0] - 1) * .04
                actual = np.sum(lengths + math.pi * .3 / 4)
                desired = ratio * len(t) * .04
                self.assertLess(abs(actual - desired), recipe['highlight_run_mm'] * 3)
                self.assertTrue(gate.any() and (~gate).any())

    def test_white_barriers_and_dark_regions_do_not_inherit_light_debt(self):
        t = np.arange(0, 100, .04)
        ratio = np.full(t.size, .3)
        ratio[500:1000] = 0
        ratio[1500:2000] = 1.4
        support = np.ones(t.size, bool); support[1100:1200] = False
        active = highlight_screen(t, ratio, support, 3, 0, self.recipes['highlight-short'], .3)
        self.assertFalse(active[500:1000].any())
        self.assertFalse(active[1100:1200].any())
        self.assertTrue(active[1500:2000].all())

    def test_darker_wave_runs_stay_continuous(self):
        d = e.Drawing(35, 35, .3, {})
        control = self.recipes['highlight-low-cutoff']
        for tone in [.4, .6, .9]:
            channel = np.full((120, 120), tone, np.float32)
            reference = wave_paths(channel, d, 0, control)
            actual = wave_paths(channel, d, 0, self.recipes['highlight-short'])
            # At the page edges the smoothing may sample outside support. Interior
            # dark rows must be continuous and geometrically unchanged.
            long_runs = lambda paths: sum(e.path_length(p) > 33 for p in paths)
            self.assertEqual(long_runs(reference), long_runs(actual))
            d.paths['K'] = reference
            old = e.stroke_mask(d, 'K', 350, 350, 2)[20:330,20:330].astype(float)
            d.paths['K'] = actual
            new = e.stroke_mask(d, 'K', 350, 350, 2)[20:330,20:330].astype(float)
            # Edge cuts can change the 0.02 mm RDP approximation's sample points.
            self.assertLess(np.mean(np.abs(old-new))/255, .004)

    def test_white_is_blank_and_uniform_light_tones_have_monotone_real_coverage(self):
        d = e.Drawing(50, 50, .3, {})
        coverages = []
        for tone in [0, .02, .06, .12, .2, .3, .36, .5]:
            d.paths['K'] = wave_paths(np.full((200, 200), tone, np.float32), d, 0, self.recipes['highlight-short'])
            _, coverage = e.render(d)
            coverages.append(coverage)
            if tone == 0:self.assertEqual(d.paths['K'], [])
            for p in d.paths['K']:
                self.assertGreaterEqual(len(p), 2)
                self.assertTrue(all(.15 <= x <= 49.85 and .15 <= y <= 49.85 for x,y in p))
        self.assertTrue(all(a < b for a,b in zip(coverages, coverages[1:])), coverages)
        # Low coverage should follow demand within one absolute percentage point.
        for tone, coverage in zip([.02, .06, .12, .2, .3], coverages[1:6]):
            self.assertAlmostEqual(coverage, tone * .7, delta=.012)

    def test_dither_is_reproducible_and_decorrelates_rows(self):
        t = np.arange(0, 150, .04)
        ratio = np.full(t.size, .25)
        support = np.ones(t.size, bool)
        f = lambda row: highlight_screen(t, ratio, support, row, 0, self.recipes['highlight-short'], .3)
        np.testing.assert_array_equal(f(4), f(4))
        self.assertGreater(np.mean(f(4) != f(5)), .2)
        short = f(4)
        long = highlight_screen(t, ratio, support, 4, 0, self.recipes['highlight-long'], .3)
        self.assertGreater(np.count_nonzero(np.diff(short)), np.count_nonzero(np.diff(long)))

    def test_light_strokes_remain_inside_supported_ink(self):
        d = e.Drawing(50, 50, .3, {})
        channel = np.full((200, 200), .15, np.float32)
        channel[:, 80:120] = 0
        paths = wave_paths(channel, d, 0, self.recipes['highlight-short'])
        for path in paths:
            self.assertFalse(any(21 < x < 29 for x, y in path))
        self.assertTrue(paths)

    def test_lettering_pen_and_interpolation_are_separate_variables(self):
        gray = np.full((100, 100), 255, np.uint8)
        gray[20:80, 48:51] = 0
        image = Image.fromarray(gray).convert('RGB')
        native = e.draw(image, dict(self.recipes['letter-native'], long_edge_in=20/25.4))
        narrow = e.draw(image, dict(self.recipes['letter-narrow'], long_edge_in=20/25.4))
        self.assertEqual(native.paths, narrow.paths)
        self.assertAlmostEqual(e.metrics(narrow)['ink_area_ratio'] / e.metrics(native)['ink_area_ratio'], .5)
        cubic = lettering.analysis_gray(image, self.recipes['letter-cubic'])
        self.assertEqual(cubic.shape, (200, 200))
        self.assertEqual(lettering.analysis_gray(image, self.recipes['letter-native']).shape, (100, 100))
        self.assertTrue(np.any((cubic > 0) & (cubic < 255)))
        preview, _ = e.render(e.Drawing(25.4, 25.4, .075, {}), 450)
        self.assertEqual(preview.size, (450, 450))

    def test_retaining_controls_keeps_keys_and_rejects_changed_recipes_or_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root/'state'; output = root/'output'
            (state/'inputs').mkdir(parents=True)
            image = state/'inputs'/'test.jpg'; Image.new('RGB', (30, 30), 'white').save(image)
            source = dict(id='test',title='Test',artist='Test',file='test.jpg',pixels=[30,30],sha256=hashlib.sha256(image.read_bytes()).hexdigest())
            sources = state/'sources.json';sources.write_text(json.dumps(dict(images=[source])))
            recipe = dict(self.recipes['edges-fine'],id='original',long_edge_in=1)
            recipes = root/'recipes.json';recipes.write_text(json.dumps([recipe]))
            first = build(state, output, recipes)
            added = dict(recipe,id='new',low=recipe['low']+1)
            recipes.write_text(json.dumps([recipe, added]))
            with patch('raster_lab.engine.draw', wraps=e.draw) as draw:
                second = build(state, output, recipes, retain_previous=True)
            self.assertEqual(draw.call_count, 1)
            self.assertEqual(draw.call_args.args[1]['id'], 'new')
            self.assertEqual(first['cells']['test']['original'], second['cells']['test']['original'])
            self.assertIn(first['cells']['test']['original']['key'], second['retained_result_keys'])
            recipes.write_text(json.dumps([dict(recipe,low=recipe['low']+2)]))
            with self.assertRaisesRegex(ValueError, 'experiment changed'):build(state, output, recipes, retain_previous=True)
            recipes.write_text(json.dumps([recipe, added]))
            Image.new('RGB', (30,30), 'black').save(image)
            source['sha256']=hashlib.sha256(image.read_bytes()).hexdigest();sources.write_text(json.dumps(dict(images=[source])))
            with self.assertRaisesRegex(ValueError, 'source or resolved recipe changed'):build(state, output, recipes, retain_previous=True)


if __name__ == '__main__':
    unittest.main()
