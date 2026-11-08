"""One durable authoring action: saved map → full detail → HPGL studio.

The recipe is immutable once submitted. Preview tabs can close or edit without
cancelling this explicitly requested preparation. Physical Plot is never called.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import re
import threading
import time
import uuid

from .model import digest, validate_recipe
from .printing import ACTIVE, SCHEMA as PRINT_SCHEMA, can_retry
from .storage import atomic_json, Conflict

SCHEMA='plotline.map-preparation/1'


class Preparations:
    def __init__(self,state,jobs,printing,poll_interval=.5):
        self.directory=Path(state)/'preparations';self.directory.mkdir(parents=True,exist_ok=True)
        self.jobs=jobs;self.printing=printing;self.poll_interval=poll_interval
        self.lock=threading.RLock();self.running=set();self.stop=threading.Event()
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='map-plot')
        for path in self.directory.glob('*.json'):
            value=json.loads(path.read_text())
            if value['phase'] in ('rendering','handoff'):self.enqueue(path.stem)

    def path(self,key):
        if not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('Invalid map preparation ID')
        return self.directory/(key+'.json')

    def load(self,key):return json.loads(self.path(key).read_text())

    def save(self,key,**changes):
        with self.lock:
            value=self.load(key);value.update(changes,updated_at=time.time())
            atomic_json(self.path(key),value);return value

    def enqueue(self,key):
        with self.lock:
            if key not in self.running and not self.stop.is_set():
                self.running.add(key);self.pool.submit(self.run,key)

    def existing_print(self,recipe_hash):
        # Reuse the exact design's previous handoff across renderer upgrades.
        # This also recovers the old two-button workflow without re-rendering.
        rows=[json.loads(p.read_text()) for p in self.printing.directory.glob('*.json')]
        rows=[r for r in rows if r.get('schema')==PRINT_SCHEMA and r.get('recipe_hash')==recipe_hash]
        return max(rows,key=lambda r:r['created_at'],default=None)

    def request(self,recipe,retry=False,rebuild=False,source_document=None):
        validate_recipe(recipe)
        key=digest(recipe)
        with self.lock:
            path=self.path(key)
            if path.exists():
                value=self.load(key)
                if value['recipe']!=recipe:raise Conflict('Preparation belongs to another map')
                if rebuild and value['phase'] not in ('rendering','handoff'):
                    # Rebuild with the current composer, keeping the durable
                    # document pointer. Earlier exports/print receipts stay intact.
                    value=self.save(key,phase='rendering',export_key=None,render_job_id=None,
                                    error=None,retry_requested=False,started_at=time.time())
                elif retry and (value['phase']=='failed' or value.get('export_key') and can_retry(self.printing.load(value['export_key']))):
                    value=self.save(key,phase='handoff' if value.get('export_key') else 'rendering',
                                    error=None,retry_requested=True,render_job_id=None,started_at=time.time())
            else:
                previous=None if rebuild else self.existing_print(key)
                value=dict(schema=SCHEMA,id=key,recipe_hash=key,recipe=copy.deepcopy(recipe),
                           source_document=copy.deepcopy(source_document),phase='handoff' if previous else 'rendering',created_at=time.time(),updated_at=time.time(),started_at=time.time(),
                           export_key=previous['export_key'] if previous else None,
                           retry_requested=bool(previous and can_retry(previous)))
                atomic_json(path,value)
            if value['phase'] in ('rendering','handoff'):self.enqueue(key)
            return self.public(key)

    def public(self,key):
        with self.lock:
            value=self.load(key)
            result={k:copy.deepcopy(v) for k,v in value.items() if k not in ('recipe','retry_requested')}
            result['retryable']=value['phase']=='failed'
            if value.get('export_key'):
                printed=self.printing.public(value['export_key'])
                result.update(printed,id=key,recipe_hash=value['recipe_hash'],created_at=value['created_at'])
                if value.get('retry_requested'):
                    result.update(phase='queued',error=None,retryable=False,message='Resuming the saved map preparation')
                if value.get('error'):result.update(phase='failed',error=value['error'],retryable=True)
            elif value.get('render_job_id'):
                try:result['render']=self.jobs.public(value['render_job_id'])
                except FileNotFoundError:pass
            return result

    def step(self,key):
        value=self.load(key)
        if value['phase']=='rendering':
            try:job=self.jobs.public(value.get('render_job_id'))
            except FileNotFoundError:
                session='plot-'+uuid.uuid4().hex
                job=self.jobs.submit(value['recipe'],'proof',session,1)
                self.save(key,render_job_id=job['id'])
            if job['status'] in ('error','cancelled'):
                self.save(key,phase='failed',error=job.get('error','Map preparation was interrupted'));return
            if job['status']!='ready':return
            unmeasured=job.get('result',{}).get('unmeasured_inks',[])
            if unmeasured:
                details='; '.join(i['label']+' ('+', '.join(r['region']+' '+r['role'] for r in i['roles'])+')' for i in unmeasured)
                self.save(key,phase='failed',error='Ink diameter missing: '+details+'. Choose a measured ink for these roles under Feature inks.')
                return
            export=self.jobs.export(job['id'],job['session'],job['revision'],value['recipe_hash'])
            # PrintTasks durably records its intent before it can upload. If we
            # restart before saving export_key, requesting it again is idempotent.
            self.printing.request(export,value['recipe_hash'],**({'source_document':value['source_document']} if value.get('source_document') else {}))
            self.save(key,phase='handoff',export_key=export.name)
        else:
            if value.get('retry_requested'):
                # Clear before the call: on restart the existing task receipt
                # determines what happened; never blindly resubmit a POST.
                self.save(key,retry_requested=False)
                printed=self.printing.load(value['export_key'])
                if can_retry(printed):self.printing.retry(value['export_key'],value['recipe_hash'])
            printed=self.printing.public(value['export_key'])
            if printed['phase'] not in ACTIVE:self.save(key,phase=printed['phase'])

    def run(self,key):
        try:
            while not self.stop.is_set():
                with self.lock:
                    if self.load(key)['phase'] not in ('rendering','handoff'):return
                    try:self.step(key)
                    except Exception as error:self.save(key,phase='failed',error=str(error))
                self.stop.wait(self.poll_interval)
        finally:
            with self.lock:
                self.running.discard(key)
                if not self.stop.is_set() and self.load(key)['phase'] in ('rendering','handoff'):self.enqueue(key)

    def close(self):
        self.stop.set();self.pool.shutdown(wait=True,cancel_futures=True)
