import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf
import shapely

from cartographer.compose import compose, InkDrawing
from cartographer.furniture import compass_geometry, furniture_plan, furniture_occupied
from cartographer.geography import footprint, projection, framing
from cartographer.model import MM, default_recipe, layout, migrate, validate_recipe
from pdf_native import extract


class FurnitureTests(unittest.TestCase):
    def test_old_recipes_gain_optional_furniture_without_changing_the_saved_input(self):
        recipe=default_recipe();recipe.pop('furniture');before=copy.deepcopy(recipe)
        validate_recipe(recipe)
        result=migrate(recipe);validate_recipe(result)
        self.assertEqual(recipe,before);self.assertFalse(any(result['furniture'].values()))
        result['furniture']['title']='yes'
        with self.assertRaises(ValueError):validate_recipe(result)

    def test_rose_tracks_projected_north_and_fits_at_every_camera_heading(self):
        recipe=default_recipe();frame=shapely.box(*(np.array(layout(recipe)['content_mm'])*MM))
        for relief in [False,True]:
            recipe['camera']['relief']=relief;recipe['camera']['elevation_deg']=13
            for azimuth in range(0,360,15):
                recipe['camera']['azimuth_deg']=azimuth;rose=compass_geometry(recipe)
                self.assertTrue(frame.covers(rose['backing']))
                north=projection(recipe)[:,1];north/=np.linalg.norm(north)
                tip=np.array(rose['outlines'][0].exterior.coords[2])
                np.testing.assert_allclose((tip-rose['center'])/(10*MM),north,atol=1e-12)
                self.assertGreater(rose['outlines'][0].area,rose['outlines'][2].area)

    def test_full_viewport_and_vector_knockouts_with_no_required_printed_furniture(self):
        recipe=default_recipe();recipe['center']=[0,0];recipe['title']='Cascades'
        recipe['furniture']=dict.fromkeys(recipe['furniture'],False)
        recipe['features'].update(labels='none',roads='none',buildings='none',park_treatment='fill',shore='none')
        data=dict(source={'attribution':'Fixture map credit'},issues=[],roads=[],labels=[],
                  areas=[dict(kind='parks',id='forest',name='',geometry=footprint(recipe))])
        with tempfile.TemporaryDirectory() as directory,patch('cartographer.compose.dataset',return_value=data):
            root=Path(directory);bare=compose(recipe,root/'bare',quality='draft',version='furniture-fixture')
            self.assertEqual(bare['layout']['frame_mm'],bare['layout']['content_mm'])
            with pymupdf.open(root/'bare/map.pdf') as document:
                self.assertEqual(document[0].get_text(),'')
                self.assertEqual(document.metadata['title'],'Cascades')
                self.assertIn('Fixture map credit',document.metadata['subject'])
                pix=document[0].get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False)
                background=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
            recipe['furniture'].update(title=True,compass=True)
            recipe['features']['labels']='normal'
            draw=InkDrawing(recipe);plan=furniture_plan(draw,recipe,'Fixture map credit',False)
            occupied=furniture_occupied(draw,plan);draw.doc.close()
            scale,offset=framing(recipe)
            for index,region in enumerate(occupied):
                page_point=np.array(region.centroid.coords[0])
                p=(page_point-offset)@np.linalg.inv(projection(recipe)[:,:2]).T/scale
                data['labels'].append(dict(name=f'Place {index}',kind='place',rank=index,geometry=shapely.Point(p)))
            ornament=compose(recipe,root/'ornament',quality='draft',version='furniture-fixture')
            labels=json.loads((root/'ornament/provenance.json').read_text())['labels']
            self.assertTrue(labels)
            for label in labels:
                self.assertFalse(any(shapely.geometry.shape(label['background']).intersects(area) for area in occupied),
                                 'Geographic labels must avoid enabled furniture')
            self.assertEqual(bare['layout'],ornament['layout'])
            with pymupdf.open(root/'ornament/map.pdf') as document:
                self.assertIn('Cascades',document[0].get_text())
                self.assertNotIn('Plotline',document[0].get_text())
                self.assertFalse(document[0].get_images())
                pix=document[0].get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False)
                rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
                title=document[0].search_for('Cascades')[0]+(-3,-3,3,3)
            x0,y0,x1,y1=np.array(layout(recipe)['content_mm'])*MM*3
            self.assertTrue(np.all(background[int(y0)+3:int(y1)-3,int(x0)+3:int(x1)-3,1]==128),
                            'Map must cover the formerly reserved title and footer bands')
            # The title clears its glyph halo, retaining map between the letters.
            a,b,c,d=np.rint(np.array(title)*3).astype(int);crop=rgb[b:d,a:c]
            self.assertGreater(np.count_nonzero(np.all(crop==255,axis=2)),100)
            self.assertGreater(np.count_nonzero(crop[:,:,1]==128),100)
            self.assertGreater(np.count_nonzero(np.all(crop<16,axis=2)),100)
            rose=compass_geometry(recipe)
            a,b,c,d=np.rint(np.array(rose['backing'].bounds)*3).astype(int)
            xx,yy=np.meshgrid(np.arange(a,c),np.arange(b,d))
            inside=shapely.contains_xy(rose['backing'].buffer(-.2*MM),xx/3,yy/3)
            colors=rgb[b:d,a:c][inside]
            self.assertTrue(np.all(colors[:,0]==colors[:,1]),'Rose backing must clear green map ink, including behind N')
            events,_=extract(root/'ornament/map.pdf')
            self.assertFalse([e for e in events['events'] if e['kind']=='unsupported'])
            self.assertTrue(any(e.get('color')==[1,1,1] for e in events['events']),
                            'White knockout paint must survive native PDF extraction')
            recipe['furniture']=dict.fromkeys(recipe['furniture'],True)
            compose(recipe,root/'all',quality='draft',version='furniture-fixture')
            with pymupdf.open(root/'all/map.pdf') as document:
                self.assertIn('Plotline',document[0].get_text());self.assertIn('Fixture map credit',document[0].get_text())
