"""2D hillshade must see real slopes even when the sketch grid misses them."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import shapely

from cartographer.compose import compose
from cartographer.geography import footprint
from cartographer.model import default_recipe
from cartographer.terrain_marks import grid_shade_regions


class FlatHillshadeTests(unittest.TestCase):
    def test_flat_ground_clear_and_planar_slope_uses_latitude_and_sun_direction(self):
        xs=np.linspace(-200,200,33);ys=np.linspace(-150,150,25)
        xx,yy=np.meshgrid(xs,ys);frame=shapely.box(xs[0],ys[0],xs[-1],ys[-1])
        for elevation in [5,45,85]:
            light=dict(azimuth_deg=180,elevation_deg=elevation)
            self.assertTrue(all(g.is_empty for g in grid_shade_regions(xs,ys,xx*0,light)))
        # A 0.15 Mercator slope is too shallow to shade at the equator, but
        # represents a 0.30 physical slope at latitude factor 0.5.
        light=dict(azimuth_deg=0,elevation_deg=45)
        self.assertTrue(all(g.is_empty for g in grid_shade_regions(xs,ys,.15*yy,light)))
        dim,dark=grid_shade_regions(xs,ys,.15*yy,light,latitude_factor=.5)
        self.assertAlmostEqual(dim.area,frame.area);self.assertTrue(dark.is_empty)
        self.assertTrue(all(g.is_empty for g in grid_shade_regions(xs,ys,.15*yy,dict(light,azimuth_deg=180),.5)))

    def test_sun_flip_moves_shade_and_filled_bands_preserve_holes(self):
        xs=np.linspace(-500,500,129);xx,yy=np.meshgrid(xs,xs)
        z=10000*np.exp(-(xx*xx+yy*yy)/200**2)
        light=dict(azimuth_deg=180,elevation_deg=35)
        south=grid_shade_regions(xs,xs,z,light)
        north=grid_shade_regions(xs,xs,z,dict(light,azimuth_deg=0))
        self.assertGreater(south[0].centroid.y,0);self.assertLess(north[0].centroid.y,0)
        dim,dark=grid_shade_regions(xs,xs,z,dict(light,elevation_deg=85))
        self.assertTrue(dim.is_valid and dark.is_valid)
        self.assertGreater(dark.area,1000)
        self.assertLess(dark.difference(dim).area,1e-8)
        self.assertFalse(dim.covers(shapely.Point(0,0)),'Flat summit must remain an unshaded hole')
        self.assertGreater(sum(len(p.interiors) for p in shapely.get_parts(dim)),0)

    def test_flat_composition_samples_detail_without_contours_and_reuses_it_for_sun_edits(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=18000
        r['features'].update(contours=False,hillshade=True,parks=False,roads='none',
                             buildings='none',shore='none',labels='none')
        x0,y0,x1,y1=footprint(r).bounds
        xs=np.linspace(x0,x1,65);ys=np.linspace(y0,y1,65);xx,yy=np.meshgrid(xs,ys)
        z=700*np.exp(-((xx+250)**2+(yy-200)**2)/400000)
        coarse=(np.linspace(x0,x1,3),np.linspace(y0,y1,3),np.zeros((3,3)),dict(grid=[3,3],tiles=[]))
        detail=(xs,ys,z,dict(sampling={'grid':[65,65]},tiles=[]))
        data=dict(source={'attribution':'Fixture'},issues=[],areas=[],roads=[],labels=[])
        with tempfile.TemporaryDirectory() as temp,patch('cartographer.compose.dataset',return_value=data), \
             patch('cartographer.compose.elevations',return_value=coarse), \
             patch('cartographer.compose.flat_elevations',return_value=detail) as sampler, \
             patch('cartographer.compose.adaptive_mesh',side_effect=AssertionError('Flat shading built a relief mesh')):
            root=Path(temp)
            def render(name):return compose(r,root/name,'draft',root/'cache',version='flat-shade-fixture')
            first=render('first')
            self.assertGreater(first['feature_counts']['terrain/hillshade'],0)
            scene=json.loads((root/'first/scene.json').read_text())
            self.assertEqual(len(scene['terrain']['z']),3)
            self.assertFalse((root/'first/scene-detail.json').exists())
            meta=json.loads((root/'first/provenance.json').read_text())['inputs']['elevation']
            self.assertEqual(meta['hillshade']['sampling']['grid'],[65,65]);self.assertNotIn('contours',meta)
            r['camera']['exaggeration']=4
            second=render('exaggerated')
            self.assertEqual(first['source_sha256'],second['source_sha256'])
            r['lighting']['azimuth_deg']=0
            third=render('sun-moved')
            self.assertNotEqual(first['source_sha256'],third['source_sha256'])
            rebuilt={n['node'] for n in third['build_cache']['nodes'] if not n['reused']}
            self.assertEqual(rebuilt,{'Slope illumination','Hillshade paint'})
            r['features']['contours']=True;r['features']['contour_interval_m']=100
            fourth=render('contours-too')
            self.assertGreater(fourth['feature_counts']['terrain/contours'],0)
            self.assertEqual(sampler.call_count,1)


if __name__=='__main__':unittest.main()
