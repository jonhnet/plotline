import sys
import json
from pathlib import Path
import unittest

import numpy as np
import pymupdf
import shapely
from shapely.geometry import box, LineString

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'tools'))
from terrain_geometry import Terrain, stroke_clip
from regional_map import Drawing
from terrain_map import page_layout
from check_terrain import visible_coverage


class TerrainTests(unittest.TestCase):
    def test_contour_audit_accepts_svg_rounding_crossings_and_preserves_holes(self):
        # Actual six-decimal diagnostic ring from contour paint 337: overlaying
        # it directly raises a GEOS error despite the sub-micropoint crossing.
        data = ('M 1047.748124 447.699064 L 1047.747924 447.699020 '
                'L 1047.747865 447.699005 L 1047.818856 447.379217 '
                'L 1047.888329 447.064366 L 1046.788176 446.821583 '
                'L 1046.788109 446.821569 L 1046.648054 447.456298 '
                'L 1046.648103 447.456311 L 1047.748124 447.699064 Z')
        geometry = visible_coverage(data)
        self.assertTrue(geometry.is_valid)
        self.assertAlmostEqual(geometry.area, .7322806380680874, places=9)
        with_hole = visible_coverage('M 0 0 L 4 0 L 4 4 L 0 4 Z '
                                     'M 1 1 L 3 1 L 3 3 L 1 3 Z')
        self.assertEqual(with_hole.area,12)
        self.assertTrue(with_hole.intersection(box(1,1,3,3)).area == 0)

    def test_landscape_layout_respects_both_observed_limits_and_furniture(self):
        style = json.loads((ROOT/'styles/seattle-relief-hp7585-11x17.json').read_text())
        layout = page_layout(style)
        rect = np.array(layout['content_rect_pt'])*25.4/72
        # Back/front become PDF left/right; machine right/left become top/bottom.
        np.testing.assert_allclose(rect, [40.425,12,415.375,263.6], atol=1e-8)
        np.testing.assert_allclose(rect[2:]-rect[:2], [374.95,251.6], atol=1e-8)
        frame = layout['terrain_frame_pt']
        self.assertGreater(frame[1], layout['content_rect_pt'][1]+80)
        self.assertLess(frame[3], layout['content_rect_pt'][3]-80)
        legacy = json.loads((ROOT/'styles/seattle-relief-11x17.json').read_text())
        self.assertEqual(page_layout(legacy)['terrain_frame_pt'], (36,125,1188,682))

    def test_landscape_layout_rejects_invalid_or_exhausted_printable_area(self):
        style = json.loads((ROOT/'styles/seattle-relief-hp7585-11x17.json').read_text())
        for overrides in [dict(content_inset_mm=-1), dict(hard_clip_size_mm=[0,254]),
                          dict(margins_mm=dict(left=420,top=11,right=15,bottom=14.8))]:
            with self.assertRaises(ValueError):
                page_layout(dict(style, **overrides))

    def test_flat_surface_keeps_every_cell_and_draped_area(self):
        t = Terrain(range(4), range(4), np.zeros((4,4)), 3, 215, 30, [0,0,300,200])
        self.assertEqual(t.stats['fully_hidden_triangles'], 0)
        self.assertAlmostEqual(t.stats['projected_face_area_pt2'], t.outline.area, places=3)
        self.assertAlmostEqual(t.drape_area(box(0,0,3,3)).area, t.outline.area, places=3)
        self.assertTrue(t.locate(1.2,1.2)[1])

    def test_ridge_hides_far_road_and_visible_depth_is_frontmost(self):
        z = np.zeros((5,5)); z[2,:] = 2
        for azimuth in [180, 215, 35]:
            t = Terrain(range(5), range(5), z, 2, azimuth, 20, [0,0,300,200])
            if azimuth == 180:
                self.assertTrue(t.locate(1.5,1)[1])
                self.assertFalse(t.locate(1.5,3)[1])
                line, mask = t.drape_line(LineString([(0,3),(4,3)]), .65, 'hidden road')
                self.assertTrue(line.is_empty)
                self.assertTrue(mask.is_empty)
            self.assertGreater(t.stats['fully_hidden_triangles'], 0)
            self.assertLess(t.audit_visibility()['maximum_frontmost_depth_error_m'], 1e-4)

    def test_visible_run_stops_before_hidden_continuation(self):
        z = np.zeros((5,5)); z[2,:] = 2
        t = Terrain(range(5), range(5), z, 2, 180, 20, [0,0,300,200])
        line, mask = t.drape_line(LineString([(2,0),(2,4)]), .65, 'cross-ridge road')
        self.assertFalse(line.is_empty)
        self.assertLess(t.audit_lines[0]['visible_length_pt'], t.audit_lines[0]['projected_length_pt'])
        # Occlusion splits the centerline, not the pen width. Every retained
        # run carries its full width up to its end cut.
        self.assertLess(line.buffer(.325, cap_style='flat').difference(mask).area, 1e-7)

    def test_viewport_work_reduction_preserves_fills_hatches_and_finite_width(self):
        from cartographer.compose import hatches
        from build_map import parts
        z = np.zeros((9, 11)); z[4, :] = 2
        park = box(.2, .2, 9.8, 7.8).difference(box(4, 3, 6, 5))
        page = box(220, 110, 380, 220)
        for azimuth in [35, 215]:
            t = Terrain(range(11), range(9), z, 2, azimuth, 22, [0, 0, 600, 330])
            work = t.ground_for_viewport(page.buffer(12))
            self.assertLess(work.area, 50, 'Off-page facets must be excluded from decoration work')
            full_area = t.drape_area(park).intersection(page)
            reduced_area = t.drape_area(park.intersection(work)).intersection(page)
            self.assertLess(full_area.symmetric_difference(reduced_area).area, 1e-5)
            paints = []
            for cropped in [False, True]:
                hatch = hatches(park.intersection(work) if cropped else park, .5)
                # Crop original outlines as lines: never invent park boundaries
                # at the edge of the reduced working region.
                boundary = park.boundary.intersection(work) if cropped else park.boundary
                centerlines = t.drape_centerlines(shapely.union_all([hatch, boundary]), 'park')
                paints.append(shapely.union_all([
                    run.buffer(2).intersection(stroke_clip(run, 4))
                    for run in parts(centerlines, 'LineString')]).intersection(page))
            # Centerlines use a 1e-5 point snapping lattice; cropping can change
            # the phase of an intersection by one lattice step, never a mark.
            for reference, actual in [paints, paints[::-1]]:
                self.assertLess(reference.difference(actual.buffer(2e-5)).area, 1e-7)

    def test_rugged_surface_all_view_quadrants_matches_independent_depth(self):
        rng = np.random.default_rng(1024)
        z = rng.uniform(0, 2, (9,11))
        for azimuth in [15, 80, 135, 200, 270, 320]:
            t = Terrain(range(11), range(9), z, 2, azimuth, 25, [0,0,300,200])
            self.assertLess(t.audit_visibility()['maximum_frontmost_depth_error_m'], 1e-4)
            self.assertAlmostEqual(sum(g.area for g in t.visible), t.outline.area, places=3)

    def test_pdf_visibility_clip_does_not_thin_an_unoccluded_stroke(self):
        t = Terrain(range(4), range(4), np.zeros((4,4)), 3, 215, 30, [20,20,280,180])
        line, mask = t.drape_line(LineString([(.2,1.1),(2.8,2.2)]), .65, 'road')
        pixels = []
        for clipping in [None, mask]:
            drawing = Drawing(300,200)
            drawing.paint(line,'roads','road',width=.65)
            drawing.commit(clipping)
            pix = drawing.page.get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False)
            pixels.append(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,pix.width,3).astype(int))
            drawing.doc.close()
        difference = np.max(np.abs(pixels[0]-pixels[1]),axis=2)
        # Flat visibility end cuts intentionally remove the round source cap.
        for point in [line.coords[0],line.coords[-1]]:
            x,y = np.round(np.asarray(point)*3).astype(int)
            difference[y-5:y+6,x-5:x+6] = 0
        self.assertLess(np.count_nonzero(difference > 64), 5)

    def test_contour_width_survives_a_nearby_internal_mesh_edge(self):
        for elevation in [10,30,70]:
            t = Terrain(range(4), range(4), np.zeros((4,4)), 3, 215, elevation, [20,20,280,180])
            # This used to lose almost half its width despite perfectly flat,
            # completely visible terrain: the line lies just above a grid edge.
            line, mask = t.drape_line(LineString([(.2,1.0002),(2.8,1.0002)]), .65, 'contour')
            ink = line.buffer(.325, cap_style='flat')
            self.assertEqual(line.geom_type,'LineString')
            self.assertLess(ink.difference(mask).area, 1e-7)


if __name__ == '__main__':
    unittest.main()
