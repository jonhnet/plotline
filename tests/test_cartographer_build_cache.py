import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf
import shapely

from cartographer.build_cache import BuildCache,fingerprint
from cartographer.compose import compose,InkDrawing
from cartographer.model import default_recipe,digest
from pdf_native import extract


class IncrementalCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.number=0
        self.recipe=default_recipe();self.recipe['center']=[0,0]
        self.recipe['framing']['scale_denominator']=12000
        self.recipe['features'].update(buildings='all',hatch_spacing_mm=6,shore='echo')
        self.data=dict(source={'attribution':'Fixture'},issues=[],labels=[dict(kind='place',name='Town',rank=1,geometry=shapely.Point(-800,-600))],
            areas=[dict(kind='parks',id='forest',name='Forest',geometry=shapely.box(-1500,-1000,1500,1000)),
                   dict(kind='water',id='lake',name='Lake',geometry=shapely.box(1200,-500,1800,500)),
                   dict(kind='buildings',id='house',name='',geometry=shapely.box(-100,200,100,400))],
            roads=[dict(id='road',name='Street',cls='residential',bridge=False,level=0,geometry=shapely.LineString([(-5000,0),(5000,0)]))])
        self.mock=patch('cartographer.compose.dataset',side_effect=lambda *a,**kw:copy.deepcopy(self.data));self.mock.start()

    def tearDown(self):self.mock.stop();self.temp.cleanup()

    def render(self,recipe=None,cache='cache',quality='draft',version='cache-fixture'):
        self.number+=1;out=self.root/str(self.number)
        result=compose(recipe or self.recipe,out,quality=quality,cache=self.root/cache,version=version)
        return result,out

    def nodes(self,result):return {r['node']:r for r in result['build_cache']['nodes']}

    def equal_art(self,left,right):
        # Exact PDF bytes exercise paint order, resource relocation, masks and
        # ink bindings, not just a cache bookkeeping counter.
        self.assertEqual((left/'map.pdf').read_bytes(),(right/'map.pdf').read_bytes())
        self.assertEqual(json.loads((left/'map.print-intent.json').read_text()),json.loads((right/'map.print-intent.json').read_text()))
        # Audit the interpreted PDF, not just the renderer's binding table.
        # Includes fills, white knockouts and outlined text, in cached/clean
        # 2D and terrain builds, both boundary regions, and page furniture.
        for output in [left,right]:
            recipe=json.loads((output/'recipe.json').read_text())
            intent=json.loads((output/'map.print-intent.json').read_text())
            bindings={b['ocg_name']:b for b in intent['bindings']}
            inks={i['id']:i for i in intent['palette']['inks']}
            events,_=extract(output/'map.pdf')
            sources={int(k):v for k,v in events['sources'].items()}
            for index,event in enumerate(events['events']):
                if event['kind'] not in ('fill','stroke'):continue
                self.assertIn(index,sources,'Every PDF mark must have a feature layer')
                binding=bindings[sources[index]]
                category=binding['feature_group'].split('/')
                style=recipe['outside'] if category[0]=='outside' else recipe
                if category[0]=='outside':category=category[1:]
                role=category[0]
                if role=='legend':role=category[1]
                elif category[:2]==['terrain','hillshade']:role='hillshade'
                elif category[:2]==['terrain','silhouette']:role='silhouettes'
                elif role=='bridges' and not style['features']['bridges']:role='roads'
                self.assertIn(role,style['ink_roles'])
                self.assertEqual(binding['ink_id'],style['ink_roles'][role])
                if event['color']!=[1,1,1]:
                    color=inks[binding['ink_id']]['color_srgb']
                    np.testing.assert_allclose(event['color'],[int(color[i:i+2],16)/255 for i in (1,3,5)],atol=1e-6)

    def test_unknown_categories_and_stale_cached_role_bindings_fail(self):
        draw=InkDrawing(self.recipe)
        try:
            with self.assertRaisesRegex(ValueError,'no ink role'):
                draw.paint(shapely.LineString([(10,10),(20,20)]),'unassigned/mystery','x')
            draw.paint(shapely.LineString([(10,10),(20,20)]),'parks/boundary','forest')
            next(iter(draw.bindings.values()))['ink_id']='red'
            with self.assertRaisesRegex(ValueError,'does not match'):
                draw.flush()
        finally:draw.doc.close()

    def test_label_edit_skips_geometry_paint_and_matches_a_clean_build(self):
        initial,_=self.render();self.assertGreater(initial['build_cache']['built'],5)
        r=copy.deepcopy(self.recipe);r['features']['park_labels']=False
        paint=InkDrawing.paint
        def only_labels(draw,geometry,category,*args,**kwargs):
            self.assertTrue(category.startswith('labels/'),'Non-label paint was rebuilt after a label-only edit')
            return paint(draw,geometry,category,*args,**kwargs)
        with patch('cartographer.compose.hatches',side_effect=AssertionError('Hatches rebuilt')),patch.object(InkDrawing,'paint',only_labels):
            result,warm=self.render(r)
        self.assertEqual(result['build_cache']['built'],0)
        _,clean=self.render(r,'fresh');self.equal_art(warm,clean)
        with pymupdf.open(warm/'map.pdf') as doc:
            text=doc[0].get_text();self.assertNotIn('Forest',text);self.assertIn('Town',text)
        self.assertGreater(result['feature_counts']['parks/explicit_hatch'],0)

    def test_color_width_furniture_and_spacing_have_distinct_dependencies(self):
        self.render()
        r=copy.deepcopy(self.recipe);r['palette']['inks'][1]['width_mm']=.35
        result,_=self.render(r);self.assertEqual(result['build_cache']['built'],0)
        r['title']='New title';r['furniture']['title']=True
        result,_=self.render(r);self.assertEqual(result['build_cache']['built'],0)
        r['features']['hatch_spacing_mm']=4
        result,_=self.render(r);self.assertEqual([k for k,v in self.nodes(result).items() if not v['reused']],['Forest paint'])
        # Colors bind after path construction, including fills and outlines.
        r['palette']['inks'][2]['color_srgb']='#008888'
        with patch('cartographer.compose.hatches',side_effect=AssertionError('Hatches rebuilt')):
            result,warm=self.render(r)
        self.assertEqual(result['build_cache']['built'],0)
        _,fresh=self.render(r,'fresh');self.equal_art(warm,fresh)

    def test_water_ink_assignment_reuses_paths_but_spacing_rebuilds_only_water(self):
        r=copy.deepcopy(self.recipe);r['features']['water_treatment']='concentric'
        self.render(r)
        r['palette']['inks'].append(dict(r['palette']['inks'][0],id='blue',label='Blue',color_srgb='#1234ff'))
        r['ink_roles']['water']='blue'
        with patch('cartographer.compose.concentric_rings',side_effect=AssertionError('Water rebuilt')):
            result,warm=self.render(r)
        self.assertEqual(result['build_cache']['built'],0)
        _,fresh=self.render(r,'fresh-water');self.equal_art(warm,fresh)
        r['features']['water_spacing_mm']=4
        result,warm=self.render(r)
        self.assertEqual({k for k,v in self.nodes(result).items() if not v['reused']},{'Water fill','Shore paint'})
        _,fresh=self.render(r,'fresh-spacing');self.equal_art(warm,fresh)

    def test_same_color_new_identity_and_recoloring_preserve_white_knockouts(self):
        r=copy.deepcopy(self.recipe);r['features']['water_treatment']='fill'
        self.render(r)
        r['palette']['inks'].append(dict(r['palette']['inks'][0],id='other',label='Other black'))
        r['ink_roles']['water']='other'
        result,warm=self.render(r);self.assertEqual(result['build_cache']['built'],0)
        _,fresh=self.render(r,'fresh-identity');self.equal_art(warm,fresh)
        # Recolor all inks, including fills. Road cores and label/forest
        # backgrounds must remain white; same-color ink IDs stay distinct.
        for ink in r['palette']['inks']:ink['color_srgb']='#123456'
        result,warm=self.render(r);self.assertEqual(result['build_cache']['built'],0)
        _,fresh=self.render(r,'fresh-white');self.equal_art(warm,fresh)

    def test_road_pen_diameter_rebuilds_casings_and_matches_a_clean_build(self):
        self.render();r=copy.deepcopy(self.recipe)
        r['palette']['inks'][0]['width_mm']=.5
        result,warm=self.render(r)
        self.assertEqual([k for k,v in self.nodes(result).items() if not v['reused']],['Road paint'])
        _,fresh=self.render(r,'fresh');self.equal_art(warm,fresh)
        with pymupdf.open(warm/'map.pdf') as doc:
            lines=doc[0].get_drawings()
        casing=next(g for g in lines if g.get('layer','').startswith('roads/casing|'))
        self.assertAlmostEqual(casing['width'],.56*72/25.4,places=4)

    def test_camera_paper_quality_code_and_source_changes_invalidate_the_right_work(self):
        self.render();r=copy.deepcopy(self.recipe);r['camera']['azimuth_deg']=20
        result,warm=self.render(r);self.assertFalse(self.nodes(result)['Forest paint']['reused'])
        _,fresh=self.render(r,'fresh');self.equal_art(warm,fresh)
        r['media']['sheet_size_mm'][0]+=10
        result,_=self.render(r);self.assertFalse(self.nodes(result)['Forest paint']['reused'])
        result,_=self.render(r,quality='proof');self.assertFalse(self.nodes(result)['Forest paint']['reused'])
        result,_=self.render(r,version='new-code');self.assertEqual(result['build_cache']['reused'],0)
        self.data['areas'][0]['geometry']=shapely.box(-1600,-1100,1600,1100)
        result,_=self.render(r)
        self.assertFalse(self.nodes(result)['Forest paint']['reused']);self.assertTrue(self.nodes(result)['Road paint']['reused'])

    def test_layer_resources_relocate_when_preceding_paint_and_boundary_styles_change(self):
        self.render();r=copy.deepcopy(self.recipe);r['features']['water_treatment']='fill'
        result,warm=self.render(r)
        self.assertTrue(self.nodes(result)['Forest paint']['reused'])
        _,fresh=self.render(r,'fresh');self.equal_art(warm,fresh)
        # Distinct masks and ink IDs for each region; labels share collision
        # state, so their placement remains fresh after either region changes.
        geometry={'type':'Polygon','coordinates':[[[0.,-.1],[.1,-.1],[.1,.1],[0.,.1],[0.,-.1]]]}
        r['boundary']=dict(name='Half',geometry=geometry,source='Fixture',sha256=digest(geometry))
        r['outside']=copy.deepcopy(dict(features=r['features'],ink_roles=r['ink_roles']));r['outside']['ink_roles']['parks']='red'
        self.render(r);r['outside']['features']['park_labels']=False
        result,warm=self.render(r);self.assertEqual(result['build_cache']['built'],0)
        _,fresh=self.render(r,'fresh-boundary');self.equal_art(warm,fresh)
        r['outside']['ink_roles']['parks']='black'
        r['palette']['inks'][0]['color_srgb']='#123456'
        result,warm=self.render(r);self.assertEqual(result['build_cache']['built'],0)
        _,fresh=self.render(r,'fresh-boundary-color');self.equal_art(warm,fresh)

    def test_sun_changes_only_hillshade_while_hidden_geometry_remains_reused(self):
        r=copy.deepcopy(self.recipe);r['features'].update(hillshade=True,contours=True,contour_interval_m=100,terrain_edges=True)
        r['camera'].update(relief=True,elevation_deg=25,exaggeration=1)
        def height(p,zoom):
            p=np.asarray(p);return 700*np.exp(-(p[:,0]**2+p[:,1]**2)/400000)
        def elevation(recipe,domain,*args,**kwargs):
            x0,y0,x1,y1=domain.bounds;xs=np.linspace(x0,x1,17);ys=np.linspace(y0,y1,17);xx,yy=np.meshgrid(xs,ys)
            return xs,ys,height(np.column_stack([xx.ravel(),yy.ravel()]),0).reshape(xx.shape),{'tiles':[]}
        with patch('cartographer.compose.elevations',side_effect=elevation),patch('cartographer.compose.ElevationSampler') as sampler:
            sampler.return_value.sample.side_effect=height;sampler.return_value.inputs={}
            self.render(r);r['lighting']['azimuth_deg']=0
            result,warm=self.render(r)
            rebuilt={k for k,v in self.nodes(result).items() if not v['reused']}
            self.assertEqual(rebuilt,{'Slope illumination','Hillshade paint'})
            _,fresh=self.render(r,'fresh');self.equal_art(warm,fresh)
            r['features']['park_labels']=False
            with patch('cartographer.compose.MeshTerrain',side_effect=AssertionError('Terrain rebuilt')),patch('cartographer.compose.shade_regions',side_effect=AssertionError('Shade rebuilt')):
                result,_=self.render(r);self.assertEqual(result['build_cache']['built'],0)
                r['palette']['inks'][0]['color_srgb']='#123456'
                r['ink_roles']['hillshade']='green'
                result,warm=self.render(r);self.assertEqual(result['build_cache']['built'],0)
            _,fresh=self.render(r,'fresh-terrain-color');self.equal_art(warm,fresh)

    def test_geometry_identity_includes_precision_policy(self):
        snapped=shapely.set_precision(shapely.box(0,0,1,1),.1)
        floating=shapely.set_precision(snapped,0)
        self.assertEqual(snapped.wkb,floating.wkb)
        self.assertNotEqual(fingerprint(snapped),fingerprint(floating))

    def test_failed_node_is_never_published_or_reused(self):
        cache=BuildCache(self.root,'test',lambda *args:None)
        def fail():raise RuntimeError('Interrupted computation')
        with self.assertRaises(RuntimeError):cache.node('Fixture',{},fail)
        self.assertEqual(cache.node('Fixture',{},lambda:'complete'),'complete')
        self.assertFalse(cache.records[-1]['reused'])
        again=BuildCache(self.root,'test',lambda *args:None)
        self.assertEqual(again.node('Fixture',{},fail),'complete');self.assertTrue(again.records[-1]['reused'])
