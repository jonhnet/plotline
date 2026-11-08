#!/usr/bin/env python3
"""Fail a container build if its source copy includes any unlisted file."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
allowed = {line[1:] for line in (root / '.dockerignore').read_text().splitlines()
           if line.startswith('!') and not line.endswith('/')}
found = set()
for entry in root.iterdir():
    if entry.name in ('.venv', 'node_modules'):
        continue  # Installed by earlier dependency-only build steps.
    if entry.is_symlink():
        raise SystemExit(f'Unexpected source symlink: {entry.name}')
    if entry.is_dir():
        found.update(str(p.relative_to(root)) for p in entry.rglob('*') if not p.is_dir())
    else:
        found.add(entry.name)
unexpected = found - allowed
missing = allowed - found
if unexpected or missing:
    raise SystemExit(f'Container source mismatch: unexpected={sorted(unexpected)[:10]}, missing={sorted(missing)[:10]}')
print(f'Container source verified: {len(found)} public files; no private state or host builds.')
