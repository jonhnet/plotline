#!/usr/bin/env python3
"""Export a reviewable source snapshot without copying private Git history.

Reads committed blobs only. Never creates a remote or publishes anything.
Run from the private development checkout after committing the candidate.
"""
import argparse
import fnmatch
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def matches(path, patterns):
    return any(path.startswith(p) if p.endswith('/') else fnmatch.fnmatchcase(path, p)
               for p in patterns)


def selection(paths, config, samples):
    chosen = {p for p in paths if matches(p, config['include'])
              and not matches(p, config['exclude'])}
    for key, sample in samples.items():
        prefix = f'docs/readme/examples/{key}/'
        assets = [sample.get(k) for k in ('image', 'video', 'poster')]
        assets += [filename for _, filename in sample['files']]
        for name in filter(None, assets):
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Unsafe example path: {key}/{name}')
            path = prefix + name
            if path not in paths:
                raise ValueError(f'Example is not committed: {path}')
            chosen.add(path)
        capture = prefix + 'capture/provenance.json'
        if capture in paths:
            chosen.add(capture)
    for path in chosen:
        if matches(path, config['forbidden_prefixes']):
            raise ValueError(f'Private path selected: {path}')
    return sorted(chosen)


def check_readme(markdown, chosen):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []

        def handle_starttag(self, tag, attrs):
            self.urls.extend(v for k, v in attrs if k in ('href', 'src') and v)

    links = Links()
    links.feed(markdown)
    urls = links.urls + re.findall(r'\]\(([^\s)]+)\)', markdown)
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme or not parsed.path:
            continue
        if unquote(parsed.path) not in chosen:
            raise ValueError(f'README link missing from public package: {url}')


def export(ref, output):
    revision = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    read = lambda path: git('show', f'{revision}:{path}')
    config = json.loads(read('packaging/public-source.json'))
    samples = json.loads(read('docs/readme/samples.json'))
    paths = set(git('ls-tree', '-rz', '--name-only', revision).decode().split('\0')) - {''}
    chosen = selection(paths, config, samples)
    check_readme(read('README.md').decode(), set(chosen))
    if output.exists():
        raise ValueError(f'Choose a new output directory; refusing to replace {output}')
    # git archive supplies immutable bytes and executable bits, with no .git.
    archive = git('archive', '--format=tar', revision, '--', *chosen)
    hashes = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        members = []
        for item in bundle:
            if item.isdir():
                continue
            if not item.isfile() or item.name not in chosen:
                raise ValueError(f'Unexpected archive member: {item.name}')
            if item.size > 50 * 1024**2:
                raise ValueError(f'Unexpected large file: {item.name}')
            data = bundle.extractfile(item).read()
            # Narrow checks for actual key blocks or accidentally copied share URLs.
            if re.search(rb'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{20}', data):
                raise ValueError(f'Private key material in {item.name}')
            if re.search(rb'https?://immich\.jonh\.net/share/[A-Za-z0-9_-]{20}', data):
                raise ValueError(f'Private share URL in {item.name}')
            hashes[item.name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
            members.append((item.name, item.mode, data))
    output.mkdir(parents=True)
    for name, mode, data in members:
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if mode & 0o111 else 0o644)
    manifest = {'source_revision': revision, 'files': hashes}
    (output/'SOURCE_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    report = {
        'source_revision': revision, 'file_count': len(hashes),
        'bytes': sum(v['bytes'] for v in hashes.values()),
        'excluded_tracked_files': sorted(paths - set(chosen)),
        'open_decisions': config['open_decisions'],
        'checks': ['Committed blobs only; no Git history', 'README local links present',
                   'No selected private paths, key blocks or Immich share links', 'No file above 50 MiB'],
        'limits': 'Candidate for review, not legal clearance or a complete secret audit. No binaries or dependency packages are bundled.',
    }
    report_path = output.with_name(output.name + '-report.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'{output}: {len(hashes)} files, {report["bytes"]/1024**2:.1f} MiB; no Git history')
    print(f'Review report: {report_path}')
    for decision in config['open_decisions']:
        print('Open: ' + decision)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.ref, args.output.resolve())
