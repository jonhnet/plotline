"""Summarize native conversion time, source recovery and changed plot workload.

Usage: python tools/profiling/clearance_compare.py OLD_DIR NEW_DIR > audit.json
Directories contain summary.json, plot.hpgl and optional SVG artifacts. Timing
comes from GNU time -v at the sibling NAME-time.txt. This summarizes the native
footprint audits; it is not an independent geometric proof or plot-time model.
"""
import argparse
import hashlib
import json
from pathlib import Path


def read_run(directory):
    summary = json.loads((directory / 'summary.json').read_text())
    result = {key: summary[key] for key in (
        'timing', 'metrics', 'verification', 'detail_loss', 'by_ink', 'by_feature')}
    result['hashes'] = {}
    for name in ('plot.hpgl', 'coverage.svg', 'hpgl.svg', 'detail-loss.svg'):
        path = directory / name
        if path.exists():
            with path.open('rb') as stream:
                result['hashes'][name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    timing = directory.with_name(directory.name + '-time.txt')
    if timing.exists():
        fields = dict(line.strip().rsplit(': ', 1) for line in timing.read_text().splitlines()
                      if ': ' in line)
        elapsed = fields['Elapsed (wall clock) time (h:mm:ss or m:ss)']
        seconds = 0.
        for part in elapsed.split(':'):
            seconds = seconds * 60 + float(part)
        result['wall_s'] = seconds
        result['cpu_s'] = float(fields['User time (seconds)']) + float(fields['System time (seconds)'])
        result['max_rss_kib'] = int(fields['Maximum resident set size (kbytes)'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    old, new = read_run(args.baseline), read_run(args.candidate)
    gained = {ink: (entry['unrepresented_area_pt2'] - new['by_ink'][ink]['unrepresented_area_pt2'])
              * (25.4 / 72) ** 2 for ink, entry in old['by_ink'].items()}
    features = {name: {
        'pen_down_change_mm': entry['pen_down_mm'] - old['by_feature'][name]['pen_down_mm'],
        'path_change': entry['paths'] - old['by_feature'][name]['paths'],
    } for name, entry in new['by_feature'].items()}
    print(json.dumps(dict(baseline=old, candidate=new,
                         source_area_restored_mm2=gained, feature_changes=features), indent=2))


if __name__ == '__main__':
    main()
