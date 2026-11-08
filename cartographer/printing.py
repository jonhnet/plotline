"""Durable, resumable PDF → inspection → pen draft tasks. No physical Plot API."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.error import HTTPError
from jsonschema.exceptions import ValidationError

from .client import Ambiguous, Rejected, HandoffUnavailable, error_summary
from .model import digest
from .storage import atomic_json, Conflict
from print_contract import validate_intent

ACTIVE={'queued','uploading','inspecting','submitting','converting','preparing'}
SCHEMA='plotline.map-print-task/1'


def can_retry(value):
    # Old releases classified a known carousel-provenance rejection as an
    # unrepairable palette error. It is safe to retry after the driver fix.
    return (value['phase']=='failed' and not value.get('pending_post') and
            (value.get('retryable') or value.get('stale_inventory',False)))


def matches(requested,effective):
    """Driver-added defaults are allowed; every authored setting must agree."""
    if isinstance(requested,dict):
        return isinstance(effective,dict) and all(k in effective and matches(v,effective[k]) for k,v in requested.items())
    if isinstance(requested,list):
        return isinstance(effective,list) and len(requested)==len(effective) and all(matches(a,b) for a,b in zip(requested,effective))
    return requested==effective


class PrintTasks:
    def __init__(self,state,exports,client,poll_interval=1):
        self.directory=Path(state)/'submissions';self.directory.mkdir(parents=True,exist_ok=True)
        self.exports=Path(exports);self.client=client;self.poll_interval=poll_interval
        self.lock=threading.RLock();self.running=set();self.stop=threading.Event()
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='map-print')
        # Old upload-only receipts wait for an explicit Print click. New tasks
        # already authorized by that click resume after a backend restart.
        for path in self.directory.glob('*.json'):
            value=json.loads(path.read_text())
            if value.get('schema')==SCHEMA and value.get('phase') in ACTIVE:
                self._enqueue(path.stem)

    def path(self,key):
        if not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('Invalid print export key')
        return self.directory/(key+'.json')

    def load(self,key):return json.loads(self.path(key).read_text())

    def save(self,key,**changes):
        with self.lock:
            value=self.load(key);value.update(changes,updated_at=time.time());atomic_json(self.path(key),value)
            return value

    def _enqueue(self,key):
        with self.lock:
            if key not in self.running and not self.stop.is_set():
                self.running.add(key);self.pool.submit(self._run,key)

    def _legacy_receipt(self,intent):
        # Upgrading the composer changes its export cache key even when the
        # PDF and authored intent are unchanged. Find an old upload by both,
        # never by title/filename, so those receipts remain useful after reload.
        candidates={}
        for path in self.directory.glob('*.json'):
            old=json.loads(path.read_text())
            source=self.exports/path.stem/'map.print-intent.json'
            if old.get('schema')!=SCHEMA and old.get('id') and source.exists():
                if json.loads(source.read_text())==intent:candidates[old['id']]=(path.stem,old)
        return next(iter(candidates.values())) if len(candidates)==1 else (None,None)

    def request(self,export,recipe_hash,retry=False,source_document=None):
        export=Path(export);key=export.name;path=self.path(key)
        intent=json.loads((export/'map.print-intent.json').read_text())
        with self.lock:
            value=self.load(key) if path.exists() else None
            authoring=intent.get('authoring')
            if authoring:
                # Reusing an immutable print receipt keeps its original author.
                prior=(value or {}).get('intent',{}).get('authoring',{})
                if value and value.get('schema')==SCHEMA:
                    for field in ('document_id','document_revision'):
                        if field in prior:authoring[field]=prior[field]
                elif source_document:
                    authoring['document_id']=source_document['id']
                    if source_document.get('revision'):authoring['document_revision']=source_document['revision']
            validate_intent(intent,export/'map.pdf')
            if not value or value.get('schema')!=SCHEMA:
                prior_key,old=(key,value) if value else self._legacy_receipt(intent)
                old=old or {}
                value=dict(schema=SCHEMA,export_key=key,recipe_hash=recipe_hash,source_sha256=intent['source_sha256'],
                           intent=intent,intent_sha256=digest(intent),phase='queued',created_at=time.time(),
                           updated_at=time.time(),jobs=[],previous_jobs=[],attempts=[],retryable=False)
                if old.get('id'):
                    value.update(document_id=old['id'],document_status_url=old['status_url'],
                                 review_url=self.client.resolve(old['review_url'],public=True),phase='inspecting',upload_receipt=old)
                    value['resumed_from_export']=prior_key
                atomic_json(path,value)
            if (value['recipe_hash']!=recipe_hash or value['intent_sha256']!=digest(intent) or
                    value['source_sha256']!=intent['source_sha256']):
                raise Conflict('This print task belongs to different artwork or settings')
            if retry:value=self._retry(key,value)
            if value['phase']=='recovery' and value.get('document_id') and value.get('pending_post')=='draft':
                value=self.save(key,phase='inspecting',error=None)
            if value['phase'] in ACTIVE:self._enqueue(key)
            return self.public(key)

    def _retry(self,key,value):
        if can_retry(value):
            attempts=value['attempts']+[{k:value[k] for k in ['document_id','conversion_id','phase','error','diagnostic','updated_at'] if k in value}]
            return self.save(key,phase='inspecting' if value.get('document_id') else 'queued',conversion_id=None,
                             conversion_status_url=None,error=None,pending_post=None,retryable=False,stale_inventory=False,
                             explicit_retry=True,retry_from_conversion_id=value.get('conversion_id') or value.get('retry_from_conversion_id'),attempts=attempts)
        if value['phase'] not in ACTIVE:
            raise Conflict('Use PDF settings to recover this request; it cannot be blindly repeated')
        return value

    def retry(self,key,recipe_hash):
        """Retry an already-authorized, pinned task, even after a browser reload.

        The uploaded document and saved intent suffice; no new map render or
        local export is needed. Ambiguous POSTs and newer operator choices keep
        their existing recovery rules.
        """
        with self.lock:
            value=self.load(key)
            if value.get('schema')!=SCHEMA or value['recipe_hash']!=recipe_hash:
                raise Conflict('This print task belongs to different artwork or settings')
            value=self._retry(key,value)
            if value['phase'] in ACTIVE:self._enqueue(key)
            return self.public(key)

    def public(self,key):
        with self.lock:
            value=self.load(key)
            if value.get('schema')!=SCHEMA:raise FileNotFoundError('Use Print to resume this older upload receipt')
            if value.get('error'):value['error']=error_summary(value['error'])
            value['retryable']=can_retry(value)
            if value['phase']=='failed' and value.get('stale_inventory'):
                value['error']='An older PDF Studio rejected this palette’s carousel provenance. Retry conversion to use the saved document inks.'
            public={k:copy.deepcopy(v) for k,v in value.items() if k not in ['intent','document_status_url','conversion_status_url','pending_post','explicit_retry','upload_receipt','diagnostic','submission_key']}
            for attempt in public.get('attempts',[]):attempt.pop('diagnostic',None)
            return public

    def _failure(self,key,message,retryable=False,**extra):
        stale=any(word in str(message).lower() for word in ['carousel changed','carousel snapshot','palette snapshot'])
        self.save(key,phase='failed',error=error_summary(message),diagnostic=getattr(message,'diagnostic',str(message)),retryable=retryable,
                  stale_inventory=stale,pending_post=None,**extra)

    def _accept_conversion(self,key,row):
        self.save(key,conversion_id=row['id'],conversion_status_url=row['status_url'],
                  review_url=self.client.resolve(row['review_url'],public=True),phase='converting',
                  pending_post=None,explicit_retry=False,error=None)

    def _accept_upload(self,key,value,row):
        if value.get('submission_key') and (row.get('idempotency_key')!=value['submission_key'] or
                row.get('source_sha256')!=value['source_sha256'] or row.get('upload_intent_sha256')!=value['intent_sha256']):
            raise ValueError('Plotline Studios’s submission receipt does not match the saved PDF and print intent')
        self.save(key,document_id=row['id'],document_status_url=row['status_url'],review_url=row['review_url'],
                  phase='inspecting',pending_post=None,message='Inspecting PDF',connection_error=None)

    def _upload(self,key,value):
        current=self.client.check_handoff()
        supported=current['implemented'].get('pdf_submission_idempotency',False)
        submission_key=value.get('submission_key')
        if submission_key and not supported:
            raise HandoffUnavailable('Waiting for Plotline Studios’s submission recovery API to become available')
        if value.get('pending_post')=='upload':
            row=self.client.submission(submission_key)
            if row is not None:
                self._accept_upload(key,value,row);return
        # Absence at lookup is safe even if an earlier request is still arriving:
        # concurrent/replayed POSTs use the same key and exact payload.
        validate_intent(value['intent'],self.exports/key/'map.pdf')
        if supported and not submission_key:submission_key=str(uuid.uuid4())
        value=self.save(key,phase='uploading',pending_post='upload',submission_key=submission_key,
                        message='Uploading PDF and print intent')
        row=self.client.submit(self.exports/key/'map.pdf',value['intent'],checked=True,submission_key=submission_key)
        self._accept_upload(key,value,row)

    def _jobs(self,rows):
        return [dict(page_index=row['page_index'],url=self.client.resolve(row['url'],public=True),
                     **({'job_id':row['job_id']} if row.get('job_id') else {'status':'empty'})) for row in rows]

    def _reconcile_draft(self,key,value,document):
        requested=(document.get('draft') or {}).get('requested_conversion_id')
        for row in document.get('conversions',[]):
            if row['id']==requested and row.get('publish') and matches(value['intent'],row.get('intent')):
                self._accept_conversion(key,self.client.receipt(row));return True
        self.save(key,phase='recovery',error='The draft acknowledgement was lost. Check PDF settings; no duplicate draft was requested.',retryable=False)
        return False

    def _step(self,key,value):
        # A persisted POST marker means a process may have died after the server
        # committed but before its response was saved locally.
        if value.get('pending_post')=='upload' and not value.get('submission_key'):
            self.save(key,phase='recovery',review_url=self.client.resolve('/pdf',public=True),
                      error='The upload acknowledgement was lost. Check PDF settings for the received document; no duplicate was uploaded.',retryable=False)
            return
        if not value.get('document_id'):
            self._upload(key,value)
            return
        document=self.client.read(value['document_status_url'])
        if document.get('id')!=value['document_id'] or document.get('source_sha256')!=value['source_sha256']:
            self._failure(key,'The inspected document does not match this map’s source hash');return
        if document.get('intent_error'):
            self._failure(key,'Print intent rejected: '+str(document['intent_error']));return
        if document['status']=='error':
            self._failure(key,document.get('error','PDF inspection failed'));return
        if document['status'] in ('received','inspecting'):
            self.save(key,phase='inspecting',message='Inspecting PDF');return
        if document['status']!='ready':raise ValueError('Unrecognized PDF inspection status')
        draft=document.get('draft') or {}
        if value.get('pending_post')=='draft':
            self._reconcile_draft(key,value,document);return
        if not value.get('conversion_id'):
            if draft.get('jobs'):self.save(key,previous_jobs=self._jobs(draft['jobs']))
            if (value.get('explicit_retry') and draft.get('requested_conversion_id') and
                    draft['requested_conversion_id']!=value.get('retry_from_conversion_id')):
                self.save(key,phase='superseded',error='PDF settings selected a newer draft before this retry. Continue there to keep that choice.',retryable=False);return
            if draft.get('requested_conversion_id') and not value.get('explicit_retry'):
                # An operator may have converted an old upload-only receipt.
                # Adopt exactly matching settings, never overwrite other choices.
                for row in document.get('conversions',[]):
                    if row['id']==draft['requested_conversion_id'] and row.get('publish') and matches(value['intent'],row.get('intent')):
                        self._accept_conversion(key,self.client.receipt(row));return
                self.save(key,phase='superseded',error='PDF settings already selected another draft. Continue there to keep the operator’s choice.',retryable=False);return
            self.client.check_handoff()
            self.save(key,phase='submitting',pending_post='draft',message='Requesting pen conversion')
            self._accept_conversion(key,self.client.draft(value['document_id'],value['intent']));return
        cid=value['conversion_id']
        if draft.get('requested_conversion_id') and draft['requested_conversion_id']!=cid:
            self.save(key,phase='superseded',error='A newer draft was selected in PDF settings. This older request will not replace it.',retryable=False);return
        conversion=self.client.read(value['conversion_status_url'])
        if (conversion.get('id')!=cid or conversion.get('document_id')!=value['document_id'] or
                not matches(value['intent'],conversion.get('intent'))):
            self._failure(key,'Conversion settings do not match the exported map intent');return
        if conversion['status'] in ('queued','running'):
            self.save(key,phase='converting',message='Preparing pen draft · '+conversion['status'],progress=conversion.get('progress'));return
        if conversion['status'] in ('unsupported','error'):
            self._failure(key,conversion.get('error','Pen conversion failed'),retryable=True);return
        if conversion['status']!='ready':raise ValueError('Unrecognized pen conversion status')
        # This document read can precede conversion publication; the next tick
        # reads it again until the same requested conversion's draft is ready.
        if draft.get('status')=='error':
            self._failure(key,draft.get('error','Preparing the plot failed'),retryable=True);return
        if draft.get('status')=='ready' and draft.get('requested_conversion_id')==cid:
            jobs=self._jobs(draft.get('jobs',[]))
            self.save(key,phase='ready' if any(j.get('job_id') for j in jobs) else 'empty',jobs=jobs,
                      message='Pen draft ready' if any(j.get('job_id') for j in jobs) else 'No drawable pen paths',error=None,retryable=False)
        else:self.save(key,phase='preparing',message='Preparing HP7585 plot')

    def _run(self,key):
        try:
            while not self.stop.is_set():
                value=self.load(key)
                if value['phase'] not in ACTIVE:return
                try:
                    self._step(key,value)
                    if self.load(key).get('connection_error'):self.save(key,connection_error=None)
                except Ambiguous as error:
                    value=self.load(key)
                    if value.get('pending_post')=='upload' and value.get('submission_key'):
                        self.save(key,phase='uploading',message='Recovering the upload receipt from Plotline Studios',connection_error=str(error))
                    elif value.get('pending_post')=='draft':
                        try:self._reconcile_draft(key,value,self.client.read(value['document_status_url']))
                        except (OSError,ValueError):self.save(key,phase='recovery',error=str(error),retryable=False)
                    else:self.save(key,phase='recovery',error=str(error),review_url=self.client.resolve('/pdf',public=True),retryable=False)
                except Rejected as error:self._failure(key,error,retryable=error.status!=409)
                except HandoffUnavailable as error:
                    value=self.load(key)
                    if value.get('pending_post')=='upload' and value.get('submission_key'):
                        self.save(key,message=str(error),connection_error=str(error))
                    else:self._failure(key,error,retryable=True)
                except HTTPError as error:
                    if error.code<500:self._failure(key,f'Plotline Studios status is unavailable (HTTP {error.code}); check PDF settings')
                    else:self.save(key,message='Waiting for Plotline Studios to reconnect',connection_error=str(error))
                except FileNotFoundError:self._failure(key,'The pinned export is unavailable. Regenerate the map before printing.')
                except OSError as error:
                    self.save(key,message='Waiting for Plotline Studios to reconnect',connection_error=str(error))
                except (ValueError,KeyError,TypeError,ValidationError) as error:self._failure(key,error)
                except Exception as error:self._failure(key,'Print preparation failed: '+str(error))
                if self.stop.wait(self.poll_interval):return
        finally:
            with self.lock:
                self.running.discard(key)
                if not self.stop.is_set() and self.load(key)['phase'] in ACTIVE:self._enqueue(key)

    def close(self):
        self.stop.set();self.pool.shutdown(wait=True,cancel_futures=True)
