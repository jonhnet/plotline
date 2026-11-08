import unittest

import numpy as np
import shapely
from shapely.geometry import LineString

from raster_lab import engine, fill_join


def paint(paths, diameter):
    return shapely.union_all([LineString(p).buffer(diameter/2, quad_segs=128) for p in paths])


class FillJoinTests(unittest.TestCase):
    def test_join_reduces_lifts_without_changing_painted_shape(self):
        rows=[[[1,1+i*.06],[3,1+i*.06]] for i in range(20)]
        joined=fill_join.join(rows,.075,.45)
        self.assertEqual(len(joined),1)
        before,after=paint(rows,.075),paint(joined,.075)
        self.assertLess(after.symmetric_difference(before).area,1e-8)
        self.assertGreater(sum(map(engine.path_length,joined)),sum(map(engine.path_length,rows)))

    def test_round_reversals_preserve_rasterized_caps(self):
        rows=[[[1,1+i*.06],[3,1+i*.06]] for i in range(20)]
        joined=fill_join.join(rows,.075,.45)
        old=engine.Drawing(4,3,.075,{'K':rows})
        new=engine.Drawing(4,3,.075,{'K':joined})
        np.testing.assert_array_equal(engine.stroke_mask(old,'K',800,600,3),engine.stroke_mask(new,'K',800,600,3))

    def test_disconnected_columns_and_white_row_gaps_stay_separate(self):
        rows=[[[x,y],[x+.2,y]] for y in [1,1.06,1.5,1.56] for x in [1,2]]
        joined=fill_join.join(rows,.075,.45)
        self.assertEqual(len(joined),4)
        self.assertLess(paint(rows,.075).symmetric_difference(paint(joined,.075)).area,1e-8)

    def test_preserves_tiny_marks_and_every_original_segment(self):
        rows=[[[1,1],[1.017,1]],[[1,1.001],[1.017,1.001]],[[2,2],[2.017,2]],[[2,2.06],[2.017,2.06]]]
        joined=fill_join.join(rows,.075,.45)
        self.assertEqual(len(joined),3)
        original={tuple(map(tuple,p)) for p in rows}
        segments={tuple(sorted((tuple(a),tuple(b)))) for p in joined for a,b in zip(p,p[1:])}
        self.assertTrue(original<=segments)
        self.assertLess(paint(rows,.075).symmetric_difference(paint(joined,.075)).area,1e-8)

    def test_fork_joins_at_most_one_run_and_keeps_other_branch(self):
        rows=[[[1,1],[2,1]],[[1,1.04],[1.3,1.04]],[[1.7,1.04],[2,1.04]]]
        joined=fill_join.join(rows,.075,.45)
        self.assertEqual(len(joined),2)
        self.assertLess(paint(rows,.075).symmetric_difference(paint(joined,.075)).area,1e-8)

    def test_extra_travel_limit_avoids_long_backtracking(self):
        rows=[[[1,1],[5,1]],[[1,1.04],[2,1.04]]]
        self.assertEqual(len(fill_join.join(rows,.075,.45)),2)

    def test_shifted_caps_and_grid_phases_keep_full_pen_coverage(self):
        rng=np.random.default_rng(24)
        for _ in range(120):
            y=round(float(rng.uniform(.2,3)),3)
            dy=round(float(rng.uniform(.001,.073)),3)
            left=round(float(rng.uniform(1,1.2)),3)
            right=round(float(rng.uniform(1.22,1.7)),3)
            rows=[[[1,y],[1.6,y]],[[left,round(y+dy,3)],[right,round(y+dy,3)]]]
            joined=fill_join.join(rows,.075,.45)
            self.assertLess(paint(rows,.075).symmetric_difference(paint(joined,.075)).area,1e-8)

    def test_metrics_report_listed_order_and_exact_join_count(self):
        rows=[[[1,1],[2,1]],[[1,1.04],[2,1.04]]]
        joined=fill_join.join(rows,.075,.45);m=fill_join.metrics(rows,joined)
        self.assertEqual(m['joins'],1);self.assertEqual(m['joined_runs'],1)
        self.assertEqual(m['joined_pen_up_meters'],0)
        self.assertGreater(m['original_pen_up_meters'],0)

    def test_quarter_mm_pen_preserves_paint_across_cap_phases(self):
        rng=np.random.default_rng(25)
        for _ in range(120):
            y=round(float(rng.uniform(.2,3)),3)
            dy=round(float(rng.uniform(.001,.248)),3)
            left=round(float(rng.uniform(1,1.2)),3)
            right=round(float(rng.uniform(1.22,1.7)),3)
            rows=[[[1,y],[1.6,y]],[[left,round(y+dy,3)],[right,round(y+dy,3)]]]
            joined=fill_join.join(rows,.25,.45)
            self.assertLess(paint(rows,.25).symmetric_difference(paint(joined,.25)).area,1e-8)

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(fill_join.join([],.075,.45),[])
        with self.assertRaises(ValueError):fill_join.join([[[1,1],[2,2]]],.075,.45)


if __name__=='__main__':unittest.main()
