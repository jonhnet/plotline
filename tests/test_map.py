import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import pymupdf
from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'tools'))
from build_map import build, multipolygon, page_layout, validate_style
from pdf_native import extract


class MapTests(unittest.TestCase):
    def test_asymmetric_margins_include_title_and_footer(self):
        style = json.loads((ROOT/'styles/ravenna-letter-hp7585.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p/'map.osm').write_text('<osm version="0.6"><bounds minlon="0" minlat="0" maxlon="0.001" maxlat="0.001"/></osm>')
            meta = build(p/'map.osm', p/'out', style)
            expected = [15, 39, 200.9, 264.4]
            for actual, mm in zip(meta['printable_rect_pt'], expected):
                self.assertAlmostEqual(actual*25.4/72, mm)
            allowed = pymupdf.Rect(meta['printable_rect_pt']) + (-.001, -.001, .001, .001)
            with pymupdf.open(p/'out/map.pdf') as doc:
                self.assertEqual(tuple(doc[0].rect), (0, 0, 612, 792))
                self.assertIn(style['title'], doc[0].get_text())
                self.assertIn('ODbL 1.0', doc[0].get_text())
                for block in doc[0].get_text('blocks'):
                    self.assertTrue(allowed.contains(pymupdf.Rect(block[:4])), block)

    def test_invalid_margins_rejected(self):
        style = json.loads((ROOT/'styles/ravenna-letter-hp7585.json').read_text())
        for margins in [dict(left=-1, top=39, right=15, bottom=15),
                        dict(left=15, top=float('nan'), right=15, bottom=15),
                        dict(left=15, top=39, right=15)]:
            with self.assertRaises(ValueError):
                validate_style(dict(style, margins_mm=margins))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            validate_style(dict(style, margin_pt=36))
        with self.assertRaisesRegex(ValueError, 'no map area'):
            page_layout(dict(style, margins_mm=dict(left=15, top=240, right=15, bottom=15)))

    def test_multipolygon_handles_reversed_segments_and_keeps_holes(self):
        ways = {'1':{'geometry':LineString([(0,0),(10,0),(10,10)])},
                '2':{'geometry':LineString([(0,0),(0,10),(10,10)])},
                '3':{'geometry':LineString([(3,3),(7,3),(7,7),(3,7),(3,3)])}}
        members = [ET.Element('member',type='way',ref=str(i),role='inner' if i==3 else 'outer') for i in range(1,4)]
        geometry, used = multipolygon(members,ways)
        self.assertEqual(used,{'1','2','3'})
        self.assertEqual(geometry.area,84)
        self.assertFalse(geometry.covers(Point(5,5)))
        self.assertEqual(multipolygon(members[:2],ways)[0].area,100)
        with self.assertRaisesRegex(ValueError,'missing multipolygon'):
            multipolygon(members,{'1':ways['1']})

    def test_style_emits_white_cores_black_labels_and_hollow_buildings(self):
        xml = '''<osm version="0.6"><bounds minlon="0" minlat="0" maxlon="0.001" maxlat="0.001"/>
        <node id="1" lon="0.0001" lat="0.0005"/><node id="2" lon="0.0009" lat="0.0005"/>
        <node id="3" lon="0.0002" lat="0.0002"/><node id="4" lon="0.0004" lat="0.0002"/>
        <node id="5" lon="0.0004" lat="0.0004"/><node id="6" lon="0.0002" lat="0.0004"/>
        <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/><tag k="name" v="Test Road"/></way>
        <way id="20"><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="6"/><nd ref="3"/><tag k="building" v="yes"/><tag k="addr:housenumber" v="999"/></way></osm>'''
        style=json.loads((ROOT/'styles/plotter.json').read_text())
        style.update(width_in=10,height_in=10)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'map.osm').write_text(xml)
            meta=build(p/'map.osm',p/'out',style)
            self.assertEqual(meta['issues'],[])
            self.assertGreaterEqual(len(meta['labels']),1)
            data,probe=extract(p/'out/map.pdf')
            sources=data['sources']
            roads=[e for i,e in enumerate(data['events']) if sources.get(i,'').startswith('roads/')]
            self.assertEqual([e['color'] for e in roads],[[0,0,0],[1,1,1]])
            self.assertAlmostEqual(roads[0]['style']['width']-roads[1]['style']['width'],2*style['road_edge_pt'],places=5)
            buildings=[e for i,e in enumerate(data['events']) if sources.get(i,'').startswith('buildings|')]
            self.assertEqual([e['color'] for e in buildings],[[1,1,1],[1,0,0]])
            glyphs=[e for e in data['events'] if e.get('role')=='text']
            self.assertTrue(glyphs)
            self.assertTrue(all(e['color']==[0,0,0] for e in glyphs))
            with pymupdf.open(p/'out/map.pdf') as doc:
                self.assertNotIn('999',doc[0].get_text())
                self.assertIn('Test Road',doc[0].get_text())


if __name__=='__main__':unittest.main()
