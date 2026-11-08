#!/usr/bin/env python3
"""Validate a captured plot's timing/HPGL pairing and optionally export raw CSV.

This is measurement inspection, not a plotting-time estimator. No device access.
"""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path


def open_retained(path):
    path = Path(path)
    if path.suffix == '.gz':
        return gzip.open(path, 'rb')
    try:
        return path.open('rb')
    except FileNotFoundError:
        return gzip.open(str(path) + '.gz', 'rb')


def inspect(directory, csv_writer=None):
    with open_retained(directory / 'prepared.hpgl') as stored:
        source = stored.read()
    digest = hashlib.sha256(source).hexdigest()
    summary = dict(prepared_bytes=len(source), prepared_sha256=digest,
                   write_calls=0, injected_writes=0, completion_queries=0, pause_queries=0, cancel_queries=0, acknowledged_prepared_bytes=0,
                   samples=0, truncated_final_record=False, closed=False, errors=[])
    stream_offset = 0
    pending = None
    last_time = last_accepted = 0
    if csv_writer:
        csv_writer.writerow(['pi_elapsed_ns', 'previous_poll_ns', 'accepted_bytes',
                             'kernel_pending_bytes', 'left_host_queue_bytes'])
    with open_retained(directory / 'serial-timing.jsonl') as log:
        for index, line in enumerate(log):
            # A killed process can leave a partial last line. Never pretend it
            # or an unacknowledged intent is a confirmed serial delivery.
            if not line.endswith(b'\n'):
                summary['truncated_final_record'] = True
                break
            event = json.loads(line)
            kind = event['event']
            if index == 0:
                assert kind == 'header' and event['schema'] in ('plotline.serial-timing.v1','squidprint.serial-timing.v1')
                meta = event['metadata']
                assert meta['prepared_sha256'] == digest and meta['prepared_bytes'] == len(source), 'HPGL checksum/size mismatch'
                summary.update(attempt=meta['attempt'], pi_timing_available=event['pi_timing_available'])
            elif kind == 'write':
                assert pending is None, 'A write intent has no preceding acknowledgement'
                assert event['stream_offset'] == stream_offset, 'Noncontiguous transport offsets'
                data = bytes(event['data'])
                assert 0 < len(data) <= 512
                offset = event['prepared_offset']
                assert offset == summary['acknowledged_prepared_bytes']
                if event['kind'] == 'prepared':
                    assert source[offset:offset+len(data)] == data, 'Write differs from prepared HPGL'
                else:
                    summary['injected_writes'] += 1
                    if event['kind'] == 'completion-query':
                        assert offset == len(source) and data in (b'OA;\n', b'OS;\n'), 'Invalid completion query tail'
                        summary['completion_queries'] += 1
                    elif event['kind'] == 'pause-query':
                        assert data in (b'OA;\n', b'OS;\n'), 'Invalid pause query'
                        summary['pause_queries'] += 1
                    elif event['kind'] == 'cancel-query':
                        assert data in (b'OA;\n', b'OS;\n'), 'Invalid cancel query'
                        summary['cancel_queries'] += 1
                stream_offset += len(data)
                summary['write_calls'] += 1
                pending = event
            elif kind == 'tx_queue_timing_v1':
                for row in event['samples']:
                    now, previous, accepted, queued = row
                    assert 0 <= previous <= now and now >= last_time
                    assert last_accepted <= accepted <= stream_offset and queued >= 0
                    last_time, last_accepted = now, accepted
                    summary['samples'] += 1
                    if csv_writer:
                        csv_writer.writerow([*row, accepted-queued])
            elif kind == 'reply':
                if event['op'] == 'write':
                    assert pending is not None and event['result']['written'] == len(pending['data'])
                    if pending['kind'] == 'prepared':
                        summary['acknowledged_prepared_bytes'] += len(pending['data'])
                    pending = None
                elif event['op'] == 'close':
                    summary['closed'] = True
            elif kind == 'error':
                summary['errors'].append(event['error'])
    assert 'attempt' in summary, 'No timing header'
    summary.update(last_pi_elapsed_seconds=last_time/1e9,
                   stream_intended_bytes=stream_offset, last_pi_accepted_bytes=last_accepted,
                   unacknowledged_write=pending is not None)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempt_directory', type=Path)
    parser.add_argument('--csv', type=Path, help='Export observations; never interpolate unobserved byte times')
    args = parser.parse_args()
    if args.csv:
        with args.csv.open('w', newline='') as output:
            summary = inspect(args.attempt_directory, csv.writer(output))
    else:
        summary = inspect(args.attempt_directory)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
