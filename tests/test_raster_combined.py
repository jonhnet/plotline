import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from raster_lab import combined, engine, lettering
from raster_lab.check_portfolio import check_joined_fill


class CombinedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r=next(r for r in json.loads(Path('raster_lab/experiments.json').read_text()) if r['id']=='combined-neutral')
        cls.recipe=dict(r,long_edge_in=1,screen_analysis_long_edge_px=128)
        del cls.recipe['paper']

    def test_neutral_mask_keeps_black_but_does_not_fill_dark_colors(self):
        rgb=np.full((100,100,3),255,np.uint8)
        rgb[10:40,10:40]=[10,10,10]
        rgb[10:40,60:90]=[0,100,0]
        rgb[60:90,10:40]=[100,0,0]
        rgb[60:90,60:90]=[0,0,100]
        image=Image.fromarray(rgb)
        neutral=combined.ink_mask(image,self.recipe)
        dark=combined.ink_mask(image,dict(self.recipe,black_policy='luminance'))
        self.assertTrue(neutral[40,40])
        for y,x in [(40,150),(150,40),(150,150)]:
            self.assertTrue(dark[y,x]);self.assertFalse(neutral[y,x])

    def test_monochrome_masks_are_unchanged_by_neutral_guard(self):
        rng=np.random.default_rng(5)
        image=Image.fromarray(rng.integers(0,256,(64,64),dtype=np.uint8)).convert('RGB')
        np.testing.assert_array_equal(combined.ink_mask(image,self.recipe),combined.ink_mask(image,dict(self.recipe,black_policy='luminance')))

    def test_monochrome_selection_keeps_colored_compression_fringes(self):
        rgb=np.full((100,100,3),255,np.uint8);rgb[20:80,45:55]=[120,128,140]
        image=Image.fromarray(rgb)
        recipe=dict(self.recipe,palette='black')
        np.testing.assert_array_equal(combined.ink_mask(image,recipe),combined.ink_mask(image,dict(recipe,black_policy='luminance')))

    def test_joined_fill_retains_every_original_run_and_painted_corridor(self):
        pixels=np.full((100,100,3),255,np.uint8);pixels[20:80,20:80]=0
        image=Image.fromarray(pixels)
        d,report=combined.generate(image,dict(self.recipe,screening=False))
        mask=combined.ink_mask(image,self.recipe)
        raw=lettering.ink_fill_paths(mask,d)
        self.assertGreater(check_joined_fill(raw,d.paths['K'],d.pen_mm),0)
        self.assertEqual(report['recovered_paths'],len(d.paths['K']))
        self.assertEqual(report['screen_paths_by_ink'],{})

    def test_screening_only_has_no_recovered_paths_and_auto_uses_color_statistics(self):
        for color,palette in [((140,140,140),'black'),((140,100,50),'cmy')]:
            d,report=combined.generate(Image.new('RGB',(80,80),color),dict(self.recipe,black_policy='off'))
            self.assertEqual(report['recovered_paths'],0)
            self.assertEqual(report['screen_palette'],palette)
            self.assertGreater(sum(report['screen_paths_by_ink'].values()),0)

    def test_recovered_footprint_reduces_remaining_demand(self):
        rgb=np.full((20,20,3),64,np.uint8)
        empty=combined.remaining_channels(rgb,np.zeros((20,20),np.float32),'cmy')
        solid=combined.remaining_channels(rgb,np.ones((20,20),np.float32),'cmy')
        partial=combined.remaining_channels(rgb,np.full((20,20),.5,np.float32),'cmy')
        for ink in 'CMY':
            self.assertTrue(np.all(solid[ink]==0))
            self.assertTrue(np.all(partial[ink]>=0))
            self.assertTrue(np.all(partial[ink]<empty[ink]))

    def test_cmyk_gray_uses_k_shading_without_cmy(self):
        channels=combined.remaining_channels(np.full((10,10,3),100,np.uint8),np.zeros((10,10),np.float32),'cmyk')
        self.assertGreater(float(channels['K'].mean()),0)
        for ink in 'CMY':self.assertEqual(float(channels[ink].max()),0)

    def test_fullness_does_not_change_recovered_ink(self):
        pixels=np.full((100,100,3),200,np.uint8);pixels[20:80,45:55]=0
        image=Image.fromarray(pixels)
        a,ar=combined.generate(image,self.recipe)
        b,br=combined.generate(image,dict(self.recipe,interior_ink=.25,edge_fade_mm=6.35))
        self.assertEqual(a.paths['K'][:ar['recovered_paths']],b.paths['K'][:br['recovered_paths']])

    def test_turquoise_role_changes_svg_and_preview_without_recoloring_other_drawings(self):
        paths={'C':[[[1,2],[4,2]]]}
        d=engine.Drawing(5,4,.25,paths,ink_colors={'C':'#009f7c'})
        plain=engine.Drawing(5,4,.25,paths)
        a,_=engine.render(d,300);b,_=engine.render(plain,300)
        self.assertFalse(np.array_equal(a,b))
        root=ET.fromstring(engine.svg(d,'Turquoise'))
        self.assertEqual(root.find('.//{*}g[@id="ink-C"]').attrib['stroke'],'#009f7c')
        self.assertEqual(plain.ink_color('C'),'#00bcd4')
        self.assertEqual(d.pen_mm,.25)

    def test_fat_screen_pens_remain_physically_valid(self):
        # Current inventory widths must not trip the wave clearance/packet
        # constraints when used in a future actual-pen proof.
        d,report=combined.generate(Image.new('RGB',(60,60),(120,90,50)),
                                  dict(self.recipe,black_policy='off',pen_mm=.658))
        self.assertTrue(report['screen_paths_by_ink'])
        for runs in d.paths.values():
            for path in runs:
                for x,y in path:
                    self.assertTrue(.329<=x<=d.width_mm-.329)
                    self.assertTrue(.329<=y<=d.height_mm-.329)


if __name__=='__main__':unittest.main()
