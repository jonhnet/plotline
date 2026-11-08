import copy
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from cartographer.model import default_recipe, digest
from cartographer.preparing import Preparations
from cartographer.printing import PrintTasks
from cartographer.storage import atomic_json
from make_boundary_fixture import make_fixture
from test_cartographer_printing import Driver


class Composer:
    """Controlled full-detail worker; the PDF/intent are real boundary fixtures."""
    def __init__(self,exports,intent,pdf):
        self.exports=exports;self.intent=intent;self.pdf=pdf;self.calls=[];self.rows={}
        self.hold=False;self.fail=False;self.unmeasured=[];self.version='initial'
    def submit(self,recipe,quality,session,revision):
        self.calls.append(copy.deepcopy(recipe));assert quality=='proof'
        key=digest([self.version,recipe,quality]);identity=str(len(self.calls))
        self.rows[identity]=dict(id=identity,cache_key=key,recipe_hash=digest(recipe),session=session,revision=revision)
        export=self.exports/key;export.mkdir(parents=True,exist_ok=True)
        shutil.copy(self.pdf,export/'map.pdf');atomic_json(export/'map.print-intent.json',self.intent)
        return self.public(identity)
    def public(self,identity):
        if identity not in self.rows:raise FileNotFoundError('Worker not found')
        return dict(self.rows[identity],status='error' if self.fail else 'running' if self.hold else 'ready',
                    result=dict(unmeasured_inks=self.unmeasured),
                    error='Test rendering failure' if self.fail else None,
                    progress=dict(stage='drawing',overall_percent=60,detail='Drawing forest hatching'))
    def export(self,identity,session,revision,recipe_hash):
        row=self.rows[identity];assert row['session']==session and row['revision']==revision and row['recipe_hash']==recipe_hash
        return self.exports/row['cache_key']


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory();cls.intent=make_fixture(Path(cls.fixture.name))
    @classmethod
    def tearDownClass(cls):cls.fixture.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.recipe=default_recipe();self.driver=Driver(self.intent)
        self.printing=PrintTasks(self.root/'state',self.root/'exports',self.driver,poll_interval=.005)
        self.composer=Composer(self.root/'exports',self.intent,Path(self.fixture.name)/'boundary.pdf')
        self.preparing=self.manager()
    def manager(self):return Preparations(self.root/'state',self.composer,self.printing,poll_interval=.005)
    def tearDown(self):self.preparing.close();self.printing.close();self.temp.cleanup()
    def until(self,key,*phases):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=self.preparing.public(key)
            if value['phase'] in phases:return value
            time.sleep(.005)
        self.fail(str(value))
    def test_one_action_uses_full_detail_and_preserves_pdf_intent(self):
        self.composer.hold=True
        task=self.preparing.request(self.recipe);key=task['id']
        for _ in range(4):self.assertEqual(self.preparing.request(self.recipe)['id'],key)
        deadline=time.monotonic()+2
        while not self.composer.calls and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(len(self.composer.calls),1)
        self.assertEqual(self.preparing.public(key)['render']['progress']['detail'],'Drawing forest hatching')
        self.recipe['title']='Later edits';self.composer.hold=False
        done=self.until(key,'ready')
        self.assertEqual(self.composer.calls[0]['title'],default_recipe()['title'])
        self.assertEqual(self.driver.uploads,1);self.assertEqual(self.driver.posts,[self.intent])
        self.assertEqual(len(done['jobs']),2);self.assertNotIn('recipe',done)
        # Reopening is navigation to the existing drawing, even after a restart.
        self.preparing.close();self.preparing=self.manager()
        again=self.preparing.request(self.composer.calls[0])
        self.assertEqual(again['jobs'],done['jobs']);self.assertEqual(len(self.composer.calls),1)
    def test_prepared_source_document_is_pinned_on_reuse(self):
        self.composer.intent=copy.deepcopy(self.intent)
        self.composer.intent['authoring']=dict(application='map-studio',url='http://plotline.test/map',recipe_hash=digest(self.recipe))
        self.driver.intent=copy.deepcopy(self.composer.intent)
        self.driver.intent['authoring'].update(document_id='named-map',document_revision=3)
        key=self.preparing.request(self.recipe,source_document={'id':'named-map','revision':3})['id']
        self.until(key,'ready')
        origin=self.driver.posts[0]['authoring']
        self.assertEqual(origin['document_id'],'named-map')
        self.assertEqual(origin['document_revision'],3)
        self.assertEqual(origin['recipe_hash'],digest(self.recipe))
        self.preparing.request(self.recipe,source_document={'id':'another-copy','revision':1})
        self.assertEqual(self.preparing.load(key)['source_document'],{'id':'named-map','revision':3})
        self.assertEqual(len(self.driver.posts),1)

    def test_restart_resumes_authorized_recipe_and_never_uploads_newer_edits(self):
        self.composer.hold=True;key=self.preparing.request(self.recipe)['id']
        deadline=time.monotonic()+2
        while not self.composer.calls and time.monotonic()<deadline:time.sleep(.005)
        self.preparing.close();self.composer.rows.clear();self.composer.hold=False
        self.preparing=self.manager();self.until(key,'ready')
        self.assertEqual(self.driver.uploads,1);self.assertEqual(self.composer.calls,[self.recipe,self.recipe])
    def test_explicit_rebuild_updates_same_pointer_and_preserves_previous_export(self):
        key=self.preparing.request(self.recipe)['id'];old=self.until(key,'ready')
        self.composer.version='fixed-renderer';self.composer.hold=True
        requested=self.preparing.request(self.recipe,rebuild=True)
        self.assertEqual(requested['id'],key);self.assertIsNone(requested['export_key'])
        deadline=time.monotonic()+2
        while len(self.composer.calls)<2 and time.monotonic()<deadline:time.sleep(.005)
        for _ in range(3):self.preparing.request(self.recipe,rebuild=True)
        self.assertEqual(len(self.composer.calls),2,'Repeat clicks must not replace active work')
        self.composer.hold=False;new=self.until(key,'ready')
        self.assertNotEqual(old['export_key'],new['export_key'])
        self.assertTrue((self.root/'exports'/old['export_key']/'map.pdf').exists())
        self.assertEqual(self.printing.public(old['export_key'])['phase'],'ready')
        self.assertEqual(self.driver.uploads,2)
        self.preparing.close();self.preparing=self.manager()
        self.assertEqual(self.preparing.request(self.recipe)['export_key'],new['export_key'])
    def test_rebuild_bypasses_old_two_step_receipt(self):
        old=self.until(self.preparing.request(self.recipe)['id'],'ready')
        self.preparing.close();self.preparing.path(old['id']).unlink()
        self.preparing=self.manager();self.composer.version='fixed-renderer'
        new=self.until(self.preparing.request(self.recipe,rebuild=True)['id'],'ready')
        self.assertNotEqual(old['export_key'],new['export_key']);self.assertEqual(self.driver.uploads,2)
    def test_existing_two_step_workflow_is_reused_without_a_map_render(self):
        export=self.root/'exports'/digest('previous composer');export.mkdir(parents=True)
        shutil.copy(Path(self.fixture.name)/'boundary.pdf',export/'map.pdf');atomic_json(export/'map.print-intent.json',self.intent)
        self.driver.reject='Carousel changed; refresh the palette snapshot before converting'
        self.printing.request(export,digest(self.recipe))
        deadline=time.monotonic()+2
        while self.printing.public(export.name)['phase']!='failed' and time.monotonic()<deadline:time.sleep(.005)
        self.driver.reject=None
        key=self.preparing.request(self.recipe)['id'];done=self.until(key,'ready')
        self.assertEqual(done['export_key'],export.name);self.assertEqual(self.composer.calls,[])
        self.assertEqual(self.driver.uploads,1);self.assertEqual(self.driver.posts,[self.intent,self.intent])
    def test_render_failure_retries_only_after_explicit_action(self):
        self.composer.fail=True;key=self.preparing.request(self.recipe)['id'];failed=self.until(key,'failed')
        self.assertTrue(failed['retryable']);self.assertEqual(self.driver.uploads,0)
        self.preparing.request(self.recipe);self.assertEqual(len(self.composer.calls),1)
        self.composer.fail=False;self.preparing.request(self.recipe,retry=True);self.until(key,'ready')
        self.assertEqual(self.driver.uploads,1)
    def test_conversion_retry_keeps_render_and_upload_and_progress(self):
        self.driver.failure='error';key=self.preparing.request(self.recipe)['id'];self.until(key,'failed')
        self.driver.failure=None;self.preparing.request(self.recipe,retry=True);self.until(key,'ready')
        self.assertEqual(len(self.composer.calls),1);self.assertEqual(self.driver.uploads,1)
        self.assertEqual(self.driver.posts,[self.intent,self.intent])
    def test_only_emitted_unknown_inks_block_upload(self):
        bad=copy.deepcopy(self.recipe);bad['palette']['inks'][0]['width_mm']=None
        self.composer.unmeasured=[dict(id='black',label='Black',roles=[dict(region='outside',role='roads')])]
        key=self.preparing.request(bad)['id'];failed=self.until(key,'failed')
        self.assertIn('Black (outside roads)',failed['error']);self.assertEqual(self.driver.uploads,0)
        self.composer.unmeasured=[]
        self.preparing.request(bad,retry=True);self.until(key,'ready')
        self.assertEqual(self.driver.uploads,1,'Unused unknown authoring inks do not block an otherwise valid print intent')

    def test_ambiguous_upload_does_not_start_extra_work(self):
        self.driver.lost_upload=True;key=self.preparing.request(self.recipe)['id'];self.until(key,'ready')
        self.preparing.request(self.recipe,retry=True);self.assertEqual(self.driver.uploads,1)
        self.assertEqual(len(self.composer.calls),1);self.assertEqual(len(self.driver.posts),1)


if __name__=='__main__':unittest.main()
