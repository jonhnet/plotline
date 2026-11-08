import io
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import shapely

from cartographer.model import default_recipe,MM,layout
from cartographer.geography import framing,projection,WORLD,HALF
from cartographer.terrain import adaptive_mesh,MeshTerrain,ElevationSampler
from cartographer.sources import cached
from terrain_geometry import Terrain,stroke_clip
from build_map import parts


def grid_mesh(xs,ys,z):
    xx,yy=np.meshgrid(xs,ys);vertices=np.stack([xx,yy,z],axis=-1).reshape(-1,3)
    ids=[];nx=len(xs)
    for j in range(len(ys)-1):
        for i in range(nx-1):
            a=j*nx+i;ids.extend([(a,a+1,a+nx+1),(a,a+nx+1,a+nx)])
    return dict(vertices=vertices,indices=np.array(ids),center_height_m=float(z[len(ys)//2,len(xs)//2]),stats={})


class AdaptiveTerrainTests(unittest.TestCase):
    def test_geometry_cache_miss_and_hit_have_identical_precision_semantics(self):
        area=shapely.set_precision(shapely.box(.03,.07,1.04,1.06),.1)
        with tempfile.TemporaryDirectory() as directory:
            first=cached(directory,'terrain-area',lambda:area)
            again=cached(directory,'terrain-area',lambda:self.fail('Cache hit rebuilt geometry'))
            self.assertEqual(shapely.get_precision(first),shapely.get_precision(again))
            crop=shapely.box(.03,.03,.54,.54)
            self.assertEqual(first.intersection(crop).wkb,again.intersection(crop).wkb)

    def test_general_mesh_preserves_depth_and_constant_width_across_view_quadrants(self):
        r=default_recipe();r['center']=[0,0];r['camera'].update(relief=True,elevation_deg=22,exaggeration=3)
        r['framing']['scale_denominator']=25000
        xs=np.linspace(-6000,6000,9);ys=np.linspace(-6000,6000,9)
        rng=np.random.default_rng(161);z=rng.uniform(0,800,(9,9));mesh=grid_mesh(xs,ys,z)
        frame=shapely.box(*(np.array(layout(r)['frame_mm'])*MM))
        path=shapely.LineString([(-5000,-2000),(0,1000),(5000,2000)])
        for azimuth in [0,35,90,135,215,296]:
            r['camera']['azimuth_deg']=azimuth
            actual=MeshTerrain(mesh,r)
            s,o=framing(r,mesh['center_height_m']);vv=mesh['vertices'].copy();vv[:,2]*=3
            raw=vv@projection(r).T*s+o
            expected=Terrain(xs,ys,z,3,(azimuth+180)%360,22,[*raw.min(axis=0),*raw.max(axis=0)])
            self.assertLess(actual.audit_visibility(1000)['maximum_frontmost_depth_error_m'],1e-3)
            for shape in [path,shapely.LineString([(-6000,0),(6000,0)])]:
                a=actual.drape_centerlines(shape,'road');b=expected.drape_centerlines(shape,'road')
                def ink(lines):return shapely.union_all([g.buffer(.325).intersection(stroke_clip(g,.65)) for g in parts(lines,'LineString')]).intersection(frame)
                aa,bb=ink(a),ink(b)
                # The legacy engine snaps overlay intersections at 1e-7 pt;
                # grazing facets amplify that rounding at run endpoints.
                # 0.001 pt remains >70 times finer than the HPGL step.
                self.assertLess(aa.difference(bb.buffer(.001)).area,1e-5)
                self.assertLess(bb.difference(aa.buffer(.001)).area,1e-5)
                self.assertLess(aa.symmetric_difference(bb).area,.01)

    def test_area_draping_keeps_holes_and_handles_mesh_boundary_contacts(self):
        r=default_recipe();r['center']=[0,0];r['camera'].update(relief=True,elevation_deg=45,azimuth_deg=33)
        r['framing']['scale_denominator']=20000
        xs=np.linspace(-6000,6000,9);z=np.zeros((9,9));t=MeshTerrain(grid_mesh(xs,xs,z),r)
        park=shapely.box(-5000,-5000,5000,5000).difference(shapely.box(-800,-800,800,800))
        area=t.drape_area(park);hole=t.drape_area(shapely.box(-800,-800,800,800))
        self.assertTrue(area.is_valid);self.assertLess(area.intersection(hole).area,1e-4)
        self.assertGreater(area.area,100)

    def test_adaptive_edges_are_conforming_and_source_detail_is_not_diluted_by_coverage(self):
        r=default_recipe();r['center']=[0,0];r['camera'].update(relief=True,azimuth_deg=33,elevation_deg=30,exaggeration=2)
        r['framing']['scale_denominator']=20000
        def height(p,zoom):return 90*np.sin(p[:,0]/800)*np.cos(p[:,1]/600)
        meshes=[]
        for radius in [20000,80000]:
            xs=np.linspace(-radius,radius,17);xx,yy=np.meshgrid(xs,xs)
            z=height(np.column_stack([xx.ravel(),yy.ravel()]),0).reshape(xx.shape)
            mesh=adaptive_mesh(r,shapely.box(-radius,-radius,radius,radius),(xs,xs,z,{}),height)
            meshes.append(mesh)
            vertices,triangles=mesh['vertices'],mesh['indices']
            edges=Counter(tuple(sorted((int(a),int(b)))) for ids in triangles for a,b in zip(ids,np.roll(ids,-1)))
            self.assertTrue(all(n in [1,2] for n in edges.values()))
            for (a,b),count in edges.items():
                if count==1:
                    va,vb=vertices[a,:2],vertices[b,:2]
                    self.assertTrue(any(va[k]==vb[k] and abs(va[k])==radius for k in [0,1]),'Interior T-junction or crack')
            self.assertAlmostEqual(float(shapely.area(shapely.polygons(vertices[triangles][:,:,:2])).sum()),4*radius**2,places=4)
            self.assertLess(mesh['stats']['active_cell_median_m'],1000)
        self.assertEqual(meshes[0]['stats']['source_zoom_target'],meshes[1]['stats']['source_zoom_target'])
        self.assertLess(meshes[1]['stats']['source_pixel_m'],25)

    def test_detail_sampler_interpolates_tile_edges_and_reuses_loaded_pixels(self):
        r=default_recipe();r['center']=[0,0]
        def fetch(url,*args):
            z,x,y=map(int,url.removesuffix('.png').rsplit('/',3)[-3:]);xx,yy=np.meshgrid(np.arange(256)+x*256,np.arange(256)+y*256)
            height=2*xx+3*yy+32768
            rgb=np.stack([height//256,height%256,np.zeros_like(height)],axis=-1).astype(np.uint8)
            raw=io.BytesIO();Image.fromarray(rgb).save(raw,format='PNG')
            return raw.getvalue(),dict(url=url,cache_key=url,sha256='fixture')
        p=np.array([[-10.,-20.],[0.,0.],[10.,20.]])
        with tempfile.TemporaryDirectory() as d,patch('cartographer.sources.fetch',side_effect=fetch) as request:
            sampler=ElevationSampler(r,Path(d),lambda *args:None,20)
            got=sampler.sample(p,2);count=request.call_count
            np.testing.assert_allclose(got,sampler.sample(p,2));self.assertEqual(request.call_count,count)
            pixel=WORLD/(256*4);expected=2*((p[:,0]+HALF)/pixel-.5)+3*((HALF-p[:,1])/pixel-.5)
            np.testing.assert_allclose(got,expected,atol=1e-9)
