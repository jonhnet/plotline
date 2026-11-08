import unittest
import numpy as np
import shapely
from cartographer.mesh_union import mesh_union


class MeshUnionTests(unittest.TestCase):
    def check(self,cells,grid=None):
        expected=shapely.union_all(cells,grid_size=grid);actual=mesh_union(cells,grid)
        self.assertTrue(actual.is_valid)
        self.assertLess(actual.symmetric_difference(expected).area,1e-8)
        return actual

    def test_mesh_holes_point_contacts_disconnected_islands_and_empty(self):
        cells=[shapely.box(x,y,x+1,y+1) for x in range(7) for y in range(7)
               if (x,y) not in [(1,1),(1,2),(2,1),(3,3),(3,4),(4,3),(4,4)]]
        cells.extend([shapely.box(7,7,8,8),shapely.box(10,10,11,11)])
        result=self.check(cells)
        for point in [(1.5,1.5),(3.5,3.5),(9,9)]:self.assertFalse(result.covers(shapely.Point(point)))
        self.check([]);self.check([shapely.Polygon()])

    def test_interpolated_edges_snap_together_and_retain_holes(self):
        cells=[shapely.Polygon([(0,0),(1,.7),(0,2)]),
               shapely.Polygon([(1+1e-10,.7),(0,2),(2,2)])]
        self.check(cells,1e-5)
        self.check([shapely.box(0,0,10,10).difference(shapely.box(2,2,8,8)),shapely.box(4,4,6,6)])

    def test_unexpected_overlap_duplicate_cells_or_unmatched_edges_fall_back(self):
        cases=[[shapely.box(0,0,2,2),shapely.box(1,1,3,3)],
               [shapely.box(0,0,1,1)]*2,[shapely.box(0,0,1,1)]*3,
               [shapely.box(0,0,1,2),shapely.box(1,0,2,1),shapely.box(1,1,2,2)]]
        for cells in cases:
            with self.subTest(cells=cells):self.check(cells)

    def test_conforming_random_mesh_subsets_match_general_union(self):
        rng=np.random.default_rng(58)
        for _ in range(8):
            cells=[]
            for x,y in rng.integers(0,20,(200,2)):
                cells.extend([shapely.Polygon([(x,y),(x+1,y),(x+1,y+1)]),
                              shapely.Polygon([(x,y),(x+1,y+1),(x,y+1)])])
            # Choose unique mesh cells; overlapping features are not a coverage.
            cells=list({g.wkb:g for g in cells}.values());self.check(cells)


if __name__=='__main__':unittest.main()
