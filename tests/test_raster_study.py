import json
import hashlib
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

from raster_lab import engine as e
from raster_lab.build import build
from raster_lab.focus import ridge_paths
from raster_lab.study import emphasis, source_palette
from raster_lab.waves import distributed_screen


class StudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes = {r['id']: r for r in json.loads(Path('raster_lab/experiments.json').read_text())}

    def test_sensitivity_recovers_faint_and_low_contrast_lines(self):
        d = e.Drawing(64, 64, .3, {})
        for background, foreground in [(245, 205), (80, 55)]:
            g = np.full((256, 256), background, np.uint8)
            g[20:236, 127:129] = foreground
            self.assertEqual(ridge_paths(g, d, self.recipes['ridge-ink']), [])
            paths = ridge_paths(g, d, self.recipes['stroke-sensitive'])
            self.assertEqual(len(paths), 1)
            self.assertGreater(e.path_length(paths[0]), 50)

    def test_larger_scale_recovers_heavy_mark_but_not_a_flat_fill(self):
        d = e.Drawing(64, 64, .3, {})
        g = np.full((256, 256), 245, np.uint8)
        g[20:236, 119:137] = 20
        strict = ridge_paths(g, d, self.recipes['stroke-sensitive'])
        wide = ridge_paths(g, d, self.recipes['stroke-wide'])
        self.assertLess(sum(map(e.path_length, strict)), 10)
        self.assertEqual(sum(e.path_length(p) > 45 for p in wide), 1)
        g[20:236, 20:236] = 20
        self.assertEqual(ridge_paths(g, d, self.recipes['stroke-wide']), [])

    def test_hysteresis_follows_a_fading_mark_without_keeping_detached_noise(self):
        d = e.Drawing(64, 64, .3, {})
        g = np.full((256, 256), 245, np.uint8)
        g[20:115, 127:130] = 40
        g[115:236, 127:130] = 230
        g[20:236, 60:63] = 230
        paths = ridge_paths(g, d, self.recipes['stroke-linked'])
        self.assertGreater(sum(map(e.path_length, paths)), 50)
        self.assertTrue(all(x > 25 for p in paths for x, y in p))
        # Do not fabricate a link over a white gap.
        g[120:140, 127:130] = 245
        paths = ridge_paths(g, d, self.recipes['stroke-linked'])
        self.assertTrue(all(y < 35 for p in paths for x, y in p))

    def test_fine_pen_changes_coverage_without_claiming_new_source_detail(self):
        rgb = np.full((256, 256, 3), 245, np.uint8)
        rgb[20:236, 127:130] = 20
        image = Image.fromarray(rgb)
        thick = e.draw(image, self.recipes['stroke-wide'])
        fine = e.draw(image, self.recipes['stroke-fine'])
        self.assertEqual(thick.paths, fine.paths)
        self.assertAlmostEqual(e.metrics(fine)['ink_area_ratio'] / e.metrics(thick)['ink_area_ratio'], .5)

    def test_monochrome_uses_only_k_including_shading_and_recovered_lines(self):
        gray = np.full((120, 120), 195, np.uint8)
        gray[10:110, 58:61] = 20
        image = Image.fromarray(np.repeat(gray[:, :, None], 3, 2))
        for r in self.recipes.values():
            if r['method'] != 'screen_study':
                continue
            recipe = dict(r, long_edge_in=2)
            drawing = e.draw(image, recipe)
            recovered = e.draw(image, dict(recipe, method='stroke_study'))
            self.assertEqual(set(drawing.paths), {'K'})
            self.assertTrue(recovered.paths['K'])
            self.assertEqual(drawing.paths['K'][:len(recovered.paths['K'])], recovered.paths['K'])
            self.assertGreater(len(drawing.paths['K']), len(recovered.paths['K']))

    def test_color_classification_tolerates_jpeg_chroma_but_keeps_color(self):
        mono = np.full((100, 100, 3), 200, np.uint8)
        mono[:, :, 1] += 3
        mono[0, 0] = [200, 0, 0]
        self.assertEqual(source_palette(mono), 'black')
        mono[:, :20] = [130, 170, 80]
        self.assertEqual(source_palette(mono), 'cmy')
        drawing = e.draw(Image.fromarray(mono), dict(self.recipes['screen-full'], long_edge_in=1))
        self.assertEqual(set(drawing.paths), set('CMYK'))

    def test_builder_records_resolved_palettes_without_mutating_the_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'state'
            (state / 'inputs').mkdir(parents=True)
            sources = []
            for name, color in [('mono', (140, 140, 140)), ('color', (160, 80, 40))]:
                path = state / 'inputs' / (name + '.jpg')
                Image.new('RGB', (40, 40), color).save(path)
                sources.append(dict(id=name, title=name, artist='Test', file=path.name,
                                    pixels=[40, 40], sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            (state / 'sources.json').write_text(json.dumps(dict(images=sources)))
            recipe = dict(self.recipes['screen-full'], long_edge_in=1)
            recipes = root / 'recipes.json'
            recipes.write_text(json.dumps([recipe]))
            matrix = build(state, root / 'output', recipes)
            self.assertEqual(matrix['experiments'][0]['palette'], 'source')
            for name, palette in [('mono', 'black'), ('color', 'cmy')]:
                cell = matrix['cells'][name]['screen-full']
                self.assertEqual(cell['signature']['recipe']['palette'], palette)
                self.assertEqual(set(cell['metrics']['by_ink']), {'K'} if palette == 'black' else set('CMYK'))

    def test_interior_floor_keeps_light_screening_and_retains_edge_peak(self):
        rgb = np.full((200, 200, 3), [160, 100, 60], np.uint8)
        d = e.Drawing(100, 100, .3, {})
        recipe = self.recipes['screen-blend']
        np.testing.assert_allclose(emphasis(rgb, d, recipe), .25)
        rgb[:, 100:] = [50, 180, 70]
        field = emphasis(rgb, d, recipe)
        self.assertGreater(field[:, 98:102].max(), .9)
        self.assertAlmostEqual(float(field[100, 20]), .25)
        image = Image.fromarray(np.full((120, 120, 3), 140, np.uint8))
        empty = e.draw(image, dict(self.recipes['screen-edge'], long_edge_in=2))
        light = e.draw(image, dict(recipe, long_edge_in=2))
        full = e.draw(image, dict(recipe, long_edge_in=2, interior_ink=1))
        self.assertEqual(e.metrics(empty)['meters'], 0)
        self.assertGreater(e.metrics(light)['meters'], 0)
        self.assertLess(e.metrics(light)['meters'], e.metrics(full)['meters'])

    def test_distributed_breaks_occur_in_dark_and_light_rows_without_row_lock(self):
        t = np.arange(0, 500, .05)
        r = self.recipes['screen-full-scatter']
        gates = []
        for row in range(20):
            _, gate = distributed_screen(t, np.full(t.size, 2.), row, 0, r)
            self.assertTrue(gate.any() and (~gate).any())
            self.assertGreater(np.count_nonzero(np.diff(gate)), 2)
            gates.append(gate)
        self.assertGreater(np.mean(gates), .86)
        self.assertLess(np.mean(gates), .97)
        self.assertGreater(np.mean(gates[0] != gates[1]), .04)
        _, light = distributed_screen(t, np.full(t.size, .15), 3, 0, r)
        self.assertGreater(light.mean(), .04)
        self.assertLess(light.mean(), .3)
        _, repeat = distributed_screen(t, np.full(t.size, .15), 3, 0, r)
        np.testing.assert_array_equal(light, repeat)

    def test_distributed_real_coverage_increases_with_tone_and_blank_stays_blank(self):
        r = dict(self.recipes['screen-full-scatter'], long_edge_in=2)
        coverage = []
        for value in [215, 140, 50]:
            drawing = e.draw(Image.new('RGB', (100, 100), (value,) * 3), r)
            _, ink = e.render(drawing)
            coverage.append(ink)
        self.assertTrue(all(a < b for a, b in zip(coverage, coverage[1:])), coverage)
        self.assertEqual(e.metrics(e.draw(Image.new('RGB', (100, 100), 'white'), r))['meters'], 0)


if __name__ == '__main__':
    unittest.main()
