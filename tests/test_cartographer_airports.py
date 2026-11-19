import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mapbox_vector_tile
import numpy as np
import pymupdf
import shapely

from cartographer.boundaries import snapshot
from cartographer.compose import compose
from cartographer.geography import framing, footprint
from cartographer.model import MM, default_recipe, validate_recipe
from cartographer.sources import _dataset


class AirportTests(unittest.TestCase):
    def test_legacy_recipes_adopt_each_regions_road_ink_without_rewriting_input(self):
        recipe=default_recipe()
        recipe['ink_roles']['roads']='red';recipe['outside']['ink_roles']['roads']='green'
        for style in [recipe,recipe['outside']]:
            del style['features']['airports'];del style['ink_roles']['airports']
        original=copy.deepcopy(recipe);updated=validate_recipe(recipe)
        self.assertEqual(recipe,original)
        self.assertTrue(updated['features']['airports']);self.assertTrue(updated['outside']['features']['airports'])
        self.assertEqual(updated['ink_roles']['airports'],'red')
        self.assertEqual(updated['outside']['ink_roles']['airports'],'green')
        updated['features']['airports']=False
        self.assertFalse(validate_recipe(updated)['features']['airports'])
        updated['features']['airports']='yes'
        with self.assertRaisesRegex(ValueError,'airports must be true or false'):validate_recipe(updated)

    def test_tiles_join_both_runways_and_pavement_without_airport_perimeters(self):
        recipe=default_recipe();recipe['center']=[0,0]
        def tile(east):
            x0,x1=(0,2) if east else (4094,4096)
            features=[]
            for identity,cls,ref,y in [(1,'runway','16R/34L',4094),(2,'runway','16L/34R',4095),(3,'taxiway','A',4093)]:
                features.append(dict(id=identity,geometry=shapely.LineString([(x0,y),(x1,y)]),properties={'class':cls,'ref':ref}))
            for identity,cls in [(4,'apron'),(5,'aerodrome'),(1,'runway')]:
                features.append(dict(id=identity,geometry=shapely.box(x0,4093,x1,4094),properties={'class':cls}))
            features.append(dict(id=6,geometry=shapely.Point(x0,4094),properties={'class':'gate'}))
            return mapbox_vector_tile.encode({'name':'aeroway','features':features},default_options={'y_coord_down':True})
        domain=shapely.box(-20000,-20000,20000,20000)
        config=dict(tiles=[(1,0,0),(1,1,0)],zoom=1,warnings=[],buildings=False)
        with tempfile.TemporaryDirectory() as directory:
            def read(url,*args):return tile('/1/1/' in url),{}
            with patch('cartographer.sources.fetch',side_effect=read):
                data=_dataset(recipe,domain,Path(directory),config,'draft')
                off=_dataset(recipe,domain,Path(directory),dict(config,airports=False),'draft')
        self.assertEqual({a['name'] for a in data['airports'] if a['cls']=='runway'},{'16R/34L','16L/34R'})
        self.assertEqual(len(data['airports']),3);self.assertEqual(len(data['areas']),2)
        self.assertTrue(all(a['geometry'].geom_type=='LineString' for a in data['airports']))
        self.assertTrue(all(a['geometry'].bounds[0]<0<a['geometry'].bounds[2] for a in data['airports']+data['areas']))
        self.assertEqual(off['airports'],[]);self.assertEqual(off['areas'],[])

    def test_airport_paint_labels_clipping_regions_and_cache_in_flat_and_relief(self):
        recipe=default_recipe();recipe['center']=[0,0];recipe['framing']['scale_denominator']=10000
        recipe['furniture']=dict.fromkeys(recipe['furniture'],False)
        recipe['features'].update(roads='none',buildings='none',shore='none',park_treatment='fill',labels='none',terrain_edges=False)
        recipe['ink_roles']['airports']='red'
        recipe['palette']['inks'][1]['width_mm']=.3
        recipe['palette']['inks'][0]['width_mm']=.7
        def feature(identity,cls,g,name=''):
            return dict(kind='airports',id=identity,cls=cls,geometry=g,name=name)
        data=dict(source={'attribution':'Fixture'},issues=[],roads=[],areas=[
            dict(kind='parks',id='park',name='',geometry=footprint(recipe)),
            feature('apron','apron',shapely.box(100,300,350,550)),
            feature('pavement','runway',shapely.box(700,-800,800,800),'Mapped runway')],airports=[
            feature('west','runway',shapely.LineString([(-500,-5000),(-500,5000)]),'16R/34L'),
            feature('east','runway',shapely.LineString([(500,-800),(500,800)]),'16L/34R'),
            feature('taxiway','taxiway',shapely.LineString([(-500,0),(500,0)]),'A'),
            feature('mapped-center','runway',shapely.LineString([(750,-750),(750,750)]))])
        def elevation(r,domain,*args,**kwargs):
            x0,y0,x1,y1=domain.bounds;xs=np.linspace(x0,x1,9);ys=np.linspace(y0,y1,9)
            return xs,ys,np.full((9,9),100.),{'tiles':[]}
        with tempfile.TemporaryDirectory() as directory,patch('cartographer.compose.dataset',return_value=data),patch('cartographer.compose.elevations',side_effect=elevation),patch('cartographer.compose.ElevationSampler') as sampler:
            sampler.return_value.sample.side_effect=lambda p,z:np.full(len(p),100.)
            sampler.return_value.inputs={};root=Path(directory);number=0
            def render(cache='cache'):
                nonlocal number
                number+=1;out=root/str(number)
                result=compose(recipe,out,quality='draft',cache=root/cache,version='airport-fixture')
                return out,result
            for relief in [False,True]:
                recipe['camera']['relief']=relief
                out,result=render()
                self.assertEqual(result['feature_counts']['airports/runway/casing'],2,'Mapped footprints suppress duplicate centerline symbols')
                self.assertGreater(result['feature_counts']['airports/apron/outline'],0)
                scene=json.loads((out/'scene.json').read_text())
                self.assertTrue(any(a['kind']=='airports' for a in scene['lines']))
                with pymupdf.open(out/'map.pdf') as doc:
                    self.assertFalse(doc[0].get_images())
                    strokes=[d for d in doc[0].get_drawings() if d.get('color')==(1.,0.,0.)]
                    self.assertTrue(strokes)
                    self.assertTrue(all(abs(d['width']-.36*MM)<1e-4 for d in strokes))
                    if not relief:
                        pix=doc[0].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
                        rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
                        scale,offset=framing(recipe)
                        for x,y in [(-500,200),(500,200),(0,0),(200,400),(750,400)]:
                            xp,yp=np.rint((np.array([x,-y])*scale+offset)*2).astype(int)
                            self.assertTrue(np.all(rgb[yp,xp]==255),'Airport pavement must clear the land-cover fill')
                        self.assertTrue(np.all(rgb[:30]==255),'Airport strokes escaped the page viewport')
                recipe['ink_roles']['airports']='black'
                warm,_=render();clean,_=render('clean-'+str(relief))
                self.assertEqual((warm/'map.pdf').read_bytes(),(clean/'map.pdf').read_bytes())
                with pymupdf.open(warm/'map.pdf') as doc:
                    strokes=[d for d in doc[0].get_drawings() if '/casing|' in d.get('layer','')]
                    self.assertTrue(strokes);self.assertTrue(all(abs(d['width']-.76*MM)<1e-4 for d in strokes))
                recipe['ink_roles']['airports']='red'
            recipe['camera']['relief']=False;recipe['features']['labels']='normal'
            out,_=render()
            with pymupdf.open(out/'map.pdf') as doc:
                self.assertIn('16R/34L',doc[0].get_text());self.assertIn('16L/34R',doc[0].get_text())
            recipe['boundary']=snapshot(shapely.geometry.mapping(shapely.box(0,-1,1,1)),'East','Fixture')
            recipe['outside']['features'].update(roads='none',parks=False,buildings='none',shore='none',airports=False)
            out,result=render()
            self.assertFalse(any(k.startswith('outside/airports') for k in result['feature_counts']))
            recipe['outside']['features']['airports']=True;recipe['outside']['ink_roles']['airports']='black'
            out,_=render();intent=json.loads((out/'map.print-intent.json').read_text())
            self.assertTrue(any(b['feature_group'].startswith('outside/airports') and b['ink_id']=='black' for b in intent['bindings']))
            recipe['features']['airports']=False;recipe['outside']['features']['airports']=False
            out,result=render()
            self.assertFalse(any('airports' in k for k in result['feature_counts']))
            with pymupdf.open(out/'map.pdf') as doc:self.assertNotIn('16R/34L',doc[0].get_text())
            recipe['features']['airports']=True;recipe['palette']['inks'][1]['width_mm']=None
            _,result=render();self.assertFalse(result['intent_available'])
            self.assertTrue(any(dict(region='inside',role='airports') in ink['roles'] for ink in result['unmeasured_inks']))


if __name__=='__main__':unittest.main()
