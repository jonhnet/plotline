"""Independent full-PDF/decoded-HPGL checks for the image boundary."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pymupdf
from jsonschema import ValidationError
from shapely.geometry import Point,LineString,box
from shapely.ops import unary_union

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from pdf_driver import DEFAULT_POLICIES, resolve_intent, convert, PolicyDevice, PT
from pdf_native import extract
from test_pdf_driver import decoded_mm


class PdfRasterTests(unittest.TestCase):
    def test_pdf_worker_does_not_initialize_unused_ml_layout(self):
        # A fresh interpreter exercises import order; importing PyMuPDF first
        # in this test process would already have initialized its optional ML.
        code = "import sys; sys.path.insert(0, 'tools'); import pdf_driver; assert 'onnxruntime' not in sys.modules; assert pdf_driver.pymupdf._get_layout is None"
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('onnxruntime', result.stderr)

    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        subprocess.run(['/usr/bin/python3','-m','raster_lab.pdf_fixture','--output',str(cls.root/'fixture'),'--pdf-python',sys.executable],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        cls.pdf=cls.root/'fixture/mixed-raster-vector.pdf'
        inks=[dict(id=r.lower(),label=r,color_srgb=c,width_mm=w) for r,c,w in [('C','#009f7c',.25),('M','#e32991',.3),('Y','#efcf00',.35),('K','#000000',.25)]]
        cls.intent=dict(schema='plotline.print-intent/1',title='Raster fixture',source_sha256=hashlib.sha256(cls.pdf.read_bytes()).hexdigest(),
            palette=dict(schema='plotline.palette/1',id='pens',label='Test pens',inks=inks),
            pages=[dict(page_index=0,source_size_mm=[160,120],placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]))],
            policies={**DEFAULT_POLICIES,'raster':'dither','raster_settings':dict(screening=True,palette='cmy',fullness=1,edge_falloff_mm=6.35,black_treatment='lines',inks={r:r.lower() for r in 'CMYK'})})
        cls.intent=resolve_intent(cls.pdf,cls.intent)
        cls.output=cls.root/'converted';cls.output.mkdir()
        cls.result=convert(cls.pdf,cls.intent,cls.output,ROOT/'target/release/plotline',artifacts='full')
        cls.page=cls.output/'page-0'
        cls.events=json.loads((cls.page/'events.json').read_text())
        cls.report=json.loads((cls.page/'report.json').read_text())
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_unselected_image_policy_explains_the_required_action_once(self):
        intent=copy.deepcopy(self.intent)
        intent['policies']['raster']='reject'
        del intent['policies']['raster_settings']
        output=self.root/'policy-decision';output.mkdir()
        with self.assertRaises(NotImplementedError) as caught:
            convert(self.pdf,intent,output,ROOT/'target/release/plotline')
        message=str(caught.exception)
        self.assertIn('Require a decision',message)
        self.assertIn('Images & effects',message)
        self.assertIn('Draw with pens',message)
        self.assertIn('assign channel pens',message)
        self.assertEqual(message.count('raster image:'),1)  # Reused images do not repeat instructions.
        self.assertFalse(message.endswith(': '))
        self.assertFalse((output/'page-0/plot.hpgl').exists())

    def test_actual_callback_pixels_are_reused_with_distinct_transforms(self):
        device=PolicyDevice(self.intent['policies'],{},1)
        events,_=extract(self.pdf,device_factory=lambda:device)
        images=[e for e in events['events'] if e['kind']=='image']
        self.assertEqual(len(images),2);self.assertEqual(len(device.images),1)
        self.assertNotEqual(images[0]['matrix'],images[1]['matrix'])
        self.assertTrue(next(iter(device.images.values())).startswith(b'P6\n128 128\n255\n'))

    def test_decoded_footprints_respect_circle_later_white_and_page(self):
        paths=decoded_mm(self.page/'plot.hpgl',120)
        ink=unary_union([LineString(points).buffer(self.intent['palette']['inks'][pen-1]['width_mm']/2,resolution=32) for pen,points in paths])
        # Original white image pixels hide earlier vector ink even with sparse screens.
        for x,y in [(30,35),(45,35),(115,30)]:
            self.assertFalse(ink.intersects(Point(x,y).buffer(.1)),(x,y))
        self.assertTrue(ink.intersects(Point(10,35).buffer(.3)))
        self.assertTrue(ink.intersects(Point(20,35).buffer(.3)))
        # The converter independently checks finite-width footprints after HPGL quantization.
        for row in self.report['decoded_hpgl']:
            self.assertLessEqual(row['outside_allowed_area_pt2'],1e-7)
        self.assertTrue(any(e['kind']=='fill' and e.get('role')=='text' for e in self.events['events']))
        self.assertEqual(self.result['pages'][0]['raster_images'][0]['role_inks']['C'],'c')
        self.assertTrue(any(name.endswith('/C') for name in self.result['pages'][0]['by_feature']))

    def test_widths_and_generated_channel_overlaps_survive(self):
        self.assertEqual([round(i['width']/PT,3) for i in self.events['inks']],[.25,.3,.35,.25])
        by_id={r['id']:r for r in self.report['decoded_hpgl']}
        for i,e in enumerate(self.events['events']):
            if e['kind']=='raster_paths':
                self.assertGreater(by_id[i]['paths'],0)
                self.assertEqual(by_id[i]['representation'],'raster_paths')
                self.assertEqual(by_id[i]['missing_detail_bounds'] if 'missing_detail_bounds' in by_id[i] else [],[])

    def test_validation_does_not_guess_missing_or_inactive_materials(self):
        bad=copy.deepcopy(self.intent);bad['policies']['raster_settings']['inks']['C']='missing'
        with self.assertRaisesRegex(ValueError,'undefined document ink'):resolve_intent(self.pdf,bad)
        bad=copy.deepcopy(self.intent);bad['policies']['raster_settings'].update(screening=False,black_treatment='off')
        with self.assertRaisesRegex(ValueError,'Enable screening'):resolve_intent(self.pdf,bad)
        bad=copy.deepcopy(self.intent);del bad['policies']['raster_settings']['inks']['M']
        with self.assertRaisesRegex(ValueError,'Images & effects.*Magenta channel pen'):resolve_intent(self.pdf,bad)
        bad=copy.deepcopy(self.intent);bad['policies']['raster_settings'].update(palette='selected',screen_inks=['c','k'],inks={})
        with self.assertRaisesRegex(ValueError,'Black treatment pen or set Black treatment to Off'):
            resolve_intent(self.pdf,bad)
        # Channel role K accepts any document ID/label; it never looks up a pen name.
        good=copy.deepcopy(bad);black=good['palette']['inks'][-1];black.update(id='shoebox-pen',label='Old reliable')
        good['policies']['raster_settings'].update(screen_inks=['c','shoebox-pen'],inks={'K':'shoebox-pen'})
        resolve_intent(self.pdf,good)
        # Ink-only requires only the selected K, regardless of saved screen palette.
        good=copy.deepcopy(self.intent);good['policies']['raster_settings'].update(screening=False,black_treatment='shapes',inks={'K':'k'})
        resolve_intent(self.pdf,good)

    def test_placement_regenerates_at_scale_and_applies_printable_clip(self):
        intent=copy.deepcopy(self.intent);p=intent['pages'][0]
        p['placement']=dict(scale=.5,rotation_deg=90,offset_mm=[10,5])
        p['target_media'].update(sheet_size_mm=[80,100],printable_rect_mm=[15,10,55,80])
        output=self.root/'scaled';output.mkdir()
        result=convert(self.pdf,resolve_intent(self.pdf,intent),output,ROOT/'target/release/plotline')
        images=result['pages'][0]['raster_images']
        np.testing.assert_allclose(images[0]['placed_size_mm'],[25,25],atol=1e-5)
        np.testing.assert_allclose(images[1]['placed_size_mm'],[20,20],atol=1e-5)
        self.assertNotEqual(images[0]['screen_paths_by_ink'],self.result['pages'][0]['raster_images'][0]['screen_paths_by_ink'])
        for pen,points in decoded_mm(output/'page-0/plot.hpgl',100):
            footprint=LineString(points).buffer(intent['palette']['inks'][pen-1]['width_mm']/2)
            self.assertLess(footprint.difference(box(15,10,70,90)).area,1e-7)

    def test_selected_palette_validates_and_survives_pdf_clipping(self):
        intent=copy.deepcopy(self.intent)
        intent['palette']['inks'].append(dict(id='ocean',label='Blue',color_srgb='#245bd6',width_mm=.45))
        settings=intent['policies']['raster_settings']
        settings.update(palette='selected',screen_inks=['c','ocean','k'],black_treatment='off',inks={})
        for ids in [[],['ocean','ocean'],['absent'],['c']*9]:
            bad=copy.deepcopy(intent);bad['policies']['raster_settings']['screen_inks']=ids
            with self.assertRaises((ValueError,ValidationError)):
                resolve_intent(self.pdf,bad)
        output=self.root/'selected';output.mkdir()
        result=convert(self.pdf,resolve_intent(self.pdf,intent),output,ROOT/'target/release/plotline',artifacts='full')
        for report in result['pages'][0]['raster_images']:
            self.assertEqual(report['screen_palette'],'selected')
            self.assertEqual(report['separation']['color_space'],'oklab')
            self.assertEqual(set(report['role_inks'].values()), {'c','ocean','k'})
        events=json.loads((output/'page-0/events.json').read_text())
        for index,event in enumerate(events['events']):
            if event['kind']=='raster_paths':self.assertIn(events['paint_inks'][str(index)],[1,5,4])
        audit=json.loads((output/'page-0/report.json').read_text())
        for row in audit['decoded_hpgl']:
            self.assertLessEqual(row['outside_allowed_area_pt2'],1e-7)

if __name__=='__main__':unittest.main()
