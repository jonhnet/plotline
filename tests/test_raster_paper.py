import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from raster_lab import engine, paper


class PaperProofTests(unittest.TestCase):
    spec = dict(width_in=11, height_in=17, margin_in=.75, orientation='auto')

    def test_visitor_fit_keeps_aspect_and_centers_extra_vertical_space(self):
        fit = paper.layout((828, 1071), self.spec)
        self.assertEqual((fit['width_in'], fit['height_in']), (11, 17))
        self.assertAlmostEqual(fit['art_width_in'], 9.5)
        self.assertAlmostEqual(fit['art_height_in'], 9.5*1071/828)
        self.assertAlmostEqual(fit['offset_x_in'], .75)
        self.assertAlmostEqual(fit['offset_y_in'], (17-9.5*1071/828)/2)

    def test_orientation_and_fit_handle_wide_tall_and_square_images(self):
        for size in [(1200, 700), (700, 1200), (100, 100), (50, 1000)]:
            fit = paper.layout(size, self.spec)
            w, h = fit['art_width_in'], fit['art_height_in']
            self.assertAlmostEqual(w/h, size[0]/size[1])
            self.assertLessEqual(w, fit['width_in']-1.5+1e-10)
            self.assertLessEqual(h, fit['height_in']-1.5+1e-10)
            self.assertTrue(abs(w-fit['width_in']+1.5)<1e-10 or abs(h-fit['height_in']+1.5)<1e-10)
        landscape = paper.layout((1200, 700), self.spec)
        self.assertEqual((landscape['width_in'], landscape['height_in']), (17, 11))
        fixed = paper.layout((1200, 700), dict(self.spec, orientation='fixed'))
        self.assertEqual((fixed['width_in'], fixed['height_in']), (11, 17))

    def test_invalid_margins_and_nonfinite_dimensions_fail(self):
        for changes in [dict(margin_in=5.5), dict(margin_in=-1), dict(width_in=0),
                        dict(height_in=float('nan')), dict(margin_in=float('inf'))]:
            with self.assertRaises(ValueError):
                paper.layout((828, 1071), dict(self.spec, **changes))

    def test_sheet_export_translates_without_changing_pen_or_distances(self):
        fit = paper.layout((828, 1071), self.spec)
        art = engine.Drawing(fit['art_width_in']*25.4, fit['art_height_in']*25.4,
                             .25, {'K': [[[1, 1], [2, 3], [7, 3]]]})
        page = paper.place(art, fit)
        self.assertEqual(page.pen_mm, .25)
        self.assertAlmostEqual(engine.path_length(art.paths['K'][0]), engine.path_length(page.paths['K'][0]))
        self.assertEqual(art.paths['K'][0][0], [1, 1])
        for a, b in zip(art.paths['K'][0], page.paths['K'][0]):
            self.assertAlmostEqual(b[0]-a[0], fit['offset_x_in']*25.4)
            self.assertAlmostEqual(b[1]-a[1], fit['offset_y_in']*25.4)
        svg = ET.fromstring(engine.svg(page, 'Paper proof'))
        self.assertEqual(svg.attrib['width'], '279.400mm')
        self.assertEqual(svg.attrib['height'], '431.800mm')
        self.assertEqual(svg.find('{*}g').attrib['stroke-width'], '0.25')
        with self.assertRaises(ValueError):
            paper.place(engine.Drawing(10, 10, .25, {}), fit)

    def test_wider_pen_rebuilds_fill_instead_of_thickening_old_paths(self):
        recipes = json.loads(Path('raster_lab/experiments.json').read_text())
        recipe = dict(next(r for r in recipes if r['id']=='fill-paper-quarter'),
                      paper=dict(width_in=1.5, height_in=1.5, margin_in=.25),
                      lettering_upsample=1)
        pixels = np.full((254, 254), 255, np.uint8)
        pixels[20:230, 50] = 0  # 0.1 mm line: the 0.25 mm pen cannot fit.
        pixels[20:230, 150:160] = 0  # 1 mm line survives.
        image = Image.fromarray(pixels).convert('RGB')
        fine = engine.draw(image, dict(recipe, pen_mm=.075))
        thick = engine.draw(image, recipe)
        self.assertAlmostEqual(thick.width_mm, 25.4)
        self.assertEqual(thick.pen_mm, .25)
        self.assertTrue(any(x < 6 for p in fine.paths['K'] for x, y in p))
        self.assertTrue(thick.paths['K'])
        self.assertTrue(all(x > 14 for p in thick.paths['K'] for x, y in p))


if __name__ == '__main__':
    unittest.main()
