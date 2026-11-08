import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

from raster_lab import engine as e
from raster_lab.waves import integrate_phase, modulation, tone_lookup, wave_paths


class WaveTests(unittest.TestCase):
    def setUp(self):
        self.recipes=[r for r in json.loads(Path('raster_lab/experiments.json').read_text()) if r['method']=='wave']

    def test_phase_is_integrated_continuously_across_frequency_changes(self):
        f=np.r_[np.ones(30)*.1,np.ones(30)*1.2]
        p=integrate_phase(f,.05,initial=.8)
        np.testing.assert_allclose(np.diff(p),2*np.pi*.05*(f[1:]+f[:-1])/2)
        self.assertEqual(p[0],.8)
        self.assertLess(np.max(np.diff(p)),.38)

    def test_modes_vary_the_intended_quantity_and_have_monotone_lookup(self):
        q=np.linspace(0,1,8)
        for mode in ['am','fm','amfm']:
            a,f=modulation(q,mode,.42,.08,1.25)
            self.assertTrue(np.all(np.diff(a)>=0));self.assertTrue(np.all(np.diff(f)>=0))
            if mode=='am':self.assertEqual(len(set(f)),1)
            elif mode=='fm':self.assertEqual(len(set(a)),1)
            else:self.assertGreater(a[-1],a[0]);self.assertGreater(f[-1],f[0])
            ratios,_=tone_lookup(mode,.42,.08,1.25,'sine')
            self.assertTrue(np.all(np.diff(ratios)>=0))
            self.assertGreater(ratios[-1],2)

    def test_white_is_blank_and_black_patch_stays_in_bounds(self):
        for recipe in self.recipes:
            small=dict(recipe,long_edge_in=1)
            white=e.draw(Image.new('RGB',(50,50),'white'),small)
            self.assertEqual(e.metrics(white)['paths'],0)
            black=e.draw(Image.new('RGB',(50,50),'black'),small)
            for paths in black.paths.values():
                for path in paths:
                    for x,y in path:
                        self.assertTrue(.15<=x<=black.width_mm-.15)
                        self.assertTrue(.15<=y<=black.height_mm-.15)

    def test_darker_constant_patches_add_mileage_without_fragmenting_rows(self):
        d=e.Drawing(25,25,.3,{})
        for recipe in self.recipes[:3]:
            lengths=[]
            for tone in [.4,.65,.9]:
                paths=wave_paths(np.full((64,64),tone,np.float32),d,0,recipe)
                lengths.append(sum(e.path_length(p) for p in paths))
                self.assertLess(len(paths),60)
                self.assertGreater(max(map(e.path_length,paths)),20)
            self.assertTrue(all(a<b for a,b in zip(lengths,lengths[1:])),(recipe['id'],lengths))

    def test_more_actual_coverage_at_darker_tones(self):
        recipe=dict(self.recipes[2],long_edge_in=1)
        coverages=[]
        for gray in [150,95,30]:
            image=Image.new('RGB',(64,64),(gray,gray,gray))
            drawing=e.draw(image,recipe)
            _,coverage=e.render(drawing);coverages.append(coverage)
        self.assertTrue(all(a<b for a,b in zip(coverages,coverages[1:])),coverages)

    def test_wave_drawing_is_deterministic(self):
        ramp=np.tile(np.arange(64,dtype=np.uint8)*4,(64,1))
        image=Image.fromarray(np.repeat(ramp[:,:,None],3,axis=2))
        recipe=dict(self.recipes[-1],long_edge_in=1)
        self.assertEqual(e.draw(image,recipe),e.draw(image,recipe))

    def test_unresolvable_frequency_and_row_overlap_are_rejected(self):
        d=e.Drawing(25,25,.3,{})
        for changes in [dict(max_frequency_cpm=10),dict(amplitude_mm=1)]:
            with self.assertRaises(ValueError):
                wave_paths(np.ones((10,10),np.float32),d,0,dict(self.recipes[0],**changes))

    def test_zero_budget_does_not_leave_carrier_lines(self):
        d=e.Drawing(25,25,.3,{})
        self.assertEqual(wave_paths(np.ones((10,10),np.float32),d,0,dict(self.recipes[0],density=0)),[])


if __name__=='__main__':unittest.main()
