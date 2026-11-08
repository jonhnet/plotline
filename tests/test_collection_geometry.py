"""Independent GEOS audits for pinwheel coverage and finite-width face hatches."""
import json
from pathlib import Path
import subprocess
import unittest
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]

def node(program):
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', program], cwd=ROOT, text=True))

class CollectionGeometryTests(unittest.TestCase):
    def test_pinwheel_integer_tiles_cover_the_exact_patch(self):
        cases = node("""
import {pinwheelTiles} from './patterns/web/pinwheel.js';
console.log(JSON.stringify([1,2,3,4,5].flatMap(depth=>[0,1].map(patch=>({depth,patch,...pinwheelTiles({depth,patch})})))));
""")
        for case in cases:
            with self.subTest(depth=case['depth'], patch=case['patch']):
                d = case['denominator']
                tiles = [Polygon(t) for t in case['tiles']]
                target = Polygon([(0, 0), (2*d, 0), (2*d, d), (0, d)] if case['patch'] else [(0, 0), (2*d, 0), (0, d)])
                union = unary_union(tiles)
                self.assertTrue(all(t.is_valid and t.area > 0 for t in tiles))
                self.assertEqual(union.geom_type, 'Polygon')
                self.assertEqual(len(union.interiors), 0)
                self.assertEqual(sum(t.area for t in tiles), target.area)
                self.assertEqual(union.symmetric_difference(target).area, 0)

    def test_hatch_capsules_stay_in_their_visible_face(self):
        cases = node("""
import {hatchedSolidGeometry} from './patterns/web/hatched-solids.js';
const finish=it=>{let q;while(!(q=it.next()).done);return q.value;},cases=[];
for(const shape of [0,1,2])for(const azimuth of [5,35,85])for(const elevation of [5,25,75]){
 const g=finish(hatchedSolidGeometry({shape,azimuth,elevation,copies:2,density:40,cross:1,angle:30,inkWidths:[.012,.024,.018]}));cases.push(g.hatches);
}console.log(JSON.stringify(cases));
""")
        for hatches in cases:
            self.assertGreater(len(hatches), 0)
            for h in hatches:
                ink = LineString(h['points']).buffer(h['inset'], quad_segs=12)
                face = Polygon(h['polygon'])
                self.assertLess(ink.difference(face).area, 1e-11)
