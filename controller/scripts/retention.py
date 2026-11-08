#!/usr/bin/env python3
"""Plotline Studios storage collector. Dry-run by default; never walks arbitrary builds.

Only terminal conversion diagnostics, previews and terminal timing traces expire.
Originals, recipes, exact HPGL, receipts, scans and history are retained. A single
state directory has one service owner. Compression publishes a verified gzip
before unlinking the original, so concurrent readers always have a complete copy.
"""
import argparse
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

DEFAULTS = Path(__file__).resolve().parents[1] / 'retention-policy.json'
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
DIAGNOSTICS = ('events.json', 'input.events.json', 'extraction.json', 'coverage.svg',
               'hpgl.svg', 'comparison.svg', 'visible.svg', 'planned.svg', 'detail-loss.svg')
PREVIEWS = ('source.svg', 'logical.svg', 'pen.svg', 'loss.svg')
SUMMARY_FIELDS = ('document_inks', 'engine', 'geos', 'metrics', 'palette', 'pen_widths_pt',
                  'routing', 'scope', 'settings', 'timing', 'validation_definitions',
                  'verification', 'by_ink', 'by_feature', 'detail_loss', 'visible_paints_without_paths')


def regular(path):
    return not path.is_symlink() and path.is_file()


def directories(path, pattern=UUID):
    if not path.is_dir() or path.is_symlink():
        return []
    return sorted(p for p in path.iterdir() if not p.is_symlink() and p.is_dir()
                  and pattern.fullmatch(p.name))


def load(path):
    if not regular(path):
        raise ValueError(f'Missing regular metadata file: {path}')
    return json.loads(path.read_text())


def sync_dir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.retention-', delete=False) as f:
        temp = Path(f.name)
        try:
            f.write((json.dumps(value, indent=2) + '\n').encode())
            f.flush(); os.fsync(f.fileno())
            os.replace(temp, path); sync_dir(path.parent)
        finally:
            temp.unlink(missing_ok=True)


def digest_stream(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def compress(path):
    """Return byte savings, verifying complete decoded bytes before removal."""
    if not regular(path):
        return 0
    before = path.stat()
    archive = Path(str(path) + '.gz')
    if archive.is_symlink():
        raise ValueError(f'Refusing symlink: {archive}')
    with path.open('rb') as original:
        digest = digest_stream(original)
    if archive.exists():
        with gzip.open(archive, 'rb') as saved:
            if digest_stream(saved) != digest:
                raise ValueError(f'Existing archive differs; original retained: {path}')
    else:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.retention-', delete=False) as f:
            temp = Path(f.name)
            try:
                with gzip.GzipFile(filename='', mode='wb', fileobj=f, compresslevel=6, mtime=0) as zipped:
                    with path.open('rb') as original:
                        shutil.copyfileobj(original, zipped, 1024 * 1024)
                f.flush(); os.fsync(f.fileno())
                with gzip.open(temp, 'rb') as saved:
                    if digest_stream(saved) != digest:
                        raise ValueError(f'Compression verification failed: {path}')
                os.replace(temp, archive); sync_dir(path.parent)
            finally:
                temp.unlink(missing_ok=True)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f'File changed during compression; original retained: {path}')
    path.unlink(); sync_dir(path.parent)
    return before.st_size - archive.stat().st_size


def variants(path):
    return [p for p in (path, Path(str(path) + '.gz')) if regular(p)]


def policy_for(root):
    policy = load(DEFAULTS)
    override = root / 'storage-policy.json'
    if override.exists():
        extra = load(override)
        if extra.keys() - policy.keys():
            raise ValueError('Unknown storage policy keys')
        policy.update(extra)
    for key, value in policy.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{key} must be a finite nonnegative number')
    return policy


