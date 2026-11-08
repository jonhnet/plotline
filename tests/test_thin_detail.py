"""Real font outlines, independent decoded-ink audit and diagnostic registration."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from shapely.geometry import LineString,box

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from pdf_driver import convert,resolve_intent,ROOT,PT
from make_thin_fixture import make_fixture
from test_pdf_driver import decoded_mm


class ThinDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.intent=make_fixture(cls.root);cls.pdf=cls.root/'thin-labels.pdf';cls.results={}
        for tolerance in [0,1]:
            intent=copy.deepcopy(cls.intent);intent['policies']['thin_detail_thickening']=tolerance
            output=cls.root/str(tolerance);output.mkdir()
            cls.results[tolerance]=convert(cls.pdf,resolve_intent(cls.pdf,intent),output,ROOT/'target/release/plotline',artifacts='full')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_real_lettering_recovers_visible_glyphs_without_changing_logical_paint(self):
        rows={t:json.loads((self.root/str(t)/'page-0/report.json').read_text())['decoded_hpgl'] for t in [0,1]}
        labels={t:[p for p in rows[t] if p['source'].startswith('labels/') and not p['white']] for t in rows}
        represented={t:sum(p['paths']>0 for p in labels[t]) for t in labels}
        self.assertGreater(represented[1],represented[0]+10)
        self.assertGreaterEqual(represented[1],len(labels[1])*.9)
        self.assertLess(self.results[1]['pages'][0]['detail_loss']['affected_paints'],self.results[0]['pages'][0]['detail_loss']['affected_paints'])
        for name in ['coverage.svg','source.svg','logical.svg']:
            self.assertEqual((self.root/'0/page-0'/name).read_bytes(),(self.root/'1/page-0'/name).read_bytes())
        self.assertTrue(any(p['outside_visible_area_pt2']>.01 for p in labels[1]))
        self.assertTrue(all(p['outside_allowed_area_pt2']<=1e-7 for p in rows[1]))

    def test_independent_readback_cannot_enter_white_cuts_or_leave_printable_area(self):
        printable=box(10/PT,10/PT,350/PT,210/PT).buffer(1e-5)
        white=box(180/PT,115/PT,200/PT,145/PT)
        paths=decoded_mm(self.root/'1/page-0/plot.hpgl',220/PT)
        self.assertTrue(paths)
        for _,points in paths:
            ink=LineString(points).buffer(.25,quad_segs=64)
            self.assertTrue(printable.covers(ink));self.assertLess(ink.intersection(white).area,1e-7)

    def test_overlay_is_transparent_registered_and_separate_from_exported_art(self):
        for t in [0,1]:
            output=self.root/str(t)/'page-0'
            overlay=ET.parse(output/'loss.svg').getroot()
            for name in ['source.svg','logical.svg','pen.svg']:
                self.assertEqual(overlay.attrib['viewBox'],ET.parse(output/name).getroot().attrib['viewBox'])
                self.assertNotIn('lost-detail-stripes',(output/name).read_text())
            self.assertNotIn('lost-detail-stripes',(output/'plot.hpgl').read_text())
            self.assertFalse(overlay.findall('{http://www.w3.org/2000/svg}rect'),'No opaque page rectangle in overlay')
            self.assertEqual(overlay.find('{http://www.w3.org/2000/svg}path').attrib['fill'],'white')


if __name__=='__main__':unittest.main()
