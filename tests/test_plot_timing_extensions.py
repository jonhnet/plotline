"""Checks for offline timing hypotheses; never contact the plotter."""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from plot_timing.extensions import Drawing, carriage_distances, fit, residuals, simulate
from plot_timing.fit import predict
from plot_timing.model import parse, packetize


class ExtensionTests(unittest.TestCase):
    def test_carriage_proxy_uses_across_sheet_axis_exchanges_and_parking(self):
        source=(b'IN;\nPA;\nSP1;\nPU8000,1000;\nSP2;\nSP2;\n'
                b'PU-4000,3000;\nSP0;\nSP0;\nSP3;\n')
        indices,metres=carriage_distances(source,-1000)
        # Initial position is unknown; repeated selections do not exchange.
        # 50 mm to the side and back; 100 mm to park; 100 mm to pick up.
        self.assertEqual(indices.tolist(),[4,7,9])
        np.testing.assert_allclose(metres,[.1,.1,.1])
        translated=source.replace(b',1000',b',5000').replace(b',3000',b',7000')
        np.testing.assert_allclose(carriage_distances(translated,3000)[1],metres)

    def test_invalid_carriage_geometry_is_not_silently_clamped(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            carriage_distances(b'IN;\nSP1;\nPU0,-2000;\nSP0;\n',-1000)
        for edge,units in [(float('nan'),40),(0,0)]:
            with self.assertRaises(ValueError):
                carriage_distances(b'',edge,units)

    def drawing(self):
        program=parse(b'IN;\nPA;\nSP1;\nPU0,0;\nPD0,4000;\nPU;\nSP0;\n')
        indices,gaps=packetize(program,.032)
        travel,metres=carriage_distances(program.source,0)
        capture=SimpleNamespace(program=program,byte_rate=960.,packet_indices=indices,
                                drain=np.array([2.]),directory=Path('synthetic'))
        return Drawing(capture,'maps','synthetic',{'OS':{'seconds':3.}},travel,metres,gaps,
                       np.array([],dtype=int),np.array([],dtype=int))

    def test_zero_travel_preserves_baseline_and_park_adds_exact_extra_time(self):
        drawing=self.drawing()
        p=np.array([1.,100.,1e9,.03,1.,.018,1024.])
        incoming,done=simulate(drawing,p,0.)
        old_incoming,old_done,_=predict(p,drawing.capture.program,.032,960.)
        np.testing.assert_array_equal(incoming,old_incoming)
        np.testing.assert_array_equal(done,old_done)
        _,with_travel=simulate(drawing,p,4.)
        self.assertAlmostEqual(with_travel[-1]-done[-1],.4)
        with self.assertRaises(ValueError):simulate(drawing,p,-1)

    def test_equal_family_objective_does_not_reward_repeated_related_drawings(self):
        a=self.drawing();b=replace(a,family='line-art',finish={'OS':{'seconds':4.}})
        p=np.array([1.,100.,1e9,.03,1.,.018,1024.])
        original=residuals([a,b],p,0.)
        repeated=residuals([a,a,a,b],p,0.)
        self.assertAlmostEqual(np.dot(original,original),np.dot(repeated,repeated))

    def test_nested_models_can_turn_off_a_term_when_baseline_is_exact(self):
        d=self.drawing();p=np.array([1.,100.,1e9,.03,1.,.018,1024.])
        d.finish={'OS':{'seconds':float(simulate(d,p,0.)[1][-1])}}
        for variant in ('acceleration','carriage','both'):
            result=fit([d],p,variant,free_base=())
            self.assertAlmostEqual(result['objective'],0.,places=15)
            self.assertEqual(result['carriage_s_m'],0.)
            self.assertEqual(result['parameters']['acceleration_mm_s2'],1e9)


if __name__=='__main__':unittest.main()
