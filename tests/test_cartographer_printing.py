import copy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from cartographer.client import PrintClient,Ambiguous,Rejected
from cartographer.model import digest
from cartographer.printing import PrintTasks
from cartographer.storage import atomic_json,Conflict
from make_boundary_fixture import make_fixture


class Driver:
    """Controllable inspection, conversion and separately delayed publication."""
    def __init__(self,intent):
        self.intent=intent;self.uploads=0;self.posts=[];self.gets=0;self.polls=0;self.publish_polls=0
        self.lost_upload=False;self.lost_draft=False;self.intent_error=None;self.failure=None;self.reject=None
        self.superseded=False;self.blank=False;self.hold=False;self.converted=False
        self.doc=None;self.conversion=None
        self.idempotency=True;self.upload_attempts=[];self.lookups=0;self.receipts={};self.drop_before_commit=False;self.lookup_offline=False
        self.urls=PrintClient('http://127.0.0.1:9191','unused','http://public.example:9191')
    def resolve(self,*args,**kwargs):return self.urls.resolve(*args,**kwargs)
    def check_handoff(self):return dict(implemented=dict(pdf_submission_idempotency=self.idempotency))
    def submission(self,key):
        self.lookups+=1
        if self.lookup_offline:raise OSError('Disconnected during receipt lookup')
        return copy.deepcopy(self.receipts.get(key))
    def submit(self,pdf,intent,checked=False,submission_key=None):
        assert intent==self.intent
        self.upload_attempts.append(submission_key)
        if self.drop_before_commit:
            self.drop_before_commit=False;raise Ambiguous('Disconnected before receiving the upload')
        if submission_key in self.receipts:return copy.deepcopy(self.receipts[submission_key])
        self.uploads+=1
        self.doc=dict(id='document',status='received',source_sha256=intent['source_sha256'],
                      status_url='/api/v1/documents/document',review_url='/pdf?document=document',draft={'status':'none','jobs':[]},conversions=[])
        if submission_key:
            self.doc.update(idempotency_key=submission_key,upload_intent_sha256=digest(intent))
            self.receipts[submission_key]=self.doc
        if self.lost_upload:raise Ambiguous('Lost upload response')
        return copy.deepcopy(self.doc)
    def receipt(self,row):return self.urls.receipt(copy.deepcopy(row))
    def draft(self,docid,intent):
        assert docid=='document' and intent==self.intent
        self.posts.append(copy.deepcopy(intent))
        if self.reject:raise Rejected(422,self.reject)
        cid='conversion-'+str(len(self.posts));self.polls=0;self.publish_polls=0;self.converted=False
        self.conversion=dict(id=cid,document_id=docid,source_sha256=intent['source_sha256'],intent=copy.deepcopy(intent),publish=True,
                             status='queued',status_url='/api/v1/conversions/'+cid,review_url='/pdf?document=document&conversion='+cid)
        self.doc['conversions'].append(self.conversion)
        self.doc['draft'].update(status='queued',requested_conversion_id=cid)
        if self.lost_draft:raise Ambiguous('Lost draft response')
        return copy.deepcopy(self.conversion)
    def read(self,url):
        if '/documents/' in url:
            self.gets+=1
            self.doc['status']='ready' if self.gets>=3 else 'inspecting'
            self.doc['intent_error']=self.intent_error
            if self.superseded and self.conversion:self.doc['draft']['requested_conversion_id']='operator-conversion'
            if self.converted:
                self.publish_polls+=1
                if self.publish_polls>=3:
                    self.doc['draft'].update(status='ready',jobs=[dict(page_index=0,status='empty',url='/pdf?document=document')] if self.blank else
                                            [dict(page_index=0,job_id='job-one',url='/#job-one'),dict(page_index=1,job_id='job-two',url='/#job-two')])
            return copy.deepcopy(self.doc)
        if '/conversions/' in url:
            self.polls+=1
            if self.failure:self.conversion.update(status=self.failure,error='Deliberate conversion failure')
            elif self.polls<3 or self.hold:self.conversion['status']='running'
            else:self.conversion['status']='ready';self.converted=True
            return copy.deepcopy(self.conversion)
        raise AssertionError(url)


class PrintTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory();cls.intent=make_fixture(Path(cls.fixture.name))
    @classmethod
    def tearDownClass(cls):cls.fixture.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.key=digest('export');self.recipe_hash=digest('recipe')
        self.export=self.root/'exports'/self.key;self.export.mkdir(parents=True)
        shutil.copy(Path(self.fixture.name)/'boundary.pdf',self.export/'map.pdf')
        atomic_json(self.export/'map.print-intent.json',self.intent)
        self.driver=Driver(self.intent);self.tasks=self.manager()
    def manager(self):return PrintTasks(self.root/'state',self.root/'exports',self.driver,poll_interval=.01)
    def tearDown(self):self.tasks.close();self.temp.cleanup()
    def request(self,**kwargs):return self.tasks.request(self.export,self.recipe_hash,**kwargs)
    def until(self,*phases):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=self.tasks.public(self.key)
            if value['phase'] in phases:return value
            time.sleep(.005)
        self.fail(str(value))
    def test_preserves_complete_intent_delayed_publication_repeat_clicks_and_multiple_pages(self):
        self.request()
        for _ in range(5):self.request()
        self.until('preparing');ready=self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(self.driver.posts,[self.intent])
        self.assertEqual([j['url'] for j in ready['jobs']],['http://public.example:9191/#job-one','http://public.example:9191/#job-two'])
        self.assertIn('conversion=conversion-1',ready['review_url']);self.assertEqual(ready['source_sha256'],self.intent['source_sha256'])
        self.assertEqual(self.request()['phase'],'ready');self.assertEqual(self.driver.uploads,1)
        with self.assertRaises(Conflict):self.tasks.request(self.export,digest('new recipe'))

    def test_old_intent_size_rejection_retries_the_pinned_export_after_upgrade(self):
        with patch.object(self.driver, 'submit', side_effect=Rejected(422, 'Intent exceeds 1 MiB')):
            self.request();failed=self.until('failed')
        self.assertTrue(failed['retryable'])
        before=self.tasks.load(self.key)
        self.assertNotIn('document_id',before)
        with self.assertRaises(Conflict):self.tasks.retry(self.key,digest('other map'))
        self.tasks.retry(self.key,self.recipe_hash);self.until('ready')
        after=self.tasks.load(self.key)
        self.assertEqual(after['intent'],before['intent'])
        self.assertEqual(after['submission_key'],before['submission_key'])
        self.assertEqual(self.driver.posts,[before['intent']])
    def test_restart_resumes_conversion_without_upload_or_create(self):
        self.driver.hold=True;self.request();self.until('converting');self.tasks.close()
        self.driver.hold=False;self.tasks=self.manager();self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(len(self.driver.posts),1)
    def test_old_upload_only_receipt_resumes_using_existing_document(self):
        old=self.driver.submit(self.export/'map.pdf',self.intent)
        atomic_json(self.tasks.path(self.key),old)
        self.request();self.until('ready');self.assertEqual(self.driver.uploads,1)
    def test_old_upload_receipt_survives_a_composer_cache_key_change(self):
        old=self.driver.submit(self.export/'map.pdf',self.intent);old_key=digest('previous composer')
        old_export=self.root/'exports'/old_key;old_export.mkdir()
        atomic_json(old_export/'map.print-intent.json',self.intent);atomic_json(self.tasks.path(old_key),old)
        self.request();ready=self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(ready['resumed_from_export'],old_key)
    def test_inspection_and_optional_intent_errors_never_request_fallback_conversion(self):
        self.driver.intent_error='Wrong source hash';self.request();value=self.until('failed')
        self.assertIn('Wrong source hash',value['error']);self.assertFalse(self.driver.posts)
        self.assertIn('document=document',value['review_url'])
    def test_conversion_failure_explicit_retry_reuses_document_and_preserves_previous_jobs(self):
        self.driver.failure='unsupported';self.request();failed=self.until('failed');self.assertTrue(failed['retryable'])
        self.request();self.assertEqual(len(self.driver.posts),1)
        self.driver.doc['draft']['jobs']=[dict(page_index=0,job_id='older-job',url='/#older-job')]
        self.driver.failure=None;self.request(retry=True);ready=self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(len(self.driver.posts),2)
        self.assertEqual(ready['previous_jobs'][0]['job_id'],'older-job');self.assertEqual(len(ready['attempts']),1)
    def test_lost_upload_acknowledgement_recovers_receipt_without_reupload(self):
        self.driver.lost_upload=True;self.request();self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(len(self.driver.upload_attempts),1)
        self.assertGreater(self.driver.lookups,0);self.assertEqual(len(self.driver.posts),1)
    def test_request_lost_before_commit_replays_the_same_key(self):
        self.driver.drop_before_commit=True;self.request();self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(len(self.driver.upload_attempts),2)
        self.assertEqual(*self.driver.upload_attempts);self.assertTrue(self.driver.upload_attempts[0])
    def test_receipt_lookup_waits_for_connection_without_uploading_again(self):
        self.driver.lost_upload=True;self.driver.lookup_offline=True;self.request();self.until('uploading')
        deadline=time.monotonic()+2
        while not self.driver.lookups and time.monotonic()<deadline:time.sleep(.005)
        self.assertGreater(self.driver.lookups,0);self.assertEqual(self.driver.uploads,1)
        self.assertEqual(self.tasks.public(self.key)['phase'],'uploading')
        self.driver.lookup_offline=False;self.until('ready');self.assertEqual(self.driver.uploads,1)
    def test_legacy_lost_upload_acknowledgement_never_blindly_reuploads(self):
        self.driver.idempotency=False
        self.driver.lost_upload=True;self.request();self.until('recovery')
        self.request();self.assertEqual(self.driver.uploads,1)
        with self.assertRaises(Conflict):self.request(retry=True)
    def test_lost_draft_response_reconciles_exact_source_and_selected_intent(self):
        self.driver.lost_draft=True;self.request();self.until('ready')
        self.assertEqual(len(self.driver.posts),1)
    def test_restart_during_ambiguous_upload_offers_pdf_recovery(self):
        with patch.object(self.tasks.pool,'submit'):self.request()
        self.tasks.save(self.key,phase='uploading',pending_post='upload')
        self.tasks.close();self.tasks=self.manager();value=self.until('recovery')
        self.assertEqual(self.driver.uploads,0);self.assertEqual(value['review_url'],'http://public.example:9191/pdf')
    def test_restart_after_key_persisted_recovers_before_and_after_commit(self):
        for committed in [False,True]:
            with self.subTest(committed=committed):
                with patch.object(self.tasks.pool,'submit'):self.request()
                submission_key=str(uuid.uuid4())
                self.tasks.save(self.key,phase='uploading',pending_post='upload',submission_key=submission_key,document_id=None,conversion_id=None)
                if committed:self.driver.submit(self.export/'map.pdf',self.intent,submission_key=submission_key)
                uploads=self.driver.uploads
                self.tasks.close();self.tasks=self.manager();self.until('ready')
                self.assertEqual(self.driver.uploads,uploads+(0 if committed else 1))
    def test_recovered_receipt_must_bind_both_source_and_intent(self):
        for field in ['source_sha256','upload_intent_sha256','idempotency_key']:
            with self.subTest(field=field):
                with patch.object(self.tasks.pool,'submit'):self.request()
                self.tasks.close();submission_key=str(uuid.uuid4())
                self.driver.submit(self.export/'map.pdf',self.intent,submission_key=submission_key)
                self.driver.receipts[submission_key][field]='wrong'
                self.tasks.save(self.key,phase='uploading',pending_post='upload',submission_key=submission_key,document_id=None)
                self.tasks=self.manager();failed=self.until('failed')
                self.assertIn('does not match',failed['error']);self.assertFalse(failed['retryable']);self.assertFalse(self.driver.posts)
    def test_pending_keyed_upload_never_downgrades_to_an_unkeyed_post(self):
        with patch.object(self.tasks.pool,'submit'):self.request()
        self.tasks.save(self.key,phase='uploading',pending_post='upload',submission_key=str(uuid.uuid4()))
        self.tasks.close();self.driver.idempotency=False;self.tasks=self.manager()
        deadline=time.monotonic()+2
        while not self.tasks.public(self.key).get('connection_error') and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(self.tasks.public(self.key)['phase'],'uploading');self.assertEqual(self.driver.upload_attempts,[])
        self.driver.idempotency=True;self.until('ready');self.assertEqual(self.driver.uploads,1)
    def test_upload_key_conflict_cannot_be_retried_as_new_artwork(self):
        with patch.object(self.driver,'submit',side_effect=Rejected(409,'Submission key already used')):
            self.request();failed=self.until('failed')
        self.assertFalse(failed['retryable']);self.assertFalse(self.driver.posts)
        with self.assertRaises(Conflict):self.request(retry=True)
    def test_newer_operator_conversion_is_never_overwritten(self):
        self.driver.superseded=True;self.request();self.until('superseded')
        self.assertEqual(len(self.driver.posts),1)
    def test_retry_cannot_replace_a_newer_operator_choice(self):
        self.driver.failure='error';self.request();self.until('failed')
        self.driver.doc['draft']['requested_conversion_id']='new-operator-choice'
        self.request(retry=True);self.until('superseded');self.assertEqual(len(self.driver.posts),1)
    def test_empty_page_has_no_plot_link(self):
        self.driver.blank=True;self.request();value=self.until('empty')
        self.assertTrue(all('job_id' not in job for job in value['jobs']))
    def test_conversion_progress_is_forwarded_without_inventing_counts(self):
        self.driver.hold=True;self.request();self.until('converting')
        progress=dict(version=1,phase='visibility',label='Resolving hidden lines',completed=37,total=100,unit='paints',page_number=1,page_count=2)
        self.driver.conversion['progress']=progress
        deadline=time.monotonic()+2
        while time.monotonic()<deadline and self.tasks.public(self.key).get('progress')!=progress:time.sleep(.005)
        self.assertEqual(self.tasks.public(self.key)['progress'],progress)
    def test_old_carousel_rejection_retries_uploaded_document_without_rendering_or_edit(self):
        self.intent=copy.deepcopy(self.intent)
        self.intent['palette']['inks'][0]['source']=dict(kind='plotline-carousel',revision=9,slot=2)
        self.driver.intent=self.intent;atomic_json(self.export/'map.print-intent.json',self.intent)
        self.driver.reject='Carousel changed; refresh the palette snapshot before converting'
        self.request();value=self.until('failed')
        self.assertTrue(value['stale_inventory']);self.assertTrue(value['retryable'])
        self.assertEqual(self.tasks.load(self.key)['intent'],self.intent)
        # The previous release persisted retryable=False. Read-time adaptation
        # makes it retryable without rewriting the receipt or its diagnostics.
        self.tasks.close();self.tasks.save(self.key,retryable=False)
        before=self.tasks.path(self.key).read_bytes()
        self.tasks=self.manager();self.assertTrue(self.tasks.public(self.key)['retryable'])
        self.assertEqual(self.tasks.path(self.key).read_bytes(),before)
        with self.assertRaises(Conflict):self.tasks.retry(self.key,digest('different artwork'))
        self.assertEqual(self.tasks.path(self.key).read_bytes(),before)
        shutil.rmtree(self.export) # Uploaded PDF + pinned intent are sufficient.
        self.driver.reject=None;self.tasks.retry(self.key,self.recipe_hash);ready=self.until('ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(self.driver.posts,[self.intent,self.intent])
        self.assertFalse(ready['stale_inventory']);self.assertEqual(len(ready['attempts']),1)
        self.assertEqual(self.tasks.load(self.key)['intent'],self.intent)
    def test_pinned_retry_retains_ambiguous_post_and_newer_operator_guards(self):
        self.driver.reject='Carousel changed; refresh the palette snapshot before converting'
        self.request();self.until('failed')
        self.tasks.save(self.key,pending_post='draft')
        self.assertFalse(self.tasks.public(self.key)['retryable'])
        with self.assertRaises(Conflict):self.tasks.retry(self.key,self.recipe_hash)
        self.tasks.save(self.key,pending_post=None)
        self.driver.reject=None;self.driver.doc['draft']['requested_conversion_id']='operator-choice'
        self.tasks.retry(self.key,self.recipe_hash);self.until('superseded')
        self.assertEqual(len(self.driver.posts),1)
    def test_driver_trace_is_readable_but_original_diagnostic_is_retained(self):
        noisy='\x1b[0;93m[W:onnxruntime] Failed to persist telemetry device ID\x1b[m\nTraceback (most recent call last):\n  File "/driver.py", line 227\nValueError: Carousel changed; refresh the palette snapshot before converting'
        self.driver.reject=noisy;self.request();value=self.until('failed')
        self.assertTrue(value['stale_inventory']);self.assertIn('Retry conversion',value['error'])
        self.assertNotIn('telemetry',value['error']);self.assertNotIn('Traceback',value['error'])
        self.assertNotIn('diagnostic',value);self.assertEqual(self.tasks.load(self.key)['diagnostic'],noisy)
        # Existing receipts must also become readable without rewriting history.
        self.tasks.save(self.key,error=noisy)
        self.assertNotIn('Traceback',self.tasks.public(self.key)['error'])
        self.assertEqual(self.tasks.load(self.key)['error'],noisy)

    def test_only_configured_origins_are_followed_or_rewritten(self):
        client=self.driver.urls
        self.assertEqual(client.resolve('http://public.example:9191/api/v1/documents/one'),'http://127.0.0.1:9191/api/v1/documents/one')
        for url in ['https://external.example/status','//external.example/status','http://127.0.0.1:1234/status','http://user@127.0.0.1:9191/status']:
            with self.assertRaises(ValueError):client.resolve(url)
