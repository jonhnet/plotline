"""Bound rebuildable map caches while preserving recipes and source downloads."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time

from .storage import atomic_json

DEFAULTS = dict(export_max_bytes=512*1024**2, geometry_max_bytes=512*1024**2,
                max_age_days=7, idle_grace_seconds=900)


def preserve_sources(state, build):
    """HTTP responses are frozen inputs, not decoded geometry. Move to backup state.

    Runs before workers start. An interrupted move is completed on the next boot.
    Existing unrelated destinations are never merged or overwritten implicitly.
    """
    state.mkdir(parents=True, exist_ok=True)
    source = build/'geometry/http'; saved = state/'map-sources-http'
    source.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if source.resolve() != saved.resolve():
            raise ValueError('Map source cache points outside its retained input store')
        return
    if source.exists():
        if saved.exists():
            raise ValueError('Both old and retained map source stores exist; reconcile them before starting')
        os.rename(source, saved)
    else:
        saved.mkdir(exist_ok=True)
    source.symlink_to(saved.resolve(), target_is_directory=True)


def touch(directory):
    (directory/'.last-access').touch()


def files(directory):
    # Do not follow directory links or delete bundles containing unknown links.
    paths = list(directory.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError(f'Cache bundle contains a symlink: {directory}')
    return [p for p in paths if p.is_file()]


def sweep(state, build, policy=None, now=None):
    """Called with service/job locks held and no workers active; independently testable."""
    policy = DEFAULTS if policy is None else policy
    now = time.time() if now is None else now
    result = dict(reclaimed_bytes=0, expired_exports=0, expired_tasks=0, expired_geometry=0)
    exports = build/'exports'
    candidates = []
    for directory in exports.iterdir() if exports.is_dir() else []:
        if directory.is_symlink() or not directory.is_dir() or not re.fullmatch('[0-9a-f]{64}',directory.name):continue
        bundle = files(directory)
        if not bundle or not (directory/'result.json').is_file():continue
        access = directory/'.last-access'
        accessed = access.stat().st_mtime if access.exists() else max(p.stat().st_mtime for p in bundle)
        candidates.append((accessed, directory, sum(p.stat().st_size for p in bundle)))
    total = sum(size for _,_,size in candidates)
    for accessed,directory,size in sorted(candidates):
        if now-accessed < policy['idle_grace_seconds']:continue
        if now-accessed <= policy['max_age_days']*86400 and total <= policy['export_max_bytes']:continue
        receipt = state/'submissions'/f'{directory.name}.json'
        if receipt.is_file():
            submission = json.loads(receipt.read_text())
            # A failed/ambiguous upload may still need this exact PDF for retry.
            if not submission.get('document_id') and not submission.get('id'):continue
        saved = state/'export-recipes'/directory.name
        for name in ('recipe.json','provenance.json','map.print-intent.json','result.json'):
            path = directory/name
            if path.is_file():
                value = json.loads(path.read_text())
                target = saved/name
                if target.exists() and json.loads(target.read_text()) != value:
                    if name == 'recipe.json':
                        raise ValueError(f'Retained recipe differs: {target}')
                    # Rebuilding the same recipe can have different run times
                    # and cache-hit diagnostics. Keep both small records, while
                    # allowing the regenerated bulky artifacts to expire again.
                    key = hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
                    target = saved/'rebuilds'/f'{key}-{name}'
                atomic_json(target,value)
        if not (saved/'recipe.json').is_file():continue
        shutil.rmtree(directory)
        total -= size; result['reclaimed_bytes'] += size; result['expired_exports'] += 1
    result['export_retained_bytes'] = total
    tasks = build/'tasks'
    for directory in tasks.iterdir() if tasks.is_dir() else []:
        if directory.is_symlink() or not directory.is_dir() or not re.fullmatch('[0-9a-f]{32}',directory.name):continue
        bundle = files(directory)
        if not bundle or now-max(p.stat().st_mtime for p in bundle) < policy['idle_grace_seconds']:continue
        request = directory/'request.json'
        if not request.is_file():continue
        # Even an unsaved or cancelled experiment keeps its authoring inputs.
        log = directory/'composer.log'
        tail = ''
        if log.is_file():
            with log.open('rb') as stream:
                stream.seek(max(0,log.stat().st_size-4096));tail=stream.read().decode(errors='replace')
        atomic_json(state/'preview-recipes'/f'{directory.name}.json',
                    dict(recipe=json.loads(request.read_text()), diagnostic_tail=tail))
        result['reclaimed_bytes'] += sum(p.stat().st_size for p in bundle)
        shutil.rmtree(directory); result['expired_tasks'] += 1
    geometry = build/'geometry'
    candidates = []
    for directory in (geometry,geometry/'decoded',geometry/'nodes'):
        if directory.is_symlink() or not directory.is_dir():continue
        for path in directory.glob('*.pickle'):
            if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}\.pickle',path.name):continue
            candidates.append((path.stat().st_mtime,path,path.stat().st_size))
    total = sum(size for _,_,size in candidates)
    for accessed,path,size in sorted(candidates):
        if now-accessed < policy['idle_grace_seconds']:continue
        if now-accessed <= policy['max_age_days']*86400 and total <= policy['geometry_max_bytes']:continue
        # Share the composer's per-key lock, including command-line composers.
        with path.with_suffix('.lock').open('a+b') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:continue
            if path.stat().st_mtime != accessed:continue
            path.unlink()
        total -= size; result['reclaimed_bytes'] += size; result['expired_geometry'] += 1
    result['geometry_retained_bytes'] = total
    # Cancelled composers can die between writing a cache temp and publishing
    # it. Discard only our UUID-named partial files, under the matching key lock.
    for directory in (geometry,geometry/'decoded',geometry/'nodes',state/'map-sources-http'):
        if directory.is_symlink() or not directory.is_dir():continue
        for path in directory.glob('*.tmp'):
            if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}\.[0-9a-f]{32}\.tmp',path.name):continue
            if now-path.stat().st_mtime < policy['idle_grace_seconds']:continue
            with (directory/(path.name[:64]+'.lock')).open('a+b') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:continue
                result['reclaimed_bytes'] += path.stat().st_size
                path.unlink()
    atomic_json(state/'storage-status.json',dict(last_sweep=now,policy=policy,**result))
    return result


class Retention:
    def __init__(self, app):
        self.app=app;self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,name='map-cache-cleanup',daemon=True)
        self.thread.start()

    def run(self):
        app=self.app
        while not self.stop.wait(60):
            try:
                # Same order as preparation → print/job work. HTTP requests also
                # hold cache_lock while obtaining and reading artifact paths.
                with app.cache_lock, app.preparations.lock, app.print_tasks.lock, app.jobs.lock:
                    if app.preparations.running or app.print_tasks.running or any(j['status'] in ('queued','running') for j in app.jobs.jobs.values()):continue
                    policy=dict(DEFAULTS)
                    path=app.state/'storage-policy.json'
                    if path.exists():
                        overrides=json.loads(path.read_text())
                        if overrides.keys()-policy.keys():raise ValueError('Unknown map storage policy key')
                        policy.update(overrides)
                    if any(type(v) is not int or v<0 for v in policy.values()):raise ValueError('Map cache limits must be nonnegative integers')
                    result=sweep(app.state,app.build,policy)
                    if result['reclaimed_bytes']:print('Map storage: '+json.dumps(result),flush=True)
            except Exception as error:print(f'Map cache cleanup failed: {error}',flush=True)

    def close(self):
        self.stop.set();self.thread.join()
