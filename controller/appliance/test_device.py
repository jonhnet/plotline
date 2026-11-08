"""Transport tests use real PTYs; nothing here can contact the physical plotter."""
import json
import os
from pathlib import Path
import pty
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import device


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.endpoint = str(Path(self.temp.name) / 'bridge.sock')
        self.server = device.Server(self.endpoint, os.ttyname(self.slave))
        self.events = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def connection(self):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(2)
        s.connect(self.endpoint)
        self.addCleanup(s.close)
        reader = s.makefile('rb')
        self.addCleanup(reader.close)
        def call(op, **values):
            s.sendall((json.dumps(dict(protocol=2, op=op, **values))+'\n').encode())
            result = json.loads(reader.readline())
            while 'event' in result:
                self.events.append(result)
                result = json.loads(reader.readline())
            if not result['ok']:
                raise RuntimeError(result['error'])
            return result['result']
        return s, reader, call

    def wait_idle(self):
        deadline = time.monotonic()+2
        while self.server.port_lock.locked():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)

    def read_serial(self, size):
        data = b''
        deadline = time.monotonic()+2
        while len(data) < size:
            self.assertTrue(select.select([self.master], [], [], max(0, deadline-time.monotonic()))[0])
            data += os.read(self.master, size-len(data))
        return data

    def test_status_and_single_exclusive_connection(self):
        _, _, first = self.connection()
        status = first('status')
        self.assertEqual(status['protocol'], 2)
        self.assertFalse(status['busy'])
        self.assertNotIn('execution', status)
        self.assertNotIn('limits', status)
        first('open')
        _, _, other = self.connection()
        self.assertTrue(other('status')['busy'])
        with self.assertRaisesRegex(RuntimeError, 'already in use'):
            other('open')
        first('close')
        self.wait_idle()
        _, _, reopened = self.connection()
        reopened('open'); reopened('close')

    def test_arbitrary_fragmented_bytes_and_partial_os_writes(self):
        _, _, call = self.connection(); call('open')
        original = os.write
        # Fragments, invalid HPGL and binary bytes are transported unchanged.
        pieces = [b'I', b'N;P', b'D1,', b'2;', b'not HPGL\x00\xff\r\n']
        with patch.object(device.os, 'write', lambda fd,data: original(fd,data[:2])):
            for data in pieces:
                self.assertEqual(call('write', data=list(data))['written'], len(data))
        expected = b''.join(pieces)
        self.assertEqual(self.read_serial(len(expected)), expected)
        os.write(self.master, b'7585B\r\x00\xff')
        self.assertEqual(bytes(call('read', timeout_ms=100)['data']), b'7585B\r\x00\xff')
        call('close')

    def test_timing_streams_partial_write_boundaries_with_session_offsets(self):
        _, _, call = self.connection()
        self.assertIn(device.TIMING, call('open')['capabilities'])
        call('write', data=list(b'prior query'))
        self.assertEqual(os.read(self.master, 1024), b'prior query')
        metadata = call('timing')
        self.assertEqual(metadata['schema'], device.TIMING)
        original = os.write
        with patch.object(device.os, 'write', lambda fd, data: original(fd, data[:1])):
            call('write', data=[65]*512)
        call('write', data=list(b'PU;\n'))
        call('close')
        self.assertEqual(self.read_serial(516), b'A'*512+b'PU;\n')
        samples = [row for event in self.events for row in event['samples']]
        self.assertEqual(samples[0][2:], [0, 0])
        self.assertEqual(samples[-1][2:], [516, 0])
        self.assertEqual(set(row[2] for row in samples), set(range(513)) | {516})
        self.assertEqual([r[0] for r in samples], sorted(r[0] for r in samples))
        self.assertTrue(all(0 <= row[1] <= row[0] for row in samples))
        self.assertTrue(all(len(json.dumps(event)) < device.MAX_FRAME for event in self.events))
        self.assertGreater(len(self.events), 8, 'Long writes stream bounded telemetry frames')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [Path(self.endpoint)])

    def test_timing_retains_queue_stall_observation_intervals(self):
        _, _, call = self.connection(); call('open')
        # Initial sample, pre-write, post-write, and drain polls. Repeated
        # observations narrow the later transition interval without extra rows.
        pending = iter([0, 0, 0, 180, 180, 80, 80, 64, 64])
        with patch.object(device.Serial, 'pending', lambda s: next(pending, 0)):
            call('timing')
            call('write', data=[65]*200)
        call('close')
        samples = [row for event in self.events for row in event['samples']]
        reduced = next(r for r in samples if r[3] == 80)
        prior = next(r for r in samples if r[3] == 180)
        self.assertGreater(reduced[1], prior[0])
        self.assertGreater(reduced[0], reduced[1])
        self.assertTrue(any(r[2:] == [200, 64] for r in samples))
        self.assertEqual(os.read(self.master, 1024), b'A'*200)

    def test_lost_connection_sends_no_automatic_pen_command_or_replay(self):
        connection, reader, call = self.connection(); call('open')
        call('write', data=list(b'PD12,'))
        self.assertEqual(os.read(self.master, 1024), b'PD12,')
        reader.close(); connection.close(); self.wait_idle()
        self.assertFalse(select.select([self.master],[],[],.1)[0])
        _, _, second = self.connection(); second('open')
        second('write', data=list(b'fresh bytes'))
        self.assertEqual(os.read(self.master, 1024), b'fresh bytes')
        second('close')

    def test_disconnect_unblocks_flow_control_without_injecting_bytes(self):
        connection, reader, call = self.connection(); call('open')
        with patch.object(device.os, 'write', side_effect=BlockingIOError):
            connection.sendall(b'{"protocol":2,"op":"write","data":[65,66,67]}\n')
            time.sleep(.05)
            reader.close(); connection.close(); self.wait_idle()
        self.assertFalse(select.select([self.master],[],[],.1)[0])

    def test_jobs_and_hpgl_operations_do_not_exist_on_bridge(self):
        for op in ('start', 'upload', 'measure', 'pause', 'complete'):
            _, _, call = self.connection()
            with self.assertRaises(RuntimeError):
                call(op)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [Path(self.endpoint)])

    def test_forced_command_client_supports_one_shot_and_persistent_stdio(self):
        script = str(Path(device.__file__))
        result = subprocess.run([sys.executable,script,'client','--socket',self.endpoint],
            input='{"protocol":2,"op":"status"}\n',capture_output=True,text=True,timeout=3,check=True)
        self.assertTrue(json.loads(result.stdout)['ok'])
        client = subprocess.Popen([sys.executable,script,'client','--socket',self.endpoint],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            def call(op, **values):
                client.stdin.write((json.dumps(dict(protocol=2,op=op,**values))+'\n').encode()); client.stdin.flush()
                result = json.loads(client.stdout.readline()); self.assertTrue(result['ok']); return result['result']
            call('open'); call('write',data=list(b'bytes'))
            self.assertEqual(os.read(self.master,1024),b'bytes')
            call('close'); client.stdin.close(); client.wait(3)
            self.assertEqual(client.stderr.read(),b'')
        finally:
            client.kill(); client.wait()
            client.stdout.close(); client.stderr.close()


if __name__ == '__main__':
    unittest.main()
