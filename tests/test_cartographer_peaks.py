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
from shapely.geometry import mapping

from cartographer.boundaries import snapshot
from cartographer.compose import compose
from cartographer.model import default_recipe, validate_recipe
from cartographer.sources import _dataset, plan


class PeakLabelTests(unittest.TestCase):
    def test_old_recipes_default_off_and_region_choices_are_independent(self):
        old=default_recipe()
        for style in [old,old['outside']]:style['features'].pop('peak_labels')
        before=copy.deepcopy(old);updated=validate_recipe(old)
        self.assertEqual(old,before)
        self.assertFalse(updated['features']['peak_labels'])
        self.assertFalse(updated['outside']['features']['peak_labels'])
        updated['features']['peak_labels']=True
        self.assertTrue(validate_recipe(updated)['features']['peak_labels'])
        self.assertFalse(updated['outside']['features']['peak_labels'])
        updated['features']['peak_labels']='yes'
        with self.assertRaisesRegex(ValueError,'peak_labels must be true or false'):validate_recipe(updated)

    def test_vector_tiles_supply_named_peaks_and_volcanoes_once_at_seams(self):
        recipe=default_recipe();recipe['center']=[0,0]
        domain=shapely.box(-100,-100,100,100)
        before=plan(recipe,domain,'draft');recipe['features']['peak_labels']=True
        self.assertEqual(plan(recipe,domain,'draft'),before,'Label toggle must reuse decoded tiles')
        def tile(x):
            properties=[{'class':'peak','name':'Local name','name:en':'Summit','rank':1},
                        {'class':'volcano','name_en':'Volcano','rank':2},
                        {'class':'saddle','name':'Pass'}, {'class':'ridge','name':'Ridge'},
                        {'class':'cliff','name':'Cliff'}, {'class':'peak'}]
            return mapbox_vector_tile.encode({'name':'mountain_peak','features':[
                {'id':i+1,'geometry':shapely.Point(x,4096),'properties':p} for i,p in enumerate(properties)]},
                default_options={'y_coord_down':True})
        with tempfile.TemporaryDirectory() as directory,patch('cartographer.sources.fetch',side_effect=[(tile(4096),{}),(tile(0),{})]):
            data=_dataset(recipe,domain,Path(directory),dict(tiles=[(1,0,0),(1,1,0)],zoom=1,warnings=[],buildings=False),'draft')
        self.assertEqual([p['name'] for p in data['labels']],['Summit','Volcano'])
        self.assertTrue(all(p['kind']=='peaks' and p['geometry'].equals(shapely.Point(0,0)) for p in data['labels']))
        self.assertEqual([p['rank'] for p in data['labels']],[1,2])

    def test_names_use_label_ink_in_2d_and_3d_and_respect_boundary_styles(self):
        recipe=default_recipe();recipe['center']=[0,0];recipe['framing']['scale_denominator']=18000
        recipe['furniture']=dict.fromkeys(recipe['furniture'],False)
        recipe['features'].update(buildings='none',roads='none',parks=False,shore='none')
        recipe['ink_roles']['labels']='red'
        data=dict(source={'attribution':'Fixture'},issues=[],roads=[],areas=[],labels=[
            dict(name='East Peak',kind='peaks',rank=1,geometry=shapely.Point(500,0)),
            dict(name='West Peak',kind='peaks',rank=2,geometry=shapely.Point(-500,0)),
            dict(name='Town',kind='place',rank=3,geometry=shapely.Point(0,-700))])
        def elevation(recipe,domain,*args,**kwargs):
            x0,y0,x1,y1=domain.bounds;xs=np.linspace(x0,x1,9);ys=np.linspace(y0,y1,9)
            return xs,ys,np.full((9,9),100.),{'tiles':[]}
        with tempfile.TemporaryDirectory() as directory,patch('cartographer.compose.dataset',return_value=data),patch('cartographer.compose.elevations',side_effect=elevation),patch('cartographer.compose.ElevationSampler') as sampler:
            sampler.return_value.sample.side_effect=lambda p,z:np.full(len(p),100.)
            sampler.return_value.inputs={};root=Path(directory);number=0
            def render():
                nonlocal number
                number+=1;out=root/str(number);result=compose(recipe,out,quality='draft',cache=root/'cache',version='peak-fixture')
                with pymupdf.open(out/'map.pdf') as doc:
                    self.assertFalse(doc[0].get_images());text=doc[0].get_text()
                intent=json.loads((out/'map.print-intent.json').read_text())
                return text,intent,result
            text,_,_=render();self.assertIn('Town',text);self.assertNotIn('Peak',text)
            for relief in [False,True]:
                recipe['camera']['relief']=relief;recipe['features']['peak_labels']=True
                text,intent,_=render();self.assertIn('East Peak',text);self.assertIn('West Peak',text)
                peaks=[b for b in intent['bindings'] if b['feature_group']=='labels/peaks']
                self.assertTrue(peaks);self.assertTrue(all(b['ink_id']=='red' for b in peaks))
            recipe['features']['labels']='none'
            text,_,_=render();self.assertEqual(text.strip(),'')
            recipe['features']['labels']='normal';recipe['camera']['relief']=False
            recipe['boundary']=snapshot(mapping(shapely.box(0,-1,1,1)),'East','Fixture')
            recipe['outside']['features'].update(roads='none',parks=False,buildings='none',shore='none')
            text,_,_=render();self.assertIn('East Peak',text);self.assertNotIn('West Peak',text)
            recipe['outside']['features']['peak_labels']=True
            recipe['outside']['ink_roles']['labels']='green'
            text,intent,_=render();self.assertIn('West Peak',text)
            self.assertTrue(any(b['feature_group']=='outside/labels/peaks' and b['ink_id']=='green' for b in intent['bindings']))
            recipe['features']['peak_labels']=False
            text,_,_=render();self.assertNotIn('East Peak',text);self.assertIn('West Peak',text)
