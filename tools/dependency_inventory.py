#!/usr/bin/env python3
"""Record declared dependency licenses from Cargo/npm and installed Python metadata.

This is a source-release inventory, not a substitute for license texts when
distributing compiled dependencies. Run after the documented environment setup.
"""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def inventory():
    result = {'scope': 'Declared licenses; source distribution installs dependencies separately. Python entries describe the tested environments, not a complete platform-independent lock.',
              'rust': {}, 'npm': {}, 'python': {}, 'inputs': {}}
    for name in ('Cargo.toml', 'controller/Cargo.toml', 'native/cartographer-geometry/Cargo.toml'):
        data = json.loads(subprocess.check_output(['cargo', 'metadata', '--locked', '--offline',
                          '--format-version', '1', '--manifest-path', name], cwd=ROOT))
        result['rust'][name] = sorted([
            {k: p[k] for k in ('name', 'version', 'license', 'repository')}
            for p in data['packages'] if p.get('source')], key=lambda p: (p['name'], p['version']))
    for name in ('package-lock.json', 'controller/package-lock.json'):
        data = json.loads((ROOT/name).read_text())
        result['npm'][name] = [dict(name=k.removeprefix('node_modules/'), version=v['version'],
                                    license=v.get('license'), integrity=v.get('integrity'))
                               for k, v in sorted(data['packages'].items()) if k]
    # Ask each interpreter about its own distributions; don't mix environments.
    query = '''import importlib.metadata as m,json
print(json.dumps([{'name':d.metadata['Name'],'version':d.version,
'license':d.metadata.get('License-Expression') or d.metadata.get('License'),
'license_classifiers':[v for v in d.metadata.get_all('Classifier',[]) if v.startswith('License ::')],
'license_files':[str(p) for p in d.files or [] if 'license' in str(p).lower() or 'copying' in str(p).lower()]}
for d in m.distributions() if d.metadata['Name'].lower() not in ('pip','setuptools')]))'''
    for name in ('.venv', 'build/calibration-venv', 'build/timing-venv'):
        python = ROOT/name/'bin/python'
        if python.exists():
            entries = json.loads(subprocess.check_output([str(python), '-c', query]))
            result['python'][name] = sorted(entries, key=lambda p: p['name'].lower())
    inputs = ['Cargo.lock', 'controller/Cargo.lock', 'native/cartographer-geometry/Cargo.lock',
              'package-lock.json', 'controller/package-lock.json']
    inputs += [str(p.relative_to(ROOT)) for p in ROOT.glob('requirements-*.txt')]
    for name in sorted(inputs):
        result['inputs'][name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


if __name__ == '__main__':
    target = ROOT/'docs/dependency-inventory.json'
    target.write_text(json.dumps(inventory(), indent=2) + '\n')
    print(target)