class Collector:
    def __init__(self, root, apply=False, policy=None, now=None):
        self.root = Path(root).resolve(strict=True)
        self.apply = apply
        self.policy = policy if policy is not None else policy_for(self.root)
        self.now = time.time() if now is None else now
        self.stats = dict(apply=apply, compressed_files=0, compression_input_bytes=0,
                          removed_files=0, reclaimed_bytes=0, errors=[], skipped=0)
        self.previews, self.timings = [], []

    def remove(self, path):
        if regular(path):
            size = path.stat().st_size
            if self.apply:
                path.unlink()
            self.stats['removed_files'] += 1
            self.stats['reclaimed_bytes'] += size

    def compress(self, path):
        if regular(path):
            self.stats['compressed_files'] += 1
            self.stats['compression_input_bytes'] += path.stat().st_size
            if self.apply:
                self.stats['reclaimed_bytes'] += compress(path)

    def preview(self, directory, files):
        # Missing access metadata falls back to the original files' creation-era
        # mtime, never the current sweep's compression timestamp.
        files = [p for file in files for p in variants(file)]
        if not files:
            return
        stamp = directory / '.last-access'
        accessed = stamp.stat().st_mtime if regular(stamp) else max(p.stat().st_mtime for p in files)
        for path in files:
            if path.suffix == '.svg':
                self.compress(path)
        files = [p for file in files for p in variants(file)]
        files = list(set(files))
        self.previews.append((accessed, files))
        if self.apply and not stamp.exists():
            stamp.touch(); os.utime(stamp, (accessed, accessed))

    def conversion(self, directory):
        record = load(directory / 'record.json')
        if record.get('status') not in ('ready', 'error', 'unsupported'):
            self.stats['skipped'] += 1
            return
        doc_id = record.get('document_id', '')
        if not UUID.fullmatch(doc_id):
            raise ValueError(f'Invalid document reference: {directory}')
        document = self.root / 'documents' / doc_id
        # No derived-data deletion unless the original and interpretation survive.
        if document.is_symlink() or not regular(document / 'source.pdf') or not regular(directory / 'intent.json'):
            self.stats['skipped'] += 1
            return
        marker = directory / 'storage-cleanup.json'
        if not marker.exists():
            with (document / 'source.pdf').open('rb') as source:
                if digest_stream(source) != record.get('source_sha256'):
                    raise ValueError(f'Original PDF hash mismatch: {directory}')
        preview_files = []
        for page in directories(directory, re.compile(r'page-[0-9]+')):
            self.compress(page / 'plot.hpgl')
            for name in DIAGNOSTICS:
                for path in variants(page / name):
                    self.remove(path)
            # Pre-summary workers embedded duplicate full path geometry in the
            # report. Migrate their small metrics/provenance before eviction.
            report, summary = page / 'report.json', page / 'summary.json'
            if regular(report) and not summary.exists() and self.apply:
                detail = load(report)
                compact = {k: detail[k] for k in SUMMARY_FIELDS if k in detail}
                if not isinstance(compact.get('metrics'), dict):
                    raise ValueError(f'Cannot extract legacy report summary: {report}')
                compact.update(schema='plotline.conversion-summary/1',
                               retention_note='Detailed path geometry expired; original HPGL and recipes retained')
                atomic_json(summary, compact)
                del detail
            if regular(summary) or regular(report) and not self.apply:
                for path in variants(page / 'report.json'):
                    self.remove(path)
            preview_files.extend(page / name for name in PREVIEWS)
        self.preview(directory, preview_files)
        if self.apply and not marker.exists():
            atomic_json(marker, dict(version=1, cleaned_at=self.now, original_verified_sha256=record['source_sha256']))

    def plot(self, directory):
        record = load(directory / 'record.json')
        execution = record.get('execution') or {}
        if execution.get('state') not in ('finished', 'reset_acknowledged') and not (record.get('phase') == 'failed' and not execution):
            self.stats['skipped'] += 1
            return
        # Only closed executions; uncertain or prepared attempts remain untouched.
        self.compress(directory / 'prepared.hpgl')
        path = directory / 'serial-timing.jsonl'
        existing = variants(path)
        if not existing:
            return
        created = float(record.get('created_at', existing[0].stat().st_mtime))
        summary = directory / 'timing-archive.json'
        if self.apply and not summary.exists():
            opener = gzip.open if existing[0].suffix == '.gz' else open
            with opener(existing[0], 'rb') as stream:
                digest = digest_stream(stream)
            atomic_json(summary, dict(schema=1, sha256=digest, captured_at=created,
                                      raw_log_expires_after_days=self.policy['timing_max_age_days']))
        self.compress(path)
        self.timings.append((created, variants(path)))

    def bounded(self, groups, age, limit, label):
        total = sum(p.stat().st_size for _, files in groups for p in files)
        for accessed, files in sorted(groups, key=lambda group: group[0]):
            if self.now - accessed > age * 86400 or total > limit:
                for path in files:
                    total -= path.stat().st_size
                    self.remove(path)
        self.stats[label + '_retained_bytes'] = total

    def run(self):
        # Never recurse through symlinks, hidden staging dirs or unknown file types.
        # The process lock excludes another collector. These are exclusively our
        # unpublished archives/metadata, left by a killed sweep; originals survive.
        for path in self.root.glob('.retention-*'):
            self.remove(path)
        operations = [(self.conversion, p) for p in directories(self.root / 'conversions')]
        operations += [(self.plot, p) for p in directories(self.root / 'plots')]
        for directory in directories(self.root):
            if regular(directory / 'job.json'):
                operations.append((self.library, directory))
        operations += [(self.document, p) for p in directories(self.root / 'documents')]
        for operation, directory in operations:
            try:
                for parent in [directory, *directories(directory, re.compile(r'page-[0-9]+'))]:
                    for path in parent.glob('.retention-*'):
                        self.remove(path)
                operation(directory)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.stats['errors'].append(str(error))
        self.bounded(self.previews, self.policy['preview_max_age_days'], self.policy['preview_max_bytes'], 'preview')
        self.bounded(self.timings, self.policy['timing_max_age_days'], self.policy['timing_max_bytes'], 'timing')
        if self.apply:
            atomic_json(self.root / 'storage-status.json', dict(last_sweep=self.now, policy=self.policy, **self.stats))
        return self.stats

    def library(self, directory):
        self.compress(directory / 'source.hpgl')
        self.preview(directory, [directory / 'preview.svg'])

    def document(self, directory):
        if load(directory / 'record.json').get('status') == 'ready':
            self.preview(directory, [p for p in directory.iterdir() if re.fullmatch(r'page-[0-9]+(?:-v[0-9]+)?\.png', p.name)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    # Separate invocations, including manual sweeps, share this lock.
    with (args.state / '.retention.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        result = Collector(args.state, args.apply).run()
        if not args.apply or result['compressed_files'] or result['removed_files'] or result['errors']:
            print(json.dumps(result, sort_keys=True))
        if result['errors']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
