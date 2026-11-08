"""Cancellable isolated composers, immutable content cache and revision gates."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from . import ROOT
from .model import canonical, digest, validate_recipe
from .progress import estimate
from .storage import Conflict, atomic_json, identifier
from .retention import touch


class Jobs:
    def __init__(self, directory, version):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.version = version; self.lock = threading.RLock()
        self.jobs, self.sessions = {}, {}
        self.pool = ThreadPoolExecutor(max_workers=max(1,min(4,int(os.environ.get('PLOTLINE_MAP_WORKERS','2')))), thread_name_prefix='map-composer')

    def cancel(self, session, revision):
        identifier(session)
        if type(revision) is not int or revision < 1:
            raise ValueError('Preview revision must be a positive integer')
        with self.lock:
            prior = self.sessions.get(session)
            if prior and revision <= prior['revision']:
                raise Conflict('Obsolete preview revision')
            if prior and prior.get('job'):
                job = self.jobs[prior['job']]
                if job['status'] in ('queued', 'running'):
                    job['status'] = 'cancelled'
                    job['finished'] = time.time()
                    process = job.get('process')
                    if process and process.poll() is None:
                        process.terminate()
            self.sessions[session] = dict(revision=revision, job=None)

    def submit(self, recipe, quality, session, revision):
        validate_recipe(recipe)
        if quality not in ('draft', 'proof'):
            raise ValueError('Unknown preview quality')
        with self.lock:
            self.cancel(session, revision)
            identity = uuid.uuid4().hex
            key = digest([self.version, recipe, quality])
            job = dict(id=identity, status='queued', quality=quality, revision=revision, session=session,
                       recipe_hash=digest(recipe), cache_key=key, created=time.time(), recipe=recipe)
            self.jobs[identity] = job
            self.sessions[session]['job'] = identity
            # Bound in-memory completed task history; exports stay content-addressed.
            old = [j for j in self.jobs.values() if j['status'] not in ('queued', 'running')]
            for j in sorted(old, key=lambda j:j['created'])[:-200]:
                if self.sessions.get(j['session'], {}).get('job') != j['id']:
                    self.jobs.pop(j['id'], None)
            self.pool.submit(self.run, identity)
            return self.public(identity)

    def public(self, identity):
        with self.lock:
            if identity not in self.jobs:
                raise FileNotFoundError('Preview task not found; request a new preview')
            job=self.jobs[identity]
            final=self.directory/'exports'/job['cache_key']
            if job['status']=='ready':
                if not (final/'result.json').exists():
                    job.update(status='error',error='Preview expired; request a new preview from the saved map settings.')
                else:touch(final)
            result={k:v for k,v in job.items() if k not in ('recipe','process')}
            temp=self.directory/'tasks'/identity/'output'
            final=self.directory/'exports'/job['cache_key']
            result['scene_available']=(temp/'scene.json').exists() or job['status']=='ready'
            result['scene_revision']=2 if (temp/'scene-detail.json').exists() or (final/'scene-detail.json').exists() else 1
            now=job.get('finished',time.time())
            result['elapsed_seconds']=round(now-job['created'],1)
            progress=dict(stage='queued' if job['status']=='queued' else 'starting',
                          detail='Waiting for a preview worker' if job['status']=='queued' else 'Starting preview worker',
                          started_at=job.get('started',job['created']),updated_at=job.get('started',job['created']),history=[])
            try:
                file=(temp if (temp/'progress.json').exists() else final)/'progress.json'
                progress=json.loads(file.read_text())
            except (FileNotFoundError,ValueError):pass
            if job['status']=='ready':
                progress.update(stage='complete',detail='Loaded completed preview from cache' if job.get('cache_hit') else 'Composition complete',completed=None,total=None)
                progress.update(estimate('complete'))
            if 'overall_percent' not in progress:
                progress.update(estimate(progress['stage']))
            progress['stage_seconds']=round(max(0,now-progress['started_at']),1)
            progress['updated_seconds_ago']=round(max(0,now-progress['updated_at']),1)
            result['progress']=progress
            result['worker_alive']=bool(job.get('process') and job['process'].poll() is None)
            return result

    def run(self, identity):
        with self.lock:
            job = self.jobs[identity]
            if job['status'] == 'cancelled':
                return
            job['status'] = 'running'
            job['started'] = time.time()
        final = self.directory/'exports'/job['cache_key']
        temp = self.directory/'tasks'/identity
        try:
            with self.lock:
                job['cache_hit']=(final/'result.json').exists()
            if not job['cache_hit']:
                temp.mkdir(parents=True, exist_ok=True)
                atomic_json(temp/'request.json', job['recipe'])
                with (temp/'composer.log').open('w') as log:
                    with self.lock:
                        if job['status'] == 'cancelled':
                            return
                        job['process'] = subprocess.Popen([sys.executable, '-m', 'cartographer.compose',
                            str(temp/'request.json'), str(temp/'output'), '--quality', job['quality'],
                            '--cache', str(self.directory/'geometry'), '--version', self.version],
                            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    try:
                        code = job['process'].wait(timeout=600)
                    except subprocess.TimeoutExpired:
                        job['process'].kill(); job['process'].wait()
                        raise ValueError('Composition exceeded ten minutes. Reduce the extent or detail.')
                if job['status'] == 'cancelled':
                    return
                if code:
                    lines = (temp/'composer.log').read_text().strip().splitlines()
                    detail = lines[-1].removeprefix('ValueError: ') if lines else 'Worker exited without a diagnostic'
                    raise ValueError('Composition failed: '+detail)
                # A result is published only after all PDF/intent checks succeed.
                final.parent.mkdir(parents=True, exist_ok=True)
                with self.lock:
                    if job['status'] == 'cancelled':
                        return
                    if not final.exists():
                        os.replace(temp/'output', final)
            result = json.loads((final/'result.json').read_text())
            with self.lock:
                if job['status'] != 'cancelled':
                    touch(final)
                    job.update(status='ready', finished=time.time(), result=result, artifacts='/artifacts/'+job['cache_key']+'/')
        except Exception as error:
            with self.lock:
                if job['status'] != 'cancelled':
                    job.update(status='error', finished=time.time(), error=str(error))
        finally:
            with self.lock:
                job.pop('process', None)

    def export(self, identity, session, revision, recipe_hash):
        with self.lock:
            job = self.public(identity)
            current = self.sessions.get(session, {})
            if (job['session'] != session or job['revision'] != revision or job['recipe_hash'] != recipe_hash
                    or current.get('job') != identity or current.get('revision') != revision):
                raise Conflict('The preview is stale. Generate the current map before sending it.')
            if job['quality'] != 'proof' or job['status'] != 'ready':
                raise Conflict('Generate a full-detail map first')
            return self.directory/'exports'/job['cache_key']

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                if job['status'] in ('queued', 'running'):
                    job['status'] = 'cancelled'
                    if job.get('process'):
                        job['process'].terminate()
        self.pool.shutdown(wait=True, cancel_futures=True)
