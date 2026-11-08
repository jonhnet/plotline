#!/usr/bin/env python3
"""Measure a frozen Map Studio bundle without network, service writes or printing.

Use an empty private --cache for a full rebuild, then reuse it for a warm run.
Use a separate empty cache with --profile; profiler times are not latency results.
"""
import argparse
import cProfile
import hashlib
import importlib.abc
import io
import json
import os
from pathlib import Path
import pstats
import resource
import subprocess
import sys
import time
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def benchmark(bundle, output, cache, profile=False, without_layout=False):
    # Diagnostic only: PyMuPDF catches ImportError for this initializer. Keep
    # the installed environment intact while measuring its otherwise-unused ML
    # sessions. This is not an application dependency-management solution.
    class NoLayout(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == 'pymupdf.layout':
                raise ImportError('Layout initialization suppressed for benchmark')
    blocker = NoLayout()
    import_started = time.perf_counter()
    if without_layout:
        sys.meta_path.insert(0, blocker)
    try:
        from cartographer.compose import compose
        from cartographer.sources import source_version
    finally:
        if without_layout:
            sys.meta_path.remove(blocker)
    import_seconds = time.perf_counter() - import_started
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cache = Path(cache)
    raw_cache = cache / 'http'
    raw_cache.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as archive:
        recipe = json.loads(archive.read('recipe.json'))
        provenance = json.loads(archive.read('provenance.json'))
        sources = provenance['inputs']['map'].get('tiles', [])
        sources += (provenance['inputs'].get('elevation') or {}).get('tiles', [])
        for source in sources:
            key = hashlib.sha256(source['url'].encode()).hexdigest()
            assert key == source['cache_key'], 'Invalid source key'
            raw = archive.read('sources/' + key)
            assert hashlib.sha256(raw).hexdigest() == source['sha256'], 'Invalid source bytes'
            dest = raw_cache / key
            if dest.exists():
                assert dest.read_bytes() == raw, 'Cache contains different source bytes'
            else:
                dest.write_bytes(raw)
    version = source_version()
    profiler = cProfile.Profile() if profile else None
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.perf_counter()
    # Keep the real cache-hit fetch code; any missing input fails without network.
    with patch('cartographer.sources.urlopen', side_effect=RuntimeError('Benchmark source missing from frozen bundle')):
        if profiler:
            result = profiler.runcall(compose, recipe, output / 'map', provenance['quality'], cache, version)
        else:
            result = compose(recipe, output / 'map', provenance['quality'], cache, version)
    elapsed = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_SELF)
    progress = json.loads((output / 'map/progress.json').read_text())
    # Linux exposes the process high-water mark independently of getrusage.
    # Retain both so anomalous inherited/thread accounting is visible.
    proc_memory = {}
    status = Path('/proc/self/status')
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith(('VmRSS:', 'VmHWM:')):
                key, value, _ = line.split(); proc_memory[key.rstrip(':')] = int(value)
    report = dict(quality=provenance['quality'], profiled=profile,
                  layout_suppressed=without_layout, import_seconds=import_seconds,
                  onnx_loaded='onnxruntime' in sys.modules,
                  layout_active=callable(sys.modules['pymupdf']._get_layout),
                  bundle_sha256=hashlib.sha256(Path(bundle).read_bytes()).hexdigest(),
                  recipe_hash=result['recipe_hash'], composer_version=version,
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1], text=True).strip(),
                  thread_environment={k: os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'PLOTLINE_MAP_GEOMETRY', 'PLOTLINE_MAP_GEOMETRY_THREADS')},
                  wall_seconds=elapsed, user_seconds=after.ru_utime-before.ru_utime,
                  system_seconds=after.ru_stime-before.ru_stime, peak_rss_kib=after.ru_maxrss, proc_memory_kib=proc_memory,
                  initial_peak_rss_kib=before.ru_maxrss,
                  stages=progress['history'], nodes=result['build_cache'],
                  feature_counts=result['feature_counts'], depth_audit=result['depth_audit'])
    (output / 'measurement.json').write_text(json.dumps(report, indent=2) + '\n')
    if profiler:
        profiler.dump_stats(output / 'profile.pstats')
        text = io.StringIO()
        stats = pstats.Stats(profiler, stream=text).strip_dirs()
        stats.sort_stats('cumulative').print_stats(50)
        stats.sort_stats('tottime').print_stats(50)
        (output / 'profile.txt').write_text(text.getvalue())
    print(json.dumps({k: report[k] for k in ('quality', 'profiled', 'wall_seconds', 'user_seconds', 'system_seconds', 'peak_rss_kib')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--without-layout', action='store_true', help='Diagnostic import guard; never changes the installed environment')
    args = parser.parse_args()
    benchmark(args.bundle, args.output, args.cache, args.profile, args.without_layout)
