import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'tools'))
from make_boundary_fixture import make_fixture
from print_contract import capabilities, device_point, placed_point, validate_intent, validate_media
from print_sink.capture import inspect_pdf, capture, clean_spool


class PrintContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.intent = make_fixture(cls.directory)
        cls.pdf = cls.directory/'boundary.pdf'
        cls.media = cls.intent['pages'][0]['target_media']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_fixture_keeps_page_size_vectors_text_and_distinct_black_inks(self):
        report = inspect_pdf(self.pdf)
        self.assertEqual(len(report['pages']),2)
        for page in report['pages']:
            self.assertEqual(page['classification'],'vector/text')
            self.assertEqual(page['image_placements'],0)
            self.assertGreater(page['glyphs'],50)
            self.assertGreater(page['vector_paints'],10)
        self.assertAlmostEqual(report['pages'][1]['size_mm'][0],558.8,places=3)
        black = [i for i in self.intent['palette']['inks'] if i['color_srgb']=='#000000']
        self.assertEqual([i['width_mm'] for i in black],[.15,.6])

    def test_page_placement_uses_paper_origin_and_preserves_overflow(self):
        page = self.intent['pages'][0]
        start = placed_point((60,35),page['source_size_mm'],page['placement'])
        end = placed_point((160,35),page['source_size_mm'],page['placement'])
        self.assertEqual(start,(60,35))
        self.assertEqual(device_point(start,self.media),(-6716,4112))
        self.assertEqual(device_point(end,self.media),(-2716,4112))
        page = self.intent['pages'][1]
        outside = placed_point((500,300),page['source_size_mm'],page['placement'])
        self.assertEqual(outside,(500,300))
        self.assertEqual(device_point(outside,self.media),(10884,-6488))

    def test_quarter_turn_rotates_whole_page_before_scale_and_offset(self):
        expected = [(25,47),(365,27),(185,367),(45,187)]
        for angle, point in zip([0,90,180,270],expected):
            actual = placed_point((10,20),(100,200),dict(scale=2,rotation_deg=angle,offset_mm=[5,7]))
            self.assertEqual(actual,point)

    def test_contract_rejects_stale_source_unknown_ink_and_invalid_media(self):
        for edit in [lambda i:i.update(source_sha256='0'*64),
                     lambda i:i['bindings'][0].update(ink_id='missing'),
                     lambda i:i['palette']['inks'][0].update(width_mm=None),
                     lambda i:i['pages'][0].update(source_size_mm=[100,200]),
                     lambda i:i['pages'][0]['target_media'].update(printable_rect_mm=[0,0,500,500])]:
            bad=copy.deepcopy(self.intent); edit(bad)
            with self.assertRaises((ValueError,ValidationError)):
                validate_intent(bad,self.pdf)
        bad=copy.deepcopy(self.media)
        bad['calibration']['sheet_to_device_units'][0]=41
        with self.assertRaises(ValueError): validate_media(bad)

    def test_inventory_is_read_only_provenance_and_unknown_width_is_not_invented(self):
        inventory=dict(revision=7,slots={
            '2':dict(label='red',state='loaded',color='#ff0000',width_mm=.3),
            '4':dict(label='pink',state='loaded',color='#ff1493',width_mm=None),
            '1':dict(label='dry',state='unavailable',color='#000000',width_mm=.2)})
        original=copy.deepcopy(inventory)
        result=capabilities(inventory,[self.media])
        self.assertEqual(inventory,original)
        self.assertEqual(len(result['available_inks']),2)
        self.assertIsNone(result['available_inks'][1]['width_mm'])
        self.assertEqual(result['available_inks'][0]['source']['slot'],2)
        self.assertFalse(result['implemented']['per_ink_widths'])

    def test_explicit_carousel_link_preserves_frozen_conversion_material(self):
        intent = copy.deepcopy(self.intent)
        ink = intent['palette']['inks'][0]
        material = (ink['color_srgb'], ink['width_mm'])
        for kind in ('squidprint-carousel', 'plotline-carousel'):
            ink['source'] = dict(kind=kind, slot=2, revision=7, follow_slot=True)
            validate_intent(intent, self.pdf)
        ink['measured_color_srgb'] = '#e4a597'
        validate_intent(intent, self.pdf)
        self.assertEqual((ink['color_srgb'], ink['width_mm']), material)
        self.assertEqual(ink['measured_color_srgb'], '#e4a597')
        ink['measured_color_srgb'] = None
        validate_intent(intent, self.pdf)
        ink['measured_color_srgb'] = 'bad color'
        with self.assertRaises(ValidationError):validate_intent(intent, self.pdf)
        del ink['measured_color_srgb']
        ink['source']['follow_slot'] = 'yes'
        with self.assertRaises(ValidationError):
            validate_intent(intent, self.pdf)

    def test_sheet_half_turn_keeps_limits_and_validates_its_calibration(self):
        rotated=copy.deepcopy(self.media);rotated['orientation_deg']=180
        with self.assertRaises(ValueError):validate_media(rotated)
        w,h=rotated['sheet_size_mm'];x,y,rw,rh=rotated['printable_rect_mm']
        rotated['printable_rect_mm']=[w-x-rw,h-y-rh,rw,rh]
        a,b,c,d,e,f=rotated['calibration']['sheet_to_device_units']
        rotated['calibration']['sheet_to_device_units']=[-a,-b,-c,-d,a*w+c*h+e,b*w+d*h+f]
        validate_media(rotated)
        self.assertEqual(rotated['calibration']['hard_clip_units'],self.media['calibration']['hard_clip_units'])
        before=device_point([60,35],self.media);after=device_point([w-60,h-35],rotated)
        for actual,want in zip(after,before):self.assertAlmostEqual(actual,want)
        rotated['orientation_deg']=45
        with self.assertRaises(ValidationError):validate_media(rotated)

    def test_capture_keeps_exact_bytes_and_reports_mixed_content(self):
        pdf=self.directory/'policy-probes.pdf'
        with tempfile.TemporaryDirectory() as target:
            directory,result=capture(pdf,target,{'CONTENT_TYPE':'application/pdf','SECRET_TEST':'do not retain'})
            self.assertEqual((directory/'source.pdf').read_bytes(),pdf.read_bytes())
            self.assertNotIn('SECRET_TEST',json.dumps(result))
            page=result['inspection']['pages'][0]
            self.assertEqual(page['classification'],'mixed')
            self.assertEqual(page['image_placements'],1)
            self.assertIn('fill-shade',page['paint_kinds'])

    def test_spool_drops_only_verified_duplicates_after_capture(self):
        with tempfile.TemporaryDirectory() as target:
            state=Path(target);spool=state/'spool/session-test';spool.mkdir(parents=True)
            pdf=spool/'1-test.pdf';original=self.pdf.read_bytes();pdf.write_bytes(original)
            directory,_=capture(pdf,state/'captures',{})
            self.assertFalse(pdf.exists())
            self.assertEqual((directory/'source.pdf').read_bytes(),original)
            duplicate=spool/'2-old.pdf';duplicate.write_bytes(original)
            unmatched=spool/'3-unknown.pdf';unmatched.write_bytes(b'unique unmatched input')
            clean_spool(state)
            self.assertFalse(duplicate.exists())
            self.assertTrue(unmatched.exists())
            duplicate.write_bytes(original)
            (directory/'source.pdf').write_bytes(b'damaged capture')
            clean_spool(state)
            self.assertTrue(duplicate.exists(),'Do not delete the only good copy')


if __name__ == '__main__':
    unittest.main()
