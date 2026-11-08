#!/usr/bin/env python3
"""Reject unlisted source files and missing runtime maintenance helpers."""
from pathlib import Path

# Keep this independent of the generated allowlist: matching omissions in the
# context and image must fail too, before any compilation starts.
REQUIRED_RUNTIME_FILES = {
    'controller/scripts/retention.py',
    'controller/retention-policy.json',
    'controller/scripts/render-page.py',
}

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
missing_runtime = REQUIRED_RUNTIME_FILES - found
if missing_runtime:
    raise SystemExit(f'Missing required runtime files: {sorted(missing_runtime)}')
if unexpected or missing:
    raise SystemExit(f'Container source mismatch: unexpected={sorted(unexpected)[:10]}, missing={sorted(missing)[:10]}')
print(f'Container source verified: {len(found)} public files; no private state or host builds.')
