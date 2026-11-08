import unittest

import cv2
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box

from raster_lab import engine as e, ink_fit, lettering


class InkFitTests(unittest.TestCase):
    def test_pixel_cells_keep_exact_area_and_white_holes(self):
        mask=np.zeros((20,20),bool)
        mask[2:18,2:18]=True;mask[6:14,6:14]=False
        mask[0,0]=True
        d=e.Drawing(4,2,.075,{})
        shape=ink_fit.pixel_shape(mask,d)
        self.assertAlmostEqual(shape.area,mask.sum()*.02)
        self.assertTrue(shape.covers(box(0,0,.2,.1)))
        self.assertFalse(shape.covers(Point(2,1)))

    def test_smoothing_removes_stair_steps_and_keeps_endpoints(self):
        points=[[.5+i*.1,1+(.04 if i%2 else 0)] for i in range(51)]
        d=e.Drawing(6,2,.075,{})
        paths,changed=ink_fit.smooth_paths([points],box(0,0,6,2),d,.12)
        self.assertEqual(changed,1)
        self.assertEqual(paths[0][0],points[0]);self.assertEqual(paths[0][-1],points[-1])
        self.assertLess(len(paths[0]),len(points)/3)
        self.assertLessEqual(shapely.hausdorff_distance(LineString(paths[0]),LineString(points),densify=.1),.121)

    def test_fits_do_not_cut_across_white_or_move_junctions(self):
        paths=[[[1,1],[1,2],[1,3],[2,3],[3,3]],[[1,1],[2,1]]]
        d=e.Drawing(4,4,.075,{})
        ink=shapely.union_all([LineString(p).buffer(.06,quad_segs=64) for p in paths])
        fitted,_=ink_fit.smooth_paths(paths,ink,d,.4)
        for old,new in zip(paths,fitted):
            self.assertEqual(new[0],old[0]);self.assertEqual(new[-1],old[-1])
            self.assertLess(LineString(new).buffer(d.pen_mm/2,quad_segs=64).difference(ink).area,1e-9)
        self.assertFalse(shapely.union_all([LineString(p).buffer(d.pen_mm/2,quad_segs=64) for p in fitted]).covers(Point(1.2,2.8)))

    def test_tiny_loops_dots_and_short_arms_are_unchanged(self):
        paths=[[[1,1],[1.1,1],[1.1,1.1],[1,1.1],[1,1]],[[2,2],[2.025,2]],[[2,2],[2.1,2],[2.1,2.1]]]
        d=e.Drawing(4,4,.075,{})
        fitted,changed=ink_fit.smooth_paths(paths,box(0,0,4,4),d,.12)
        self.assertEqual(fitted,paths);self.assertEqual(changed,0)

    def test_large_loop_remains_closed_and_does_not_collapse(self):
        theta=np.linspace(0,2*np.pi,60)
        p=np.c_[2+np.cos(theta),2+np.sin(theta)].tolist();p[-1]=p[0]
        d=e.Drawing(4,4,.075,{})
        fitted,_=ink_fit.smooth_paths([p],box(0,0,4,4),d,.12)
        line=LineString(fitted[0])
        self.assertTrue(line.is_ring)
        self.assertGreater(Polygon(line).area,.75*Polygon(p).area)

    def test_weight_passes_add_coverage_stay_inside_and_overlap(self):
        d=e.Drawing(6,2,.075,{})
        paths=[[[.5,1],[5.5,1]]]
        ink=box(.4,.7,5.6,1.3)
        extra=ink_fit.weight_paths(paths,ink,d,.4)
        self.assertGreater(len(extra),2)
        strokes=[LineString(p).buffer(d.pen_mm/2,quad_segs=64) for p in paths+extra]
        for stroke in strokes:self.assertLess(stroke.difference(ink).area,1e-9)
        painted=shapely.union_all(strokes)
        self.assertEqual(painted.geom_type,'Polygon')
        self.assertGreater(painted.area/strokes[0].area,6)
        self.assertGreater(painted.area/ink.area,.85)

    def test_weight_passes_cannot_jump_to_neighbor_across_white(self):
        d=e.Drawing(6,2,.075,{})
        paths=[[[.5,.9],[5.5,.9]]]
        ink=shapely.union_all([box(.4,.7,5.6,1),box(.4,1.05,5.6,1.5)])
        extra=ink_fit.weight_paths(paths,ink,d,.6)
        self.assertTrue(extra)
        self.assertTrue(all(y<1 for p in extra for x,y in p))

    def test_blank_ink_is_empty(self):
        d=e.Drawing(2,2,.075,{})
        self.assertEqual(ink_fit.fit([],np.zeros((20,20),bool),d,dict(fit_tolerance_mm=.12,recover_weight=True,weight_max_radius_mm=.4)),[])

    def test_spatial_tiles_do_not_cut_long_passes_at_seams(self):
        d=e.Drawing(20,2,.075,{})
        ink=box(.4,.7,19.6,1.3)
        paths=[[[.5,1],[19.5,1]]]
        tiles=ink_fit.ink_tiles(ink,d.pen_mm)
        self.assertGreater(len(tiles),1)
        extra=ink_fit.weight_paths(paths,ink,d,.4)
        self.assertTrue(extra)
        self.assertTrue(all(e.path_length(p)>18.9 for p in extra))
        for p in extra:
            self.assertLess(LineString(p).buffer(d.pen_mm/2,quad_segs=64).difference(ink).area,1e-9)


if __name__=='__main__':unittest.main()
