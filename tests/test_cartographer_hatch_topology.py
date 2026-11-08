"""Keep a real narrow forest hole after rotation, without dropping hatch rows."""
import math
from pathlib import Path
import unittest

import numpy as np
import shapely
from shapely import affinity

from cartographer.area_geometry import valid_area
from cartographer.compose import hatches
from cartographer.terrain_marks import hatch_rows


class HatchTopologyTests(unittest.TestCase):
    def setUp(self):
        self.area=shapely.from_wkt((Path(__file__).parent/'fixtures/woodland-rotation-hole.wkt').read_text())

    def reference_rows(self,area,spacing,angle):
        # Independent clipping order: rotate each carrier, then intersect the
        # original valid polygon. Never rotate the polygon's narrow hole.
        x0,y0,x1,y1=affinity.rotate(area,-angle,origin=(0,0)).bounds
        rows=[]
        for y in np.arange(math.floor(y0/spacing)*spacing,y1+spacing,spacing):
            carrier=affinity.rotate(shapely.LineString([(x0,y),(x1,y)]),angle,origin=(0,0))
            row=carrier.intersection(area)
            if row.length>1e-7:rows.append(row)
        return shapely.union_all(rows)

    def check_coverage(self,actual,expected,area):
        self.assertLess(actual.hausdorff_distance(expected),1e-7)
        self.assertAlmostEqual(actual.length,expected.length,places=6)
        self.assertLess(actual.difference(area.buffer(1e-7)).length,1e-7)
        for hole in area.interiors:
            self.assertLess(actual.intersection(shapely.Polygon(hole).buffer(-1e-7)).length,1e-7)

    def test_rotation_repair_preserves_filled_area_and_hole(self):
        self.assertTrue(self.area.is_valid)
        rotated=affinity.rotate(self.area,45,origin=(0,0))
        self.assertFalse(rotated.is_valid,'Fixture must retain the roundoff crossing')
        repaired=valid_area(rotated)
        self.assertTrue(repaired.is_valid)
        self.assertAlmostEqual(repaired.area,rotated.area,places=6)
        restored=affinity.rotate(repaired,-45,origin=(0,0))
        self.assertLess(restored.symmetric_difference(self.area).area,1e-5)
        self.assertIs(valid_area(self.area),self.area,'Already valid areas keep their coordinates')

    def test_crosshatches_match_carriers_clipped_in_original_coordinates(self):
        expected=shapely.union_all([self.reference_rows(self.area,1000,a) for a in [-45,45]])
        self.check_coverage(hatches(self.area,1000),expected,self.area)

    def test_hillshade_repairs_before_whole_area_and_stripe_clipping(self):
        # Added holes exercise the stripe optimization too; retain the exact
        # failing ring rather than re-sampling it to manufacture more vertices.
        extra=shapely.Point(-121000,211000).buffer(100,quad_segs=80)
        dense=shapely.Polygon(self.area.exterior,holes=[*self.area.interiors,extra.exterior])
        for area in [self.area,dense]:
            self.assertTrue(area.is_valid)
            for angle in [-45,45]:
                actual=shapely.union_all(list(hatch_rows(area,500,angle)))
                self.check_coverage(actual,self.reference_rows(area,500,angle),area)


if __name__=='__main__':unittest.main()
