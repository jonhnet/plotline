"""Forest hatch acceleration preserves the old geometric ink coverage."""
import math
import unittest

import numpy as np
import shapely
from shapely import affinity

from cartographer.compose import hatches
from build_map import parts


def whole_area_rows(area,spacing):
    """Original full-polygon clipping, without strip selection or row helpers."""
    rows=[]
    for angle in [-45,45]:
        rotated=affinity.rotate(area,-angle,origin=(0,0))
        assert rotated.is_valid
        x0,y0,x1,y1=rotated.bounds
        for y in np.arange(math.floor(y0/spacing)*spacing,y1+spacing,spacing):
            clipped=shapely.LineString([(x0,y),(x1,y)]).intersection(rotated)
            rows.extend(parts(affinity.rotate(clipped,angle,origin=(0,0)),'LineString'))
    return rows


class CrosshatchTests(unittest.TestCase):
    def test_crossings_keep_complete_strokes_and_same_round_pen_footprints(self):
        area=shapely.box(-20,-20,20,20)
        rows=whole_area_rows(area,2)
        old=shapely.union_all(rows);new=hatches(area,2)
        self.assertEqual(len(new.geoms),len(rows))
        self.assertTrue(all(len(line.coords)==2 for line in new.geoms))
        self.assertGreater(len(old.geoms),len(new.geoms)*3)
        self.assertLess(new.hausdorff_distance(old),1e-10)
        self.assertAlmostEqual(new.length,old.length,places=9)
        for diameter in [.3,1.2]:
            a,b=new.buffer(diameter/2),old.buffer(diameter/2)
            # Many near-coincident buffer edges accumulate tiny overlay areas;
            # check both their total area and maximum boundary displacement.
            self.assertLess(a.symmetric_difference(b).area,1e-6)
            self.assertLess(a.boundary.hausdorff_distance(b.boundary),1e-8)

    def test_strips_preserve_global_phase_holes_islands_and_continuity(self):
        area=shapely.box(-100,-80,120,160).difference(shapely.union_all([
            shapely.Point(x,y).buffer(2.7,quad_segs=8)
            for x in range(-90,110,13) for y in range(-70,150,17)]))
        area=shapely.MultiPolygon([area,shapely.box(140,-9,156,83)])
        for spacing in [1.7,6.3]:
            rows=whole_area_rows(area,spacing);expected=shapely.MultiLineString(rows)
            actual=hatches(area,spacing)
            self.assertEqual(len(actual.geoms),len(rows),'Strips must not split a carrier')
            self.assertLess(actual.hausdorff_distance(expected),1e-9)
            np.testing.assert_allclose(shapely.length(actual.geoms),shapely.length(rows),atol=1e-9,rtol=0)
            self.assertLess(actual.difference(area.buffer(1e-8)).length,1e-8)


if __name__=='__main__':unittest.main()
