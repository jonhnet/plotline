import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from raster_lab import engine as e, lettering


class ThinningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipes={r['id']:r for r in json.loads(Path('raster_lab/experiments.json').read_text())}

    def test_every_comparison_uses_identical_mask_scale_and_pen(self):
        rgb=np.full((100,100,3),255,np.uint8)
        cv2.putText(rgb,'Eo',(8,65),cv2.FONT_HERSHEY_SIMPLEX,1.5,(0,0,0),3)
        image=Image.fromarray(rgb)
        masks=[]
        for id in ['thin-reference','thin-guo','thin-medial','thin-fill']:
            recipe=self.recipes[id]
            d=e.Drawing(20,20,recipe['pen_mm'],{})
            masks.append(lettering.component_mask(lettering.analysis_gray(image,recipe),d,recipe))
            self.assertEqual(recipe['pen_mm'],.075)
            self.assertEqual(recipe['preview_dpi'],450)
        for mask in masks[1:]:np.testing.assert_array_equal(mask,masks[0])

    def test_guo_hall_is_not_followed_by_zhang_suen(self):
        image=Image.new('RGB',(60,60),'white')
        for y in range(10,50):
            for x in range(28,32):image.putpixel((x,y),(0,0,0))
        with patch('raster_lab.engine.cv2.ximgproc.thinning', wraps=cv2.ximgproc.thinning) as thinning:
            d=e.draw(image,dict(self.recipes['thin-guo'],long_edge_in=12/25.4))
        self.assertTrue(d.paths['K'])
        self.assertEqual(thinning.call_count,1)
        self.assertEqual(thinning.call_args.kwargs['thinningType'],cv2.ximgproc.THINNING_GUOHALL)

    def test_medial_axis_is_reproducible_and_does_not_call_pixel_thinning(self):
        mask=np.zeros((80,80),np.uint8)
        cv2.rectangle(mask,(10,10),(60,60),1,5)
        mask[70:72,70:72]=1
        image=Image.fromarray((1-mask)*255).convert('RGB')
        recipe=dict(self.recipes['thin-medial'],long_edge_in=16/25.4)
        with patch('raster_lab.engine.cv2.ximgproc.thinning',side_effect=AssertionError('Unexpected re-thinning')):
            a=e.draw(image,recipe);b=e.draw(image,recipe)
        self.assertEqual(a,b)
        self.assertTrue(a.paths['K'])
        paint=e.stroke_mask(a,'K',800,800,1)
        self.assertEqual(lettering.topology(paint>64),dict(components=2,holes=1))

    def test_fill_diagnostics_do_not_claim_a_skeleton(self):
        image=Image.new('RGB',(20,20),'white')
        recipe=self.recipes['thin-fill']
        drawing=e.draw(image,recipe)
        with tempfile.TemporaryDirectory() as tmp:
            result=lettering.diagnostics(image,drawing,recipe,Path(tmp))
            self.assertFalse(result['skeleton_used'])
            self.assertIsNone(result['thinning_method'])
            self.assertTrue((Path(tmp)/'threshold-mask.png').is_file())
            self.assertFalse((Path(tmp)/'accepted-skeleton.png').exists())
            self.assertNotIn('skeleton_topology',result['regions']['whole_image'])

    def test_each_method_preserves_isolated_marks_loops_and_crossings(self):
        mask=np.zeros((100,100),np.uint8)
        mask[5:7,5:7]=1
        cv2.rectangle(mask,(15,15),(40,40),1,3)
        mask[60:90,73:77]=1;mask[73:77,60:90]=1
        d=e.Drawing(20,20,.075,{})
        for id in ['thin-reference','thin-guo','thin-medial']:
            recipe=self.recipes[id]
            thin=lettering.narrow_skeleton(mask>0,d,recipe)
            d.paths['K']=lettering.component_paths(thin,d,already_thin=recipe.get('trace_skeleton_directly',False))
            paint=e.stroke_mask(d,'K',1000,1000,1)
            self.assertEqual(lettering.topology(paint>64),dict(components=3,holes=1),id)

    def test_fill_preserves_shape_weight_better_than_a_uniform_skeleton(self):
        mask=np.zeros((100,100),np.uint8)
        mask[20:80,20:25]=1
        mask[20:25,20:60]=1;mask[48:53,20:50]=1;mask[75:80,20:60]=1
        d=e.Drawing(10,10,.075,{})
        thin=lettering.narrow_skeleton(mask>0,d,self.recipes['thin-reference'])
        d.paths['K']=lettering.component_paths(thin,d)
        skeleton=e.stroke_mask(d,'K',1000,1000,2)>127
        d.paths['K']=lettering.ink_fill_paths(mask>0,d)
        filled=e.stroke_mask(d,'K',1000,1000,2)>127
        target=cv2.resize(mask,(1000,1000),interpolation=cv2.INTER_NEAREST)>0
        self.assertGreater(np.sum(filled & target)/target.sum(),.94)
        self.assertLess(np.sum(filled ^ target),np.sum(skeleton ^ target)/5)
        self.assertEqual(np.sum(filled & ~target),0)

    def test_fill_pen_boxes_fit_ink_and_keep_holes_and_single_pixel_marks(self):
        mask=np.zeros((100,100),bool)
        mask[5:95,5:95]=True;mask[25:75,25:75]=False
        mask[0,0]=True;mask[50,50]=True
        d=e.Drawing(10,10,.075,{})
        paths=lettering.ink_fill_paths(mask,d)
        self.assertTrue(paths)
        for path in paths:
            (x0,y),(x1,y1)=path
            self.assertEqual(y,y1)
            r=d.pen_mm/2
            lo=int(np.floor((y-r)/.1));hi=int(np.floor((y+r)/.1))
            left=int(np.floor((x0-r)/.1));right=int(np.floor((x1+r)/.1))
            self.assertTrue(mask[lo:hi+1,left:right+1].all())
        d.paths['K']=paths
        paint=e.stroke_mask(d,'K',1000,1000,1)
        self.assertGreater(paint[505,505],0)
        self.assertEqual(paint[450,450],0)
        self.assertTrue(all(0.0375 <= v <= 9.9625 for p in paths for point in p for v in point))

    def test_tracing_a_supplied_skeleton_does_not_delete_its_pixels(self):
        mask=np.zeros((10,10),bool)
        mask[4:6,4:6]=True
        with patch('raster_lab.engine.cv2.ximgproc.thinning',side_effect=AssertionError('Unexpected re-thinning')):
            paths=e.skeleton_paths(mask,include_points=True,already_thin=True)
        self.assertEqual({v for p in paths for v in p},set(zip(*np.nonzero(mask)[::-1])))


if __name__=='__main__':unittest.main()
