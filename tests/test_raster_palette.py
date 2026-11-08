import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
from skimage.color import rgb2lab, rgb2hsv

from raster_lab import combined, engine, palette


class PaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.colors = dict(C='#009f7c', M='#ff1493', Y='#ffff00', K='#000000')
        cls.expanded = dict(cls.colors, blue='#245bd6', red='#ff0000')
        cls.recipes = json.loads(Path('raster_lab/palette-experiments.json').read_text())

    def separate(self, colors, pixels, max_coverage=.7):
        fields, report = palette.separate(pixels, colors, {k:.25 for k in colors}, max_coverage=max_coverage)
        c = np.stack(list(fields.values()), -1)
        self.assertTrue(np.isfinite(c).all())
        self.assertTrue(np.all((c >= 0) & (c <= max_coverage+1e-12)))
        return c, palette.predict(c, palette.colors_rgb(tuple(colors.items())))

    def test_white_and_known_achievable_colors(self):
        _, white = self.separate(self.expanded, np.full((1, 1, 3), 255))
        np.testing.assert_array_equal(white, 1)
        colors = {'ocean-blue':'#245bd6'}
        target = palette.predict(np.array([[[.43]]]), palette.colors_rgb(tuple(colors.items())))
        c, predicted = self.separate(colors, target*255)
        self.assertLess(float(np.max(np.abs(c-.43))), .005)
        self.assertLess(float(np.linalg.norm(rgb2lab(predicted)-rgb2lab(target))), .3)

    def test_blue_pen_expands_reachable_blues(self):
        target = np.array([[[40, 75, 180], [70, 90, 170], [60, 100, 200]]], dtype=float)
        _, old = self.separate(self.colors, target)
        coverage, extended = self.separate(self.expanded, target)
        desired = rgb2lab(target/255)
        before = np.linalg.norm(rgb2lab(old)-desired, axis=-1)
        after = np.linalg.norm(rgb2lab(extended)-desired, axis=-1)
        self.assertTrue(np.all(after < before*.7), (before, after))
        self.assertTrue(np.all(coverage[..., 4] > .3))

    def test_optimizing_actual_swatches_beats_ideal_cmyk(self):
        rng = np.random.default_rng(740)
        pixels = rng.integers(0, 256, (25, 20, 3), dtype=np.uint8)
        cmyk = combined.remaining_channels(pixels, np.zeros(pixels.shape[:2]), 'cmyk')
        baseline = palette.predict(.7*np.stack(list(cmyk.values()), -1),
                                   palette.colors_rgb(tuple(self.colors.items())))
        _, optimized = self.separate(self.colors, pixels)
        target = rgb2lab(pixels/255)
        before = np.linalg.norm(rgb2lab(baseline)-target, axis=-1).mean()
        after = np.linalg.norm(rgb2lab(optimized)-target, axis=-1).mean()
        self.assertLess(after, before*.8, (before, after))

    def test_material_changes_are_not_stale_cache_hits(self):
        image = np.array([[[35, 80, 180]]])
        a, _ = self.separate({'custom':'#245bd6'}, image)
        b, _ = self.separate({'custom':'#ff0000'}, image)
        self.assertGreater(float(np.max(np.abs(a-b))), .1)

    def test_lut_has_no_abrupt_mixture_jump_on_blue_ramp(self):
        pixels = np.linspace([35, 60, 140], [150, 180, 240], 1000)[None, ...]
        c, _ = self.separate(self.expanded, pixels)
        self.assertLess(float(np.abs(np.diff(c, axis=1)).max()), .02)

    def test_saturated_blue_does_not_become_purple_at_screen_ceiling(self):
        # Reported regression: CIELAB fitted saturated blue with maximum blue
        # AND magenta, expanding the purple corner across the first ramp band.
        hue = np.linspace(210, 265, 100)
        from skimage.color import hsv2rgb
        target = hsv2rgb(np.stack([hue/360, np.ones(100), np.ones(100)], -1))[None]
        fields, report = palette.separate(target*255, self.expanded,
            {k:.25 for k in self.expanded}, max_coverage=.572287572276123, color_space='oklab')
        predicted = palette.predict(np.stack(list(fields.values()), -1),
                                    palette.colors_rgb(tuple(self.expanded.items())))
        rendered_hue = rgb2hsv(predicted)[0,:,0]*360
        self.assertTrue(report['converged'])
        self.assertLess(float(np.max(np.abs(rendered_hue-hue))), 16)
        self.assertLess(float(rendered_hue[hue < 240].max()), 251)
        self.assertGreater(float(fields['blue'][0,45]), .5)

    def test_oklab_keeps_white_and_neutral_gray(self):
        pixels = np.repeat(np.array([1, .7, .3, 0])[None,:,None],3,axis=2)
        fields, _ = palette.separate(pixels*255,self.expanded,
            {k:.25 for k in self.expanded}, color_space='oklab')
        predicted = palette.predict(np.stack(list(fields.values()), -1),
                                    palette.colors_rgb(tuple(self.expanded.items())))
        np.testing.assert_array_equal(predicted[0,0], 1)
        self.assertLess(float(np.ptp(predicted, axis=-1).max()), .04)

    def test_selected_screening_keeps_recovery_separate_and_removes_its_demand(self):
        from PIL import ImageDraw
        recipe = dict(self.recipes[-1], long_edge_in=1, screen_inks=['K'],
                      ink_colors={'K':'#000000'}, ink_angles={'K':0}, black_policy='luminance')
        recipe.pop('paper')
        image = Image.new('RGB',(64,64),'white')
        ImageDraw.Draw(image).rectangle((12,12,52,52),fill='black')
        recovered, report = combined.generate(image,recipe)
        plain, _ = combined.generate(image,dict(recipe,black_policy='off'))
        self.assertGreater(report['recovered_paths'], 0)
        residual_length = sum(map(engine.path_length, recovered.paths['K'][report['recovered_paths']:]))
        self.assertLess(residual_length, sum(map(engine.path_length, plain.paths['K']))/2)
        self.assertEqual(set(recovered.paths), {'K'})
        self.assertEqual(report['separation']['colors'], {'K':'#000000'})

    def test_arbitrary_ink_paths_keep_width_and_white_paper(self):
        recipe = dict(self.recipes[-1], long_edge_in=1,
                      ink_colors={'custom-blue':'#245bd6'}, ink_angles={'custom-blue':90})
        recipe.pop('paper')
        blank, _ = combined.generate(Image.new('RGB', (60,60), 'white'), recipe)
        self.assertFalse(any(blank.paths.values()))
        blue, report = combined.generate(Image.new('RGB', (60,60), (45,85,180)), recipe)
        self.assertTrue(blue.paths['custom-blue'])
        for path in blue.paths['custom-blue']:
            for x, y in path:
                self.assertTrue(.125 <= x <= blue.width_mm-.125)
                self.assertTrue(.125 <= y <= blue.height_mm-.125)
        svg = ET.fromstring(engine.svg(blue, 'Custom ink'))
        self.assertEqual(svg.find('.//{*}g[@id="ink-custom-blue"]').attrib['stroke'], '#245bd6')
        curve = report['separation']['software_tone_curves']['custom-blue']['coverage']
        self.assertTrue(np.all(np.diff(curve) >= 0))
        self.assertLess(curve[-1], recipe['density'])

    def test_invalid_materials_fail_before_solving(self):
        for colors, widths in [((), ()), ((('a','#112233'),), (0,)),
                               ((('a','#112233'),), (float('nan'),))]:
            with self.assertRaises(ValueError):
                palette.lookup(colors, widths)

    def test_eight_independent_ink_ids_and_carousel_limit(self):
        colors = tuple((f'pen-{i}',c) for i,c in enumerate(
            ['#009f7c','#ff1493','#ffff00','#000000','#245bd6','#ff0000','#800080','#008000']))
        table, _ = palette.lookup(colors, (.25,)*8, grid=3)
        self.assertEqual(table.shape,(3,3,3,8))
        self.assertTrue(np.isfinite(table).all())
        with self.assertRaises(ValueError):
            palette.lookup(colors+(('ninth','#808080'),), (.25,)*9, grid=3)


if __name__ == '__main__':
    unittest.main()
