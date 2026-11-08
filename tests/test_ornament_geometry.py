"""Independent GEOS audit of the octagonal tiles, separate from their generator."""
import json
from pathlib import Path
import subprocess
import unittest
from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]


class OctagonalGeometryTest(unittest.TestCase):
    def test_tiles_cover_one_patch_without_overlap_or_internal_holes(self):
        program = """
import {octagonalTiles} from './patterns/web/ornament.js';
const cases=[];
for(const depth of [1,2,3,4,5])for(const patch of [0,1,2]){
 const tiles=[...octagonalTiles({depth,patch})].map(t=>t.points);
 cases.push({depth,patch,tiles});
}console.log(JSON.stringify(cases));
"""
        cases = json.loads(subprocess.check_output(
            ['node', '--input-type=module', '-e', program], cwd=ROOT, text=True))
        for case in cases:
            with self.subTest(depth=case['depth'], patch=case['patch']):
                tiles = [Polygon(points) for points in case['tiles']]
                self.assertGreater(len(tiles), 100)
                self.assertTrue(all(p.is_valid and p.area > 0 for p in tiles))
                patch = unary_union(tiles)
                # Coordinates sharing a lattice vertex are generated identically;
                # no snapping or buffering is used to hide cracks/overlaps.
                self.assertEqual(patch.geom_type, 'Polygon')
                self.assertEqual(len(patch.interiors), 0)
                self.assertAlmostEqual(sum(p.area for p in tiles), patch.area, places=8)


if __name__ == '__main__':
    unittest.main()
