"""Offline math and data-integrity checks; no network or serial access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from plot_timing.model import parse, simulate, command_costs, pipeline, packetize, load_capture


class TimingTests(unittest.TestCase):
    def parameters(self, minimum=0):
        return np.array([1., 100., 1000., 0., 0., minimum, 1024.])

    def work(self, source, minimum=0):
        p=parse(source)
        c=command_costs(self.parameters(minimum),p.lengths,p.down,p.speeds,p.radius,p.move_index,p.transitions,p.selections)
        return c.sum()

    def test_straight_motion_has_analytic_trapezoid_time_and_subdivision_invariance(self):
        a=b'IN;\nPA;\nVS5;\nPU0,0;\nPD2000,0;\nPU;\n'
        b=b'IN;\nPA;\nVS5;\nPU0,0;\nPD1000,0;\nPD2000,0;\nPU;\n'
        # 50 mm at 50 mm/s; acceleration/deceleration adds v/a = .05 s.
        self.assertAlmostEqual(self.work(a),1.05)
        self.assertAlmostEqual(self.work(b),1.05)

    def test_short_sharp_turn_slows_motion_and_vector_floor_limits_dense_commands(self):
        straight=b'IN;\nPA;\nVS5;\nPU0,0;\nPD10,0;\nPD20,0;\nPU;\n'
        corner=b'IN;\nPA;\nVS5;\nPU0,0;\nPD10,0;\nPD10,10;\nPU;\n'
        self.assertGreater(self.work(corner),self.work(straight))
        self.assertGreaterEqual(self.work(straight,minimum=.1),.3)

    def test_fifo_blocks_input_until_space_is_freed_and_keeps_wire_rate(self):
        ends=np.arange(10,401,10);costs=np.zeros(40);costs[0]=10
        gap=np.zeros(40)
        arrival,finish=pipeline(costs,ends,gap,128.,960.)
        self.assertLess(arrival[10],1.)
        self.assertGreaterEqual(arrival[13],10.)
        self.assertTrue(np.all(finish>=arrival))
        self.assertTrue(np.all(np.diff(arrival)>=0))
        fast,_=pipeline(costs,ends,gap,1024.,960.)
        self.assertAlmostEqual(fast[-1],400/960)
        self.assertLess(fast[-1],arrival[-1])

    def test_packet_pacing_and_pen_transition_counts(self):
        source=b'IN;\nPA;\nSP2;\nPU0,0;\n'+b'PD100,100;\nPU;\nPU0,0;\n'*100+b'SP0;\n'
        program=parse(source);indices,gaps=packetize(program,.032)
        self.assertEqual(program.transitions.sum(),200)
        self.assertEqual(program.selections.sum(),2)
        sizes=np.diff(np.r_[0,program.ends[indices]])
        self.assertTrue(np.all((sizes>0)&(sizes<=512)))
        self.assertEqual(np.count_nonzero(gaps),len(indices)-1)
        with self.assertRaises(ValueError):parse(b'IN;PA;PU0,0;PD2,2;')
        with self.assertRaises(ValueError):parse(b'IN;\nPR;\nPU0,0;\n')
        self.assertEqual(len(parse(b'PU;\nIN;\nPA;\nSP1;\nPU0,0;\nPD40,40;\nPU;\nSP0;\n').ends),8)
        with self.assertRaises(ValueError):parse(b'PU0,0;\nIN;\nPA;\n')

    def fixture(self, directory, corrupted=False, paused=False):
        source=b'IN;\nPA;\nSP1;\nPU0,0;\nPD100,100;\nPU;\nSP0;\n'
        directory.joinpath('prepared.hpgl').write_bytes(source)
        n=len(source)
        events=[{'event':'header','schema':'plotline.serial-timing.v1','pi_timing_available':True,
                 'metadata':{'prepared_sha256':'bad' if corrupted else hashlib.sha256(source).hexdigest(),'prepared_bytes':n,'attempt':'test','units_per_mm':40}},
                {'event':'reply','op':'timing','result':{'baud':9600,'bits_per_byte':10}},
                {'event':'write','kind':'prepared','stream_offset':0,'prepared_offset':0,'data':list(source)},
                {'event':'tx_queue_timing_v1','samples':[[1000000,0,n,n],[100000000,1000000,n,0]]},
                {'event':'reply','op':'write','result':{'written':n}}]
        if paused:
            events.extend([{'event':'write','kind':'pause','stream_offset':n,'prepared_offset':n,'data':list(b'PU;\n')},
                           {'event':'reply','op':'write','result':{'written':4}}])
        events.append({'event':'reply','op':'close','result':{'closed':True}})
        directory.joinpath('serial-timing.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))

    def test_capture_binding_and_pauses_are_not_silently_used_for_calibration(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);self.fixture(directory)
            capture=load_capture(directory)
            self.assertEqual(capture.packet_ends.tolist(),[len(capture.program.source)])
            self.fixture(directory,corrupted=True)
            with self.assertRaisesRegex(AssertionError,'checksum'):load_capture(directory)
            self.fixture(directory,paused=True)
            with self.assertRaisesRegex(ValueError,'Pause/resume'):load_capture(directory)

    def test_completion_queries_are_excluded_from_prepared_timing_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);self.fixture(directory)
            trace=directory/'serial-timing.jsonl'
            events=[json.loads(line) for line in trace.read_text().splitlines()]
            close=events.pop();n=len((directory/'prepared.hpgl').read_bytes())
            for i,data in enumerate((b'OA;\n',b'OS;\n')):
                events.extend([{'event':'write','kind':'completion-query','stream_offset':n+4*i,'prepared_offset':n,'data':list(data)},
                               {'event':'reply','op':'write','result':{'written':4}}])
            events.append(close);trace.write_text(''.join(json.dumps(e)+'\n' for e in events))
            capture=load_capture(directory)
            self.assertEqual(capture.summary['completion_queries'],2)
            self.assertEqual(capture.packet_ends.tolist(),[n])
            self.assertEqual(capture.drain.tolist(),[.1])

    def test_short_job_has_whole_plot_score_without_fabricated_block_validation(self):
        from plot_timing.evaluate import score
        from plot_timing.fit import predict
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);self.fixture(directory)
            capture=load_capture(directory)
            expected,done,_=predict(self.parameters(),capture.program,.032,capture.byte_rate)
            result,curve=score(capture,self.parameters(),.032,{'OS':{'seconds':2.}})
            self.assertAlmostEqual(result['feed']['actual_s'],.1)
            self.assertAlmostEqual(result['feed']['predicted_s'],expected[-1])
            self.assertAlmostEqual(result['completion']['error_s'],done[-1]-2.)
            self.assertEqual(result['sections'],{})
            self.assertIn('longer complete trace',result['section_note'])
            self.assertEqual(len(curve),1)

    def test_review_rejects_cancelled_full_prefixes_and_recovery_as_independent_plots(self):
        from plot_timing.review import eligibility
        record={'bytes':100,'execution':{'state':'finished','submitted_bytes':100}}
        self.assertIsNone(eligibility(record))
        record['execution']['cancelled']=True
        self.assertIsNotNone(eligibility(record))
        record['execution']['cancelled']=False
        record['execution']['submitted_bytes']=99
        self.assertIsNotNone(eligibility(record))
        record['execution']['submitted_bytes']=100
        record['recovery']={'origin_attempt':'old','start_stroke':400}
        self.assertIn('Recovery suffix',eligibility(record))

    def test_completion_comparison_uses_complete_split_replies_on_one_clock(self):
        from plot_timing.evaluate import completion
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            evidence={'method':'hpgl-oa-os','position':[-12,34],'status':26}
            (directory/'record.json').write_text(json.dumps({'execution':{'completion':evidence}}))
            events=[{'event':'write','kind':'prepared'},
                    {'event':'reply','op':'write','host_elapsed_ns':10_000_000_000},
                    {'event':'write','kind':'completion-query','data':list(b'OA;\n')}]
            for t,data in [(11,b''),(12,b'\n'),(13,b'-12,3'),(14,b'4,0\r')]:
                events.append({'event':'reply','op':'read','host_elapsed_ns':t*10**9,'result':{'data':list(data)}})
            events.extend([{'event':'write','kind':'completion-query','data':list(b'OS;\n')},
                           {'event':'reply','op':'read','host_elapsed_ns':15*10**9,'result':{'data':list(b'26\r')}}])
            path=directory/'serial-timing.jsonl'
            def save():path.write_text(''.join(json.dumps(e)+'\n' for e in events))
            save();actual=completion(directory)
            self.assertEqual(actual['OA']['seconds'],14)
            self.assertEqual(actual['tail_to_oa_s'],4)
            self.assertEqual(actual['tail_to_os_s'],5)
            events[-1]['result']['data']=list(b'26');save()
            with self.assertRaisesRegex(ValueError,'lack confirmed'):completion(directory)
            events[-1]['result']['data']=list(b'48\r');save()
            with self.assertRaisesRegex(ValueError,'disagree'):completion(directory)


if __name__=='__main__':unittest.main()
