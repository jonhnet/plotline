import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf
import shapely

from cartographer.compose import compose
from cartographer.geography import footprint
from cartographer.model import default_recipe,migrate,validate_recipe,MM,layout
from cartographer.terrain import MeshTerrain
from cartographer.terrain_marks import silhouette_lines,shade_regions,vertex_illumination,hatch_rows
from test_cartographer_terrain import grid_mesh


class TerrainMarksTests(unittest.TestCase):
    def test_old_recipe_defaults_and_validation(self):
        r=default_recipe();r.pop('lighting')
        for f in [r['features'],r['outside']['features']]:
            for key in ['park_labels','terrain_edges','hillshade','hillshade_spacing_mm']:f.pop(key)
        for roles in [r['ink_roles'],r['outside']['ink_roles']]:
            for key in ['silhouettes','hillshade']:roles.pop(key)
        before=copy.deepcopy(r);new=migrate(r);validate_recipe(r)
        self.assertEqual(r,before)
        self.assertEqual(new['ink_roles']['hillshade'],'black');self.assertTrue(new['features']['park_labels']);self.assertFalse(new['features']['hillshade'])
        self.assertEqual(new['lighting'],dict(azimuth_deg=180,elevation_deg=45))
        for key,value in [('park_labels','yes'),('terrain_edges',1),('hillshade_spacing_mm',0)]:
            bad=copy.deepcopy(new);bad['features'][key]=value
            with self.assertRaises(ValueError):validate_recipe(bad)
        new['lighting']['elevation_deg']=0
        with self.assertRaises(ValueError):validate_recipe(new)

    def test_pyramid_draws_only_two_visible_silhouette_edges_without_fetch_border(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=25000
        r['camera'].update(relief=True,exaggeration=1,elevation_deg=20,azimuth_deg=0)
        vertices=np.array([[-2000,-2000,0],[2000,-2000,0],[2000,2000,0],[-2000,2000,0],[0,0,2000]],dtype=float)
        indices=np.array([[4,0,1],[4,1,2],[4,2,3],[4,3,0]])
        mesh=dict(vertices=vertices,indices=indices,center_height_m=1000,stats={})
        for az,ends in [(0,[2,3]),(90,[1,2]),(180,[0,1]),(270,[0,3])]:
            r['camera']['azimuth_deg']=az;t=MeshTerrain(mesh,r)
            actual=shapely.union_all(silhouette_lines(t))
            p=t.vertices@t.projection.T*t.scale+t.offset
            expected=shapely.LineString([p[ends[0]],p[4],p[ends[1]]])
            self.assertLess(actual.hausdorff_distance(expected),1e-4)
            self.assertAlmostEqual(actual.length,expected.length,places=3)
        mesh['vertices'][:,2]=0
        self.assertEqual(silhouette_lines(MeshTerrain(mesh,r)),[])

    def test_hidden_back_mountain_silhouette_is_removed(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=65000
        r['camera'].update(relief=True,exaggeration=1,elevation_deg=15,azimuth_deg=0)
        xs=np.linspace(-7000,7000,41);xx,yy=np.meshgrid(xs,xs)
        back=np.maximum(0,1600-np.hypot(xx,yy-2800))
        front=np.maximum(0,4000-np.hypot(xx,yy+2800))
        t=MeshTerrain(grid_mesh(xs,xs,np.maximum(front,back)),r)
        lines=shapely.union_all(silhouette_lines(t));self.assertGreater(lines.length,10)
        # The back peak is hidden, so neither contours nor a silhouette should
        # expose it. The returned point is the mesh's exact projected location.
        p,visible=t.locate(0,2800);self.assertFalse(visible)
        self.assertGreater(lines.distance(shapely.Point(p)),5)

    def test_sun_flip_moves_shading_and_flat_ground_stays_clear(self):
        xs=np.linspace(-2000,2000,25);xx,yy=np.meshgrid(xs,xs)
        z=1400*np.exp(-(xx**2+yy**2)/1000000)
        mesh=grid_mesh(xs,xs,z);light=dict(azimuth_deg=180,elevation_deg=35)
        south=shade_regions(mesh['vertices'],mesh['indices'],light)
        north=shade_regions(mesh['vertices'],mesh['indices'],dict(light,azimuth_deg=0))
        self.assertGreater(south[0].centroid.y,300);self.assertLess(north[0].centroid.y,-300)
        self.assertGreater(south[1].area,1000);self.assertLess(south[1].difference(south[0]).area,.01)
        self.assertAlmostEqual(south[0].area,north[0].area,delta=.01)
        # Mercator latitude correction and visual exaggeration scale the normal
        # in exactly the same way, independently of camera heading.
        np.testing.assert_allclose(vertex_illumination(mesh['vertices'],mesh['indices'],light,.5,1),
                                   vertex_illumination(mesh['vertices'],mesh['indices'],light,1,2))
        mesh['vertices'][:,2]=0
        for el in [5,45,85]:
            self.assertTrue(all(g.is_empty for g in shade_regions(mesh['vertices'],mesh['indices'],dict(light,elevation_deg=el))))

    def test_shade_culling_preserves_normals_at_crop_edges_and_holes(self):
        xs=np.linspace(-2000,2000,25);xx,yy=np.meshgrid(xs,xs)
        mesh=grid_mesh(xs,xs,700*np.sin(xx/900)*np.cos(yy/600))
        light=dict(azimuth_deg=240,elevation_deg=35)
        domain=shapely.box(-300,-500,700,900).difference(shapely.box(0,0,200,200))
        facets=shapely.polygons(mesh['vertices'][mesh['indices']][:,:,:2])
        ids=shapely.STRtree(facets).query(domain,predicate='intersects')
        self.assertLess(len(ids),len(facets)/2)
        whole=shade_regions(mesh['vertices'],mesh['indices'],light)
        cropped=shade_regions(mesh['vertices'],mesh['indices'],light,triangle_ids=np.sort(ids))
        for a,b in zip(whole,cropped):
            a=a.intersection(domain);b=b.intersection(domain)
            # Region unions snap to a 1e-5 ground-unit grid; regrouping faces
            # may move an interpolated boundary within that same grid cell.
            self.assertLess(a.difference(b.buffer(1e-5)).area,1e-6)
            self.assertLess(b.difference(a.buffer(1e-5)).area,1e-6)

    def test_hatches_keep_continuous_rows_at_crossings(self):
        area=shapely.box(0,0,10,10)
        rows=list(hatch_rows(area,1,0))+list(hatch_rows(area,1,90))
        self.assertEqual(len(rows),22)
        self.assertTrue(all(abs(g.length-10)<1e-8 for g in rows))

    def test_hatch_stripes_keep_global_rows_and_do_not_add_pen_lifts(self):
        # Many holes force local stripes; several lie across stripe boundaries.
        area=shapely.box(-100,-80,120,160).difference(shapely.union_all([
            shapely.Point(x,y).buffer(2.7,quad_segs=8) for x in range(-90,110,13) for y in range(-70,150,17)]))
        for angle in [-45,0,37,90]:
            spacing=1.7;g=shapely.affinity.rotate(area,-angle,origin=(0,0));x0,y0,x1,y1=g.bounds
            expected=[]
            for y in np.arange(math.floor(y0/spacing)*spacing,y1+spacing,spacing):
                row=shapely.LineString([(x0,y),(x1,y)]).intersection(g)
                if not row.is_empty:expected.append(shapely.affinity.rotate(row,angle,origin=(0,0)))
            actual=list(hatch_rows(area,spacing,angle))
            self.assertEqual(len(actual),len(expected))
            np.testing.assert_array_equal(shapely.get_num_geometries(actual),shapely.get_num_geometries(expected))
            self.assertLess(float(np.max(shapely.hausdorff_distance(actual,expected))),1e-9)
            np.testing.assert_allclose(shapely.length(actual),shapely.length(expected),atol=1e-9,rtol=0)

    def test_forest_labels_independent_and_terrain_marks_remain_vector_bound_and_clipped(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=18000
        r['features'].update(roads='none',buildings='none',shore='none',hatch_spacing_mm=6,contours=False)
        r['camera'].update(exaggeration=2,elevation_deg=30)
        def height(p,zoom):
            p=np.asarray(p);return 700*np.exp(-((p[:,0]+250)**2+(p[:,1]-200)**2)/400000)
        def elevation(recipe,domain,*args,**kwargs):
            x0,y0,x1,y1=domain.bounds;xs=np.linspace(x0,x1,17);ys=np.linspace(y0,y1,17);xx,yy=np.meshgrid(xs,ys)
            return xs,ys,height(np.column_stack([xx.ravel(),yy.ravel()]),0).reshape(xx.shape),{'tiles':[]}
        def data(recipe,*args,**kwargs):
            return dict(source={'attribution':'Fixture'},issues=[],roads=[],
                        labels=[dict(name='Town',kind='place',rank=1,geometry=shapely.Point(-900,-900))],
                        areas=[dict(kind='parks',id='forest',name='Forest label',geometry=footprint(recipe))])
        with tempfile.TemporaryDirectory() as d,patch('cartographer.compose.dataset',side_effect=data),patch('cartographer.compose.elevations',side_effect=elevation),patch('cartographer.compose.flat_elevations',side_effect=elevation),patch('cartographer.compose.ElevationSampler') as sampler:
            root=Path(d);sampler.return_value.sample.side_effect=height;sampler.return_value.inputs={}
            def render(name):
                out=root/name;result=compose(r,out,quality='draft',cache=root/'cache',version='terrain-marks-fixture')
                with pymupdf.open(out/'map.pdf') as doc:
                    self.assertFalse(doc[0].get_images());text=doc[0].get_text()
                    pix=doc[0].get_pixmap();drawings=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
                intent=json.loads((out/'map.print-intent.json').read_text())
                return result,text,drawings,intent
            _,text,_,_=render('labels-on');self.assertIn('Forest label',text);self.assertIn('Town',text)
            r['features']['park_labels']=False
            result,text,_,_=render('labels-off');self.assertNotIn('Forest label',text);self.assertIn('Town',text)
            self.assertGreater(result['feature_counts']['parks/explicit_hatch'],0)
            r['features'].update(parks=False,hillshade=True,terrain_edges=True,labels='none')
            r['ink_roles']['terrain']='red'
            for relief in [False,True]:
                r['camera']['relief']=relief
                result,_,drawings,intent=render('shading-'+str(relief))
                self.assertGreater(result['feature_counts']['terrain/hillshade'],0)
                self.assertEqual(bool(result['feature_counts'].get('terrain/silhouette')),relief)
                bindings=[b for b in intent['bindings'] if b['feature_group'].startswith('terrain/')]
                self.assertTrue(bindings);self.assertTrue(all(b['ink_id']=='black' for b in bindings))
                x0,y0,x1,y1=np.array(layout(r)['frame_mm'])*MM
                outside=np.ones(drawings.shape[:2],dtype=bool)
                outside[int(y0)-1:int(y1)+2,int(x0)-1:int(x1)+2]=False
                self.assertTrue(np.all(drawings[outside]==255),'Terrain marks escaped the printable viewport')
