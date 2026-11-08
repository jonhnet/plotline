#!/usr/bin/env python3
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from retention import Collector, compress, load


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = dict(preview_max_bytes=100000, preview_max_age_days=7,
                           timing_max_bytes=100000, timing_max_age_days=90)
        self.now = 1000000

    def json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def conversion(self, status='ready'):
        doc, cid = str(uuid4()), str(uuid4())
        original = self.root / 'documents' / doc
        original.mkdir(parents=True)
        (original / 'source.pdf').write_bytes(b'original PDF')
        self.json(original / 'record.json', dict(status='ready'))
        directory = self.root / 'conversions' / cid
        self.json(directory / 'record.json', dict(status=status, document_id=doc,
                  source_sha256=hashlib.sha256(b'original PDF').hexdigest()))
        self.json(directory / 'intent.json', {'recipe': 'must survive'})
        page = directory / 'page-0'; page.mkdir()
        for name in ('events.json', 'report.json', 'summary.json', 'pen.svg', 'logical.svg', 'source.svg', 'plot.hpgl'):
            (page / name).write_bytes(name.encode() * 1000)
        return original, directory, page

    def sweep(self, apply=True):
        return Collector(self.root, apply, self.policy, self.now).run()

    def test_roundtrip_bound_and_originals_survive(self):
        original, directory, page = self.conversion()
        hpgl = (page / 'plot.hpgl').read_bytes()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.sweep(False)
        self.assertFalse(result['errors'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.policy['preview_max_bytes'] = 0
        result = self.sweep()
        self.assertFalse(result['errors'])
        self.assertEqual(result['preview_retained_bytes'], 0)
        self.assertFalse((page / 'events.json').exists())
        self.assertFalse((page / 'pen.svg.gz').exists())
        self.assertEqual(gzip.decompress((page / 'plot.hpgl.gz').read_bytes()), hpgl)
        self.assertEqual((original / 'source.pdf').read_bytes(), b'original PDF')
        self.assertEqual(load(directory / 'intent.json'), {'recipe': 'must survive'})
        self.assertTrue((page / 'summary.json').exists())
        again = self.sweep()
        self.assertEqual((again['compressed_files'], again['removed_files']), (0, 0))

    def test_versioned_page_previews_expire_but_image_originals_survive(self):
        original, _, _ = self.conversion()
        for name in ('page-0.png','page-0-v2.png'):(original/name).write_bytes(b'preview')
        for name in ('original.jpg','original.png'):(original/name).write_bytes(b'original image')
        self.policy['preview_max_bytes']=0
        self.assertFalse(self.sweep()['errors'])
        for name in ('page-0.png','page-0-v2.png'):self.assertFalse((original/name).exists())
        for name in ('original.jpg','original.png'):self.assertEqual((original/name).read_bytes(),b'original image')

    def test_active_failed_original_and_symlink_protection(self):
        _, _, active = self.conversion('running')
        original, _, bad = self.conversion()
        (original / 'source.pdf').write_bytes(b'corrupt')
        external = self.root / 'external'; external.mkdir()
        (external / 'events.json').write_bytes(b'precious')
        (active.parent / 'page-1').symlink_to(external, target_is_directory=True)
        result = self.sweep()
        self.assertEqual(len(result['errors']), 1)
        self.assertTrue((active / 'events.json').exists())
        self.assertTrue((bad / 'events.json').exists())
        self.assertEqual((external / 'events.json').read_bytes(), b'precious')

    def test_older_preview_evicts_first_and_compression_does_not_reset_age(self):
        _, older, old_page = self.conversion()
        _, newer, new_page = self.conversion()
        for directory, age in ((older, 8 * 86400), (newer, 60)):
            stamp = directory / '.last-access'; stamp.touch()
            os.utime(stamp, (self.now-age, self.now-age))
        result = self.sweep()
        self.assertFalse(result['errors'])
        self.assertFalse((old_page / 'pen.svg.gz').exists())
        self.assertTrue((new_page / 'pen.svg.gz').exists())
        self.assertEqual((newer / '.last-access').stat().st_mtime, self.now-60)

    def test_timing_expires_but_history_and_exact_commands_remain(self):
        for status in ('finished', 'feeding', 'interrupted'):
            directory = self.root / 'plots' / str(uuid4())
            self.json(directory / 'record.json', dict(execution=dict(state=status), created_at=0))
            (directory / 'prepared.hpgl').write_bytes(b'IN;\nSP0;\n')
            (directory / 'serial-timing.jsonl').write_bytes(b'{"event":"test"}\n')
            self.sweep()
            if status == 'finished':
                self.assertTrue((directory / 'prepared.hpgl.gz').exists())
                # Advance past retention age, and preserve the trace fingerprint.
                self.now = 100 * 86400; self.sweep()
                self.assertFalse((directory / 'serial-timing.jsonl.gz').exists())
                self.assertIn('sha256', load(directory / 'timing-archive.json'))
            else:
                self.assertTrue((directory / 'serial-timing.jsonl').exists())
                self.assertTrue((directory / 'prepared.hpgl').exists())

    def test_bad_archive_keeps_original_and_interrupted_good_archive_recovers(self):
        path = self.root / 'source.hpgl'; path.write_bytes(b'precious HPGL')
        archive = self.root / 'source.hpgl.gz'; archive.write_bytes(b'broken')
        with self.assertRaises(OSError):
            compress(path)
        self.assertEqual(path.read_bytes(), b'precious HPGL')
        archive.write_bytes(gzip.compress(path.read_bytes()))
        compress(path)
        self.assertFalse(path.exists())
        self.assertEqual(gzip.decompress(archive.read_bytes()), b'precious HPGL')
        interrupted = self.root / '.retention-unpublished'
        interrupted.write_bytes(b'incomplete archive')
        self.sweep()
        self.assertFalse(interrupted.exists())
        self.assertEqual(gzip.decompress(archive.read_bytes()), b'precious HPGL')

    def test_legacy_report_keeps_metrics_without_duplicate_geometry(self):
        _, _, page = self.conversion()
        (page / 'summary.json').unlink()
        self.json(page / 'report.json', dict(metrics={'paths':12}, engine='old engine',
                  decoded_hpgl=['bulky path geometry'], planned=['duplicate geometry']))
        result = self.sweep()
        self.assertFalse(result['errors'])
        summary = load(page / 'summary.json')
        self.assertEqual(summary['metrics'], {'paths':12})
        self.assertEqual(summary['engine'], 'old engine')
        self.assertNotIn('decoded_hpgl', summary)
        self.assertFalse((page / 'report.json').exists())


if __name__ == '__main__':
    unittest.main()
