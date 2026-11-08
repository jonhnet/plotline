"""Flat contours retain paper-scale terrain detail without enlarging the sketch."""
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import shapely

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cartographer.contours import contours,elevation_grid,pixel_axes,sampling_plan
from cartographer.compose import compose
from cartographer.geography import footprint
from cartographer.model import default_recipe


class FlatContourTests(unittest.TestCase):
    def recipe(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=100000
        r['features'].update(contours=True,parks=False,roads='none',buildings='none',labels='none')
        return r

    def test_sampling_tracks_paper_scale_and_obeys_budgets(self):
        r=self.recipe();domain=shapely.box(-20000,-15000,20000,15000)
        draft=sampling_plan(r,domain,'draft');proof=sampling_plan(r,domain,'proof')
        self.assertLessEqual(max(draft['spacing_mm']),.5)
        self.assertLessEqual(max(proof['spacing_mm']),.2)
        self.assertGreater(proof['grid'][0],draft['grid'][0])
        larger=sampling_plan(r,shapely.box(-40000,-30000,40000,30000),'draft')
        self.assertGreater(larger['grid'][0],draft['grid'][0]*1.9)
        for q in ['draft','proof']:
            bounded=sampling_plan(r,shapely.box(-1e6,-.8e6,1e6,.8e6),q)
            self.assertLessEqual(math.prod(bounded['grid']),bounded['max_points'])
            self.assertLessEqual(bounded['tile_count'],bounded['max_tiles'])
            self.assertTrue(bounded['limited'])

    def test_new_sampling_retains_peak_that_falls_between_old_grid_points(self):
        r=self.recipe();domain=shapely.box(-20000,-20000,20000,20000)
        def heights(xs,ys):
            xx,yy=np.meshgrid(xs,ys)
            return 500*np.exp(-((xx-625)**2+(yy-625)**2)/200**2)
        coarse=np.linspace(-20000,20000,33)
        self.assertLess(heights(coarse,coarse).max(),1.)
        plan=sampling_plan(r,domain,'draft')
        xs=np.linspace(-20000,20000,plan['grid'][0]);ys=np.linspace(-20000,20000,plan['grid'][1])
        lines=dict(contours(xs,ys,heights(xs,ys),100))[100.]
        self.assertEqual(len(lines.geoms),1)
        self.assertTrue(lines.geoms[0].is_ring)
        expected=shapely.Point(625,625).buffer(200*math.sqrt(math.log(5)),quad_segs=256).boundary
        self.assertLess(lines.hausdorff_distance(expected),10.)

    def test_real_tile_interpolation_preserves_linear_elevations_across_seams(self):
        r=self.recipe();r['framing']['scale_denominator']=20000
        domain=shapely.box(-1000,-1000,1000,1000);calls=[]
        def fetch(url,cache):
            zoom,x,y=map(int,url.removesuffix('.png').rsplit('/',3)[-3:]);calls.append((zoom,x,y))
            xx,yy=np.meshgrid(np.arange(256)+x*256,np.arange(256)+y*256)
            height=(xx+2*yy-3*128*2**zoom)/256+120.5
            encoded=np.round((height+32768)*256).astype(np.int64)
            rgb=np.stack([encoded>>16,(encoded>>8)%256,encoded%256],axis=-1).astype(np.uint8)
            raw=io.BytesIO();Image.fromarray(rgb).save(raw,format='PNG')
            return raw.getvalue(),dict(url=url,cache_key=url)
        with tempfile.TemporaryDirectory() as temp,patch('cartographer.contours.fetch',side_effect=fetch):
            xs,ys,z,meta=elevation_grid(r,domain,Path(temp),'draft')
        zoom=meta['sampling']['zoom'];ix,iy,u,v=pixel_axes(r,xs,ys,zoom)
        expected=(ix[None,:]+u[None,:]+2*(iy[:,None]+v[:,None])-3*128*2**zoom)/256+120.5
        np.testing.assert_allclose(z,expected,atol=1e-9)
        self.assertEqual((xs[0],ys[0],xs[-1],ys[-1]),domain.bounds)
        self.assertGreaterEqual(len(calls),4)
        self.assertEqual(len(calls),len(set(calls)))

    def test_regular_grid_contours_follow_exact_plane_and_keep_bounds(self):
        xs=np.linspace(-5,5,31);ys=np.linspace(-4,4,25)
        xx,yy=np.meshgrid(xs,ys)
        for level,lines in contours(xs,ys,xx+2*yy,2):
            xy=shapely.get_coordinates(lines)
            if not len(xy):continue
            np.testing.assert_allclose(xy[:,0]+2*xy[:,1],level,atol=1e-12)
            self.assertTrue(shapely.box(-5,-4,5,4).covers(lines))
        with self.assertRaisesRegex(ValueError,'400'):
            list(contours(xs,ys,xx+2*yy,.01))

    def test_composition_uses_detailed_contours_but_keeps_small_sketch_and_cache(self):
        r=self.recipe();domain=footprint(r)
        x0,y0,x1,y1=domain.bounds
        xs=np.linspace(x0,x1,65);ys=np.linspace(y0,y1,65);xx,yy=np.meshgrid(xs,ys)
        z=500*np.exp(-((xx/(x1*.4))**2+(yy/(y1*.4))**2))
        coarse=(np.linspace(x0,x1,3),np.linspace(y0,y1,3),np.zeros((3,3)),dict(grid=[3,3],tiles=[]))
        detail=(xs,ys,z,dict(sampling={'grid':[65,65]},tiles=[]))
        data=dict(source={'attribution':'Fixture'},issues=[],areas=[],roads=[],labels=[])
        with tempfile.TemporaryDirectory() as temp,patch('cartographer.compose.dataset',return_value=data), \
             patch('cartographer.compose.elevations',return_value=coarse), \
             patch('cartographer.compose.flat_elevations',return_value=detail) as sampler:
            root=Path(temp)
            first=compose(r,root/'first','draft',root/'cache',version='contour-fixture')
            second=compose(r,root/'second','draft',root/'cache',version='contour-fixture')
            self.assertEqual(sampler.call_count,1)
            self.assertGreater(first['feature_counts']['terrain/contours'],0)
            self.assertEqual(first['source_sha256'],second['source_sha256'])
            scene=json.loads((root/'first/scene.json').read_text())
            self.assertEqual(len(scene['terrain']['z']),3)
            meta=json.loads((root/'first/provenance.json').read_text())['inputs']['elevation']
            self.assertEqual(meta['grid'],[3,3])
            self.assertEqual(meta['contours']['sampling']['grid'],[65,65])


if __name__=='__main__':unittest.main()
