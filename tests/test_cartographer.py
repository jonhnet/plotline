"""Map authoring boundary, revision races, vector exports and print-client contract."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pymupdf
import shapely
from shapely.geometry import LineString, box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cartographer.client import PrintClient
from cartographer.compose import compose, fit_recipe, framing, projection, valid_area
from cartographer.jobs import Jobs
from cartographer.model import default_recipe, digest, layout, validate_recipe, media_profiles
from cartographer.sources import elevations, plan, dataset, fetch
from cartographer.geography import footprint, origin, geo_geometry, point, lonlat
from cartographer.model import migrate
from cartographer.storage import Conflict, Recipes, atomic_json
from print_contract import capabilities, validate_intent
from pdf_native import extract


def fixture_data(recipe):
    x0, y0, x1, y1 = footprint(recipe).bounds
    dx, dy = x1-x0, y1-y0
    return dict(source={'attribution':'Test data'}, issues=[], city=None,
                areas=[dict(kind='parks', id='park', name='', geometry=box(x0, y0, x1, y1)),
                       dict(kind='buildings', id='house', name='', geometry=box(x0+dx*.2,y0+dy*.6,x0+dx*.4,y0+dy*.8))],
                roads=[dict(id='road', name='', cls='residential', bridge=False, level=0,
                            geometry=LineString([(x0-dx, y0+dy/2), (x1+dx, y0+dy/2)]))])


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.recipe = default_recipe()
        cls.recipe['media']=media_profiles()[1]
        cls.recipe['center']=[0,0]
        cls.recipe['framing']['scale_denominator']=2000
        cls.recipe['features']['labels'] = 'none'
        cls.recipe['features']['park_treatment'] = 'fill'
        cls.recipe['furniture']['title'] = True
        cls.recipe['palette']['inks'][0]['width_mm'] = .15
        cls.recipe['palette']['inks'].append(dict(id='broad-black', label='Broad black',color_srgb='#000000',width_mm=.6))
        cls.recipe['ink_roles']['buildings'] = 'broad-black'
        with patch('cartographer.compose.dataset',return_value=fixture_data(cls.recipe)):
            cls.result = compose(cls.recipe, cls.directory/'first', version='fixture')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_vector_page_binding_and_same_color_different_inks(self):
        directory = self.directory/'first'
        intent=json.loads((directory/'map.print-intent.json').read_text())
        validate_intent(intent, directory/'map.pdf')
        page=intent['pages'][0]
        self.assertEqual(page['source_size_mm'],[215.9,279.4])
        self.assertEqual(page['placement'],dict(scale=1,rotation_deg=0,offset_mm=[0,0]))
        self.assertEqual(page['target_media'],self.recipe['media'])
        with pymupdf.open(directory/'map.pdf') as document:
            self.assertFalse(document[0].get_images())
            self.assertIn(self.recipe['title'], document[0].get_text())
            names={g['name'] for g in document.get_ocgs().values()}
            self.assertTrue(all(b['ocg_name'] in names for b in intent['bindings']))
        self.assertTrue(any(b['ink_id']=='broad-black' and b['feature_group']=='buildings' for b in intent['bindings']))
        blacks=[i for i in intent['palette']['inks'] if i['color_srgb']=='#000000']
        self.assertEqual({i['width_mm'] for i in blacks},{.15,.6})
        self.assertNotIn('#ffffff',[i['color_srgb'] for i in intent['palette']['inks']])

    def test_batched_pdf_keeps_white_cores_hollow_buildings_and_clipping(self):
        with pymupdf.open(self.directory/'first/map.pdf') as document:
            pix=document[0].get_pixmap(matrix=pymupdf.Matrix(4,4),alpha=False)
            rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
        x0,y0,x1,y1=footprint(self.recipe).bounds
        scale,offset=framing(self.recipe)
        def pixel(x,y):
            xp,yp=round((x*scale+offset[0])*4),round((-y*scale+offset[1])*4)
            return rgb[yp,xp]
        self.assertTrue(np.all(pixel((x0+x1)/2,(y0+y1)/2)==255),'White road core must erase the park fill')
        self.assertTrue(np.all(pixel(x0+(x1-x0)*.3,y0+(y1-y0)*.7)==255),'Hollow house must clear the park')
        self.assertLess(pixel(x0+(x1-x0)*.6,y0+(y1-y0)*.8)[0],50,'Park fill must survive elsewhere')
        # Source road extends beyond the selected domain and page frame.
        px,py,pw,ph=self.recipe['media']['printable_rect_mm']
        mm=72/25.4*4
        self.assertTrue(np.all(rgb[:, :int((px-.5)*mm)]==255),'No paint may escape left printable margin')
        self.assertTrue(np.all(rgb[:, int((px+pw+.5)*mm):]==255),'No paint may escape right printable margin')

    def test_road_edges_follow_region_and_bridge_roles(self):
        recipe=copy.deepcopy(self.recipe)
        recipe['palette']['inks'][0]['width_mm']=.5
        recipe['palette']['inks'][1]['width_mm']=.3
        recipe['palette']['inks'][2]['width_mm']=.7
        recipe['features'].update(parks=False,buildings='none',labels='none')
        recipe['outside']=copy.deepcopy(dict(features=recipe['features'],ink_roles=recipe['ink_roles']))
        recipe['outside']['ink_roles'].update(roads='green',bridges='green')
        recipe['boundary']=dict(name='Eastern half',geometry=dict(type='Polygon',coordinates=[[[0,-1],[1,-1],[1,1],[0,1],[0,-1]]]))
        recipe['boundary'].update(source='Fixture',sha256=digest(recipe['boundary']['geometry']))
        data=dict(source={'attribution':'Fixture'},issues=[],areas=[],roads=[
            dict(id=str(bridge),name='',cls='residential',bridge=bridge,level=int(bridge),
                 geometry=LineString([(-300,y),(300,y)])) for bridge,y in [(False,-40),(True,40)]])
        for highlighted in [True,False]:
            recipe['features']['bridges']=highlighted
            out=self.directory/('road-widths-'+str(highlighted))
            with patch('cartographer.compose.dataset',return_value=data):compose(recipe,out,cache=self.directory/'road-cache',version='fixture')
            with pymupdf.open(out/'map.pdf') as doc:
                widths={g['layer'].split('|')[0]:g['width'] for g in doc[0].get_drawings() if 'casing|' in g.get('layer','') or 'core|' in g.get('layer','')}
            for group,diameter in [('roads',.5),('bridges',.3 if highlighted else .5),('outside/roads',.7),('outside/bridges',.7)]:
                self.assertAlmostEqual(widths[group+'/core'],.7,places=5)
                self.assertAlmostEqual(widths[group+'/casing'],(diameter+.06)*72/25.4,places=4)

    def test_unknown_width_stays_unknown_and_omits_conversion_intent(self):
        recipe=copy.deepcopy(self.recipe);recipe['palette']['inks'][0]['width_mm']=None
        original=copy.deepcopy(recipe)
        validate_recipe(recipe)
        self.assertEqual(recipe,original)
        directory=self.directory/'unknown'
        with patch('cartographer.compose.dataset',return_value=fixture_data(recipe)):
            result=compose(recipe,directory,version='fixture')
        self.assertFalse(result['intent_available'])
        self.assertEqual(result['unmeasured_inks'][0]['id'],'black')
        self.assertIn(dict(region='inside',role='labels'),result['unmeasured_inks'][0]['roles'])
        with zipfile.ZipFile(directory/'map-bundle.zip') as bundle:
            self.assertIn('map.pdf',bundle.namelist());self.assertNotIn('map.print-intent.json',bundle.namelist())
            exported=json.loads(bundle.read('recipe.json'))
            self.assertIsNone(exported['palette']['inks'][0]['width_mm'])

    def test_unused_building_and_outside_inks_do_not_block_printing(self):
        recipe=copy.deepcopy(self.recipe)
        recipe['palette']['inks'].append(dict(id='pink',label='Pink',color_srgb='#ff1493',width_mm=None))
        recipe['ink_roles']['buildings']='pink'
        recipe['outside']['ink_roles']=dict.fromkeys(recipe['ink_roles'],'pink')
        data=fixture_data(recipe)
        data['areas']=[a for a in data['areas'] if a['kind']!='buildings']
        before=copy.deepcopy(recipe)
        with patch('cartographer.compose.dataset',return_value=data):
            directory=self.directory/'unused-unknown'
            result=compose(recipe,directory,version='fixture')
        self.assertTrue(result['intent_available']);self.assertEqual(result['unmeasured_inks'],[])
        intent=json.loads((directory/'map.print-intent.json').read_text())
        self.assertNotIn('pink',{i['id'] for i in intent['palette']['inks']})
        self.assertEqual(recipe,before,'The authoring palette retains its inactive role choices')

    def test_same_recipe_rebuilds_identical_pdf_bytes(self):
        with patch('cartographer.compose.dataset',return_value=fixture_data(self.recipe)):
            compose(self.recipe,self.directory/'second',version='fixture')
        self.assertEqual((self.directory/'first/map.pdf').read_bytes(),(self.directory/'second/map.pdf').read_bytes())

    def test_contours_survive_forest_paint_and_keep_road_and_building_clearances(self):
        recipe=copy.deepcopy(self.recipe)
        recipe['framing']['scale_denominator']=10000
        recipe['features'].update(contours=True,contour_interval_m=50,shore='none')
        recipe['camera'].update(elevation_deg=55,azimuth_deg=0,exaggeration=1)
        def height(p,zoom):return 103+np.asarray(p)[:,0]/5
        def elevation(r,domain,*args,**kwargs):
            x0,y0,x1,y1=domain.bounds;xs=np.linspace(x0,x1,17);ys=np.linspace(y0,y1,17)
            xx,yy=np.meshgrid(xs,ys)
            return xs,ys,height(np.column_stack([xx.ravel(),yy.ravel()]),0).reshape(xx.shape),{'tiles':[]}
        def data(r,*args,**kwargs):
            return dict(source={'attribution':'Fixture'},issues=[],labels=[],
                        areas=[dict(kind='parks',id='forest',name='',geometry=footprint(r).difference(shapely.Point(0,-500).buffer(150))),
                               dict(kind='buildings',id='house',name='',geometry=box(-100,200,100,400))],
                        roads=[dict(id='road',name='',cls='residential',bridge=False,level=0,
                                    geometry=LineString([(-10000,0),(10000,0)]))])
        def render(name):
            output=self.directory/name
            result=compose(recipe,output,quality='draft',version='contour-layer-fixture',cache=self.directory/'terrain-cache')
            self.assertGreater(result['feature_counts']['terrain/contours'],0)
            with pymupdf.open(output/'map.pdf') as document:
                pix=document[0].get_pixmap(matrix=pymupdf.Matrix(4,4),alpha=False)
                return np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
        with patch('cartographer.compose.dataset',side_effect=data),patch('cartographer.compose.elevations',side_effect=elevation),patch('cartographer.compose.flat_elevations',side_effect=elevation),patch('cartographer.compose.ElevationSampler') as sampler:
            sampler.return_value.sample.side_effect=height;sampler.return_value.inputs={}
            for relief in [False,True]:
                recipe['camera']['relief']=relief;recipe['features']['parks']=False
                baseline=render(f'contours-{relief}-bare')
                x0,y0,x1,y1=np.array(layout(recipe)['frame_mm'])*72/25.4*4
                roi=np.s_[int(y0)+8:int(y1)-8,int(x0)+8:int(x1)-8]
                black=np.all(baseline[roi]<16,axis=2)
                self.assertGreater(np.count_nonzero(black),1000)
                for treatment in ['hatch','concentric','fill','outline']:
                    with self.subTest(relief=relief,treatment=treatment):
                        recipe['features'].update(parks=True,park_treatment=treatment)
                        painted=render(f'contours-{relief}-{treatment}')
                        self.assertTrue(np.all(painted[roi][black]<64),'Forest paint erased a visible contour')
                        if treatment!='outline':
                            colors=painted[roi]
                            self.assertGreater(np.count_nonzero((colors[:,:,1]>colors[:,:,0]+30)&(colors[:,:,0]<80)),100)
                        # These points lie on the 100 m contour: road core and
                        # hollow building interior must still clear its ink.
                        scale,offset=framing(recipe,103 if relief else 0)
                        for y in [0,300]:
                            p=np.array([-15,y,100 if relief else 0])@projection(recipe).T*scale+offset
                            px,py=np.rint(p*4).astype(int)
                            self.assertTrue(np.all(painted[py,px]==255),'Contours must stay below road cores and building interiors')

    def test_engine_extraction_retains_feature_ink_groups_and_no_unsupported_paint(self):
        directory=self.directory/'first'
        events,probe=extract(directory/'map.pdf')
        self.assertFalse([e for e in events['events'] if e['kind']=='unsupported'])
        self.assertTrue(probe['glyphs'])
        sources=set(events['sources'].values())
        bindings=json.loads((directory/'map.print-intent.json').read_text())['bindings']
        self.assertTrue(sources <= {b['ocg_name'] for b in bindings})
        self.assertTrue(any('buildings|house|' in name and 'ink=broad-black' in name for name in sources))
        self.assertTrue(any(name.startswith('labels/furniture|') for name in sources))

    def test_ground_extent_camera_and_sheet_do_not_implicitly_change_scale(self):
        recipe=default_recipe();recipe['camera']['relief']=True;scale,_=framing(recipe)
        recipe['camera']['azimuth_deg']=80;recipe['camera']['elevation_deg']=60
        recipe['media']=media_profiles()[-1]
        recipe['bounds']=[-122.40,47.60,-122.30,47.68]
        self.assertEqual(framing(recipe)[0],scale)
        fitted=fit_recipe(recipe)
        self.assertEqual(fitted['bounds'],recipe['bounds'])
        self.assertNotEqual(fitted['framing']['scale_denominator'],recipe['framing']['scale_denominator'])

    def test_elevation_samples_tile_pixels_and_exact_domain_edges(self):
        from PIL import Image
        import io
        recipe=default_recipe();recipe['center']=[0,0];recipe['framing']['scale_denominator']=2000
        domain=footprint(recipe)
        # Constant 120.5 m Terrarium surface, including tile boundary samples.
        image=Image.new('RGB',(256,256),(128,120,128));buffer=io.BytesIO();image.save(buffer,format='PNG')
        with patch('cartographer.sources.fetch',return_value=(buffer.getvalue(),{})):
            xs,ys,z,source=elevations(recipe,domain,Path(self.temp.name)/'dem','draft')
        np.testing.assert_allclose(z,120.5)
        np.testing.assert_allclose([xs[0],ys[0],xs[-1],ys[-1]],domain.bounds)
        self.assertEqual(z.shape,(33,33))

    def test_invalid_recipes_do_not_start_expensive_work(self):
        for edit in [lambda r:r['center'].__setitem__(1,90),
                     lambda r:r['camera'].__setitem__('elevation_deg',0),
                     lambda r:r['framing'].__setitem__('scale_denominator',float('nan')),
                     lambda r:r['ink_roles'].__setitem__('roads','missing'),
                     lambda r:r['palette']['inks'][0].__setitem__('color_srgb','#ffffff')]:
            bad=copy.deepcopy(self.recipe);edit(bad)
            with self.assertRaises((ValueError, __import__('jsonschema').ValidationError)):
                validate_recipe(bad)


class UnifiedPipelineTests(unittest.TestCase):
    def test_camera_rotates_flat_geometry_and_inverse_footprint_covers_page(self):
        from cartographer.model import MM
        r=default_recipe();r['camera']['azimuth_deg']=37
        self.assertNotEqual(projection(r)[0,1],0)
        for relief in [False,True]:
            r['camera']['relief']=relief
            g=footprint(r);scale,offset=framing(r)
            projected=np.array([[x,y,0] for x,y in g.exterior.coords]) @ projection(r).T*scale+offset
            polygon=__import__('shapely').Polygon(projected)
            self.assertTrue(polygon.covers(box(*(np.array(layout(r)['frame_mm'])*MM))))
        r['center']=[-73.98,40.75]
        validate_recipe(r)
        self.assertLess(plan(r,footprint(r),'draft')['zoom'],15)

    def test_state_scale_all_buildings_is_refused_and_auto_reports_reduction(self):
        r=default_recipe();r['framing']['scale_denominator']=2000000;r['features']['buildings']='all'
        with self.assertRaisesRegex(ValueError,'Every building'):
            plan(r,footprint(r),'proof')
        r['framing']['scale_denominator']=50000000
        with self.assertRaisesRegex(ValueError,'Every building'):
            plan(r,footprint(r),'proof')
        r['framing']['scale_denominator']=2000000
        draft=plan(r,footprint(r),'draft')
        self.assertLessEqual(len(draft['tiles']),48);self.assertTrue(draft['warnings'])
        r['features']['buildings']='auto'
        p=plan(r,footprint(r),'proof');self.assertFalse(p['buildings']);self.assertLessEqual(len(p['tiles']),96)

    def test_legacy_migration_preserves_saved_fields_and_palette(self):
        r=default_recipe();legacy={k:copy.deepcopy(r[k]) for k in ['title','bounds','media','framing','camera','features','palette','ink_roles']}
        legacy.update(schema='plotline.map-recipe/1',preset='relief')
        legacy['camera'].pop('relief');legacy['camera']['azimuth_deg']=215
        legacy['features']['buildings']=True
        before=copy.deepcopy(legacy);m=migrate(legacy);validate_recipe(m)
        self.assertEqual(legacy,before);self.assertEqual(m['palette'],legacy['palette'])
        self.assertTrue(m['camera']['relief']);self.assertEqual(m['camera']['azimuth_deg'],35)
        self.assertEqual(m['extent'],'viewport');self.assertNotIn('preset',m)

    def test_adjacent_vector_tiles_dissolve_without_shoreline_seam(self):
        import mapbox_vector_tile
        from cartographer.sources import _dataset
        r=default_recipe();r['center']=[0,0]
        domain=box(-100,-100,100,100)
        tile=mapbox_vector_tile.encode({'name':'water','features':[{'geometry':box(0,0,4096,4096),'properties':{'class':'lake'},'id':1}]},default_options={'y_coord_down':True})
        with tempfile.TemporaryDirectory() as d,patch('cartographer.sources.fetch',return_value=(tile,{})):
            data=_dataset(r,domain,Path(d),dict(tiles=[(1,x,y) for x in [0,1] for y in [0,1]],zoom=1,warnings=[],buildings=False),'draft')
        water=__import__('shapely').union_all([a['geometry'] for a in data['areas']])
        self.assertAlmostEqual(water.area,domain.area)
        self.assertTrue(water.boundary.intersection(box(-99,-99,99,99)).is_empty)

    def test_transportation_name_line_supplies_a_road_label_and_direction(self):
        import mapbox_vector_tile
        from cartographer.sources import _dataset
        r=default_recipe();r['center']=[0,0]
        tile=mapbox_vector_tile.encode({'name':'transportation_name','features':[{'geometry':LineString([(2047,2048),(2049,2048)]),'properties':{'name':'Map Street','class':'minor'},'id':1}]},default_options={'y_coord_down':True})
        with tempfile.TemporaryDirectory() as d,patch('cartographer.sources.fetch',return_value=(tile,{})):
            data=_dataset(r,box(-100,-100,100,100),Path(d),dict(tiles=[(0,0,0)],zoom=0,warnings=[],buildings=False),'draft')
        label=data['labels'][0]
        self.assertEqual(label['name'],'Map Street');self.assertEqual(label['kind'],'roads')
        self.assertGreater(label['direction'][0],0);self.assertEqual(label['direction'][1],0)

    def test_boundary_styles_clip_colored_finite_width_paint_and_keep_identity(self):
        from cartographer.boundaries import snapshot
        from shapely.geometry import mapping,Polygon
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=2000
        r['features'].update(parks=False,buildings='none',roads='all',labels='none')
        r['outside']['features']=copy.deepcopy(r['features'])
        r['ink_roles']['roads']='red';r['outside']['ink_roles']['roads']='green'
        domain=footprint(r);x0,y0,x1,y1=domain.bounds
        polygon=Polygon([lonlat(x,y) for x,y in box(x0,y0,0,y1).exterior.coords])
        r['boundary']=snapshot(mapping(polygon),'West half','Fixture')
        data=dict(areas=[],roads=[dict(id='crossing',name='',cls='path',bridge=False,level=0,geometry=LineString([(x0,0),(x1,0)]))],source={'attribution':'Fixture'},issues=[])
        with tempfile.TemporaryDirectory() as d,patch('cartographer.compose.dataset',return_value=data):
            compose(r,Path(d),version='fixture')
            intent=json.loads((Path(d)/'map.print-intent.json').read_text())
            self.assertTrue(any(b['feature_group']=='outside/roads/casing' and b['ink_id']=='green' for b in intent['bindings']))
            with pymupdf.open(Path(d)/'map.pdf') as doc:
                pix=doc[0].get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False)
                rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
            scale,offset=framing(r);middle=int(round(offset[0]*3));y=int(round(offset[1]*3))
            band=rgb[y-3:y+4]
            red=(band[:,:,0]>200)&(band[:,:,1]<100)&(band[:,:,2]<100)
            green=(band[:,:,1]>60)&(band[:,:,0]<100)&(band[:,:,2]<100)
            self.assertTrue(red[:,:middle-1].any());self.assertFalse(red[:,middle+1:].any())
            self.assertTrue(green[:,middle+1:].any());self.assertFalse(green[:,:middle-1].any())

    def test_relief_boundary_does_not_lose_labels_at_internal_mesh_seams(self):
        from cartographer.boundaries import snapshot
        from shapely.geometry import mapping,Polygon,Point
        r=default_recipe();r.update(center=[0,0],extent='bounds',bounds=[-.01,-.01,.01,.01])
        r['camera'].update(relief=True,azimuth_deg=33,elevation_deg=45)
        r['framing']['scale_denominator']=12000
        r['features'].update(parks=False,buildings='none',roads='none')
        r['outside']['features'].update(parks=False,buildings='none',roads='none')
        domain=footprint(r);x0,y0,x1,y1=domain.bounds
        r['boundary']=snapshot(mapping(Polygon([lonlat(x,y) for x,y in domain.exterior.coords])),'Whole domain','Fixture')
        xs=np.linspace(x0,x1,9);ys=np.linspace(y0,y1,9);xx,yy=np.meshgrid(xs,ys)
        z=30*np.sin(xx/800)*np.cos(yy/900)
        elevation=(xs,ys,z,{'tiles':[]})
        data=dict(areas=[],roads=[],labels=[dict(name='Across mesh seams',kind='place',rank=1,geometry=Point(0,0))],source={'attribution':'Fixture'},issues=[])
        with tempfile.TemporaryDirectory() as d,patch('cartographer.compose.dataset',return_value=data),patch('cartographer.compose.elevations',return_value=elevation),patch('cartographer.compose.ElevationSampler') as sampler:
            sampler.return_value.sample.side_effect=lambda p,z:30*np.sin(np.asarray(p)[:,0]/800)*np.cos(np.asarray(p)[:,1]/900)
            sampler.return_value.inputs={}
            result=compose(r,Path(d),cache=Path(d)/'cache',version='fixture')
            self.assertEqual(result['label_count'],1)
            self.assertEqual(result['depth_audit']['maximum_frontmost_depth_error_m'],0)
    def test_http_cache_reuses_exact_bytes_without_network(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'original source bytes'
        with tempfile.TemporaryDirectory() as d,patch('cartographer.sources.urlopen',return_value=Response()) as request:
            first,a=fetch('https://example.test/tile',d);second,b=fetch('https://example.test/tile',d)
            self.assertEqual(request.call_count,1);self.assertEqual(first,second);self.assertEqual(a,b)


class AreaRepairTests(unittest.TestCase):
    def test_real_simplified_woodland_keeps_overlaps_filled_and_holes_open(self):
        geometry=shapely.from_wkt((ROOT/'tests/fixtures/woodland-overlap.wkt').read_text())
        self.assertFalse(geometry.is_valid)
        self.assertTrue(all(p.is_valid for p in geometry.geoms))
        repaired=valid_area(geometry)
        self.assertTrue(repaired.is_valid)
        for component in geometry.geoms:
            self.assertLess(component.difference(repaired).area,1e-8)
        overlap=geometry.geoms[0].intersection(geometry.geoms[1])
        self.assertGreater(overlap.area,.2)
        self.assertTrue(repaired.covers(overlap.representative_point()),'Overlaps must not become paper holes')
        water=box(9638,157798,9641,157801)
        clipped=repaired.difference(water)
        self.assertTrue(clipped.is_valid)
        self.assertAlmostEqual(clipped.area,repaired.area-water.area,places=7)
        self.assertTrue(valid_area(clipped).equals(clipped),'Real holes must remain open')

    def test_self_crossing_component_preserves_both_lobes(self):
        crossing=shapely.Polygon([(0,0),(4,4),(0,4),(4,0),(0,0)])
        repaired=valid_area(crossing)
        self.assertTrue(repaired.is_valid)
        self.assertAlmostEqual(repaired.area,8)
        self.assertTrue(repaired.covers(shapely.Point(2,1)))
        self.assertTrue(repaired.covers(shapely.Point(2,3)))


class ProgressTests(unittest.TestCase):
    def test_overall_estimate_uses_parent_work_and_never_resets_for_inner_counts(self):
        from cartographer.progress import Progress
        with tempfile.TemporaryDirectory() as directory:
            progress=Progress(Path(directory)/'progress.json');self.addCleanup(progress.close);values=[]
            for stage in ['planning','elevation','tiles','geometry','sketch','visibility']:
                progress(stage,stage,0,10,'items');values.append(progress.current['overall_percent'])
            progress.advance('drawing',.2)
            for detail,completed,total in [('First hatch',0,100),('First hatch',100,100),('Next park',0,50)]:
                progress('drawing',detail,completed,total,'lines')
                values.append(progress.current['overall_percent'])
                self.assertEqual(progress.current['overall_percent'],49)
            progress.advance('labels',.48);progress('labels','Outside labels',1,2,'labels')
            values.append(progress.current['overall_percent'])
            progress.advance('drawing',.6);progress('drawing','Inside parks',0,30,'parks')
            values.append(progress.current['overall_percent'])
            for stage in ['pdf','preview','checks','bundle','complete']:
                progress(stage,stage);values.append(progress.current['overall_percent'])
            self.assertEqual(values,sorted(values))
            self.assertEqual(values[-1],99,'Only the browser can confirm the image is visible')
            self.assertEqual(progress.current['overall_step'],8)

    def test_progress_reports_real_counts_and_stage_times(self):
        from cartographer.progress import Progress
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'progress.json';progress=Progress(path);self.addCleanup(progress.close)
            with patch('cartographer.progress.time.time',return_value=100),patch('cartographer.progress.time.monotonic',return_value=100):progress('tiles','Loading',0,2,'tiles')
            with patch('cartographer.progress.time.time',return_value=101),patch('cartographer.progress.time.monotonic',return_value=101):progress('tiles','Loading',1,2,'tiles')
            value=json.loads(path.read_text())
            self.assertEqual((value['completed'],value['total'],value['started_at']),(1,2,100))
            with patch('cartographer.progress.time.time',return_value=103),patch('cartographer.progress.time.monotonic',return_value=103):progress('geometry','Joining features')
            value=json.loads(path.read_text());self.assertIsNone(value['total'])
            self.assertEqual(value['history'],[dict(stage='tiles',seconds=3)])
            with patch('cartographer.progress.time.time',return_value=103.3),patch('cartographer.progress.time.monotonic',return_value=103.3):progress('geometry','Projecting parks',0,300,'facets')
            value=json.loads(path.read_text())
            self.assertEqual(value['detail'],'Projecting parks')
            self.assertEqual(value['started_at'],103)
            self.assertEqual(value['history'],[dict(stage='tiles',seconds=3)])

    def test_job_exposes_worker_progress_and_cancelled_elapsed_time_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=Jobs(directory,'progress-fixture')
            try:
                with patch.object(jobs.pool,'submit'):
                    job=jobs.submit(default_recipe(),'draft','progress-tab',1)
                self.assertEqual(job['progress']['stage'],'queued')
                record=jobs.jobs[job['id']];record.update(status='running',started=record['created'])
                now=time.time()
                atomic_json(Path(directory)/'tasks'/job['id']/'output/progress.json',dict(stage='drawing',detail='Drawing parks',completed=3,total=12,unit='parks',started_at=now,updated_at=now,history=[]))
                result=jobs.public(job['id']);self.assertEqual(result['progress']['completed'],3)
                jobs.cancel('progress-tab',2);elapsed=jobs.public(job['id'])['elapsed_seconds']
                with patch('cartographer.jobs.time.time',return_value=now+100):
                    self.assertEqual(jobs.public(job['id'])['elapsed_seconds'],elapsed)
            finally:jobs.close()

    def test_large_automatic_draft_reduces_detail_without_changing_proof(self):
        recipe=default_recipe();recipe['center']=[-122.346,47.651];recipe['framing']['scale_denominator']=31304
        domain=footprint(recipe)
        self.assertEqual(plan(recipe,domain,'draft')['zoom'],13)
        self.assertEqual(plan(recipe,domain,'proof')['zoom'],14)
        recipe['framing']['scale_denominator']=6500
        self.assertEqual(plan(recipe,footprint(recipe),'draft')['zoom'],14)


class RevisionTests(unittest.TestCase):
    def test_save_conflicts_preserve_prior_snapshot_and_all_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Recipes(directory);recipe=default_recipe('waterways')
            first=store.save(recipe)
            recipe['palette']['inks'][0]['color_srgb']='#123456'
            second=store.save(recipe,first['id'],first['revision'])
            self.assertEqual(second['revision'],2)
            self.assertEqual(store.get(first['id'],1)['recipe']['palette']['inks'][0]['color_srgb'],'#000000')
            with self.assertRaises(Conflict):store.save(recipe,first['id'],1)
            self.assertEqual(len(list(Path(directory).rglob('[0-9]*.json'))),2)
            self.assertEqual(Recipes(directory).get(first['id']),second)
            with self.assertRaises(ValueError):store.get('../escape')

    def test_cancel_terminates_running_composer_and_rejects_old_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=Jobs(directory,'test');started=threading.Event();processes=[]
            real_popen=subprocess.Popen
            def slow(*args,**kwargs):
                process=real_popen([sys.executable,'-c','import time; time.sleep(30)'])
                processes.append(process);started.set();return process
            try:
                with patch('cartographer.jobs.subprocess.Popen',side_effect=slow):
                    task=jobs.submit(default_recipe('waterways'),'draft','tab-one',1)
                    self.assertTrue(started.wait(5))
                    jobs.cancel('tab-one',2)
                    processes[0].wait(timeout=5)
                    self.assertEqual(jobs.public(task['id'])['status'],'cancelled')
                    with self.assertRaises(Conflict):jobs.submit(default_recipe('waterways'),'proof','tab-one',1)
                    self.assertFalse((Path(directory)/'exports'/task['cache_key']).exists())
            finally:
                jobs.close()
                for p in processes:
                    if p.poll() is None:p.kill();p.wait()

    def test_cache_is_recipe_quality_version_bound_and_stale_export_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=Jobs(directory,'composer-v1');recipe=default_recipe('waterways')
            try:
                key=digest(['composer-v1',recipe,'proof'])
                atomic_json(Path(directory)/'exports'/key/'result.json',dict(recipe_hash=digest(recipe)))
                task=jobs.submit(recipe,'proof','tab-one',1)
                for _ in range(100):
                    if jobs.public(task['id'])['status']=='ready':break
                    time.sleep(.01)
                self.assertEqual(jobs.public(task['id'])['status'],'ready')
                self.assertEqual(jobs.export(task['id'],'tab-one',1,digest(recipe)).name,key)
                jobs.cancel('tab-one',2)
                with self.assertRaises(Conflict):jobs.export(task['id'],'tab-one',1,digest(recipe))
                with self.assertRaises(Conflict):jobs.export(task['id'],'another-tab',1,digest(recipe))
                self.assertNotEqual(key,digest(['composer-v1',recipe,'draft']))
                self.assertNotEqual(key,digest(['composer-v2',recipe,'proof']))
                recipe['palette']['inks'][0]['width_mm']=.7
                self.assertNotEqual(key,digest(['composer-v1',recipe,'proof']))
            finally:jobs.close()


class PrintClientTests(unittest.TestCase):
    def test_live_adapter_offline_snapshot_and_document_submission(self):
        requests=[]
        carousel=json.loads((ROOT/'cartographer/fixtures/carousel.json').read_text())
        cap=capabilities(carousel,media_profiles());full_api=False
        class Server(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(('GET',self.path,b''))
                if self.path=='/api/v1/capabilities' and not full_api:
                    self.send_error(404);return
                value=cap if self.path=='/api/v1/capabilities' else carousel
                data=json.dumps(value).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
            def do_POST(self):
                if self.headers.get('X-Plotline-Request') != '1':
                    self.send_error(403,'Missing X-Plotline-Request: 1');return
                data=self.rfile.read(int(self.headers['Content-Length']));requests.append(('POST',self.path,data))
                self.send_response(202);self.end_headers()
                self.wfile.write(json.dumps(dict(id='fixture-doc',status='received',status_url='/api/v1/documents/fixture-doc',review_url='/review/fixture-doc')).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Server)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as directory:
            client=PrintClient(f'http://127.0.0.1:{server.server_port}',directory,'http://dev.example:9191')
            snapshot=client.inventory()
            self.assertFalse(snapshot['offline']);self.assertEqual(snapshot['source'],'live carousel adapter')
            self.assertIsNone(next(i for i in snapshot['capabilities']['available_inks'] if i['label']=='hot pink')['width_mm'])
            full_api=True;cap['implemented']['pdf_upload']=True;cap['implemented']['pdf_handoff']=True;cap['implemented']['per_ink_widths']=True
            from make_boundary_fixture import make_fixture
            fixture=Path(directory)/'fixture';fixture.mkdir();intent=make_fixture(fixture)
            receipt=client.submit(fixture/'boundary.pdf',intent)
            self.assertEqual(receipt['status'],'received')
            self.assertEqual(receipt['review_url'],'http://dev.example:9191/review/fixture-doc')
            sent=[r for r in requests if r[0]=='POST']
            self.assertEqual([r[1] for r in sent],['/api/v1/documents'])
            self.assertIn((fixture/'boundary.pdf').read_bytes(),sent[0][2])
            self.assertIn(b'name="intent"',sent[0][2]);self.assertNotIn(b'plotline.map-recipe',sent[0][2])
            self.assertIn(b'filename="Shared boundary fixture.pdf"',sent[0][2])
            server.shutdown();server.server_close();thread.join()
            offline=client.inventory()
            self.assertTrue(offline['offline'])
            self.assertEqual(offline['capabilities'],snapshot['capabilities'])


if __name__=='__main__':unittest.main()
