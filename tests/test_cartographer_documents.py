import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image
from cartographer.model import default_recipe,digest
from cartographer.storage import Recipes,Conflict,atomic_json
from cartographer.service import Application


def thumbnail(color='green'):
    output=io.BytesIO();Image.new('RGB',(500,300),color).save(output,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Recipes(self.temp.name);self.recipe=default_recipe()
    def tearDown(self):self.temp.cleanup()
    def test_save_in_place_and_named_copies_keep_artwork_and_history(self):
        first=self.store.save(self.recipe,name='Mountains',thumbnail=thumbnail())
        changed=copy.deepcopy(self.recipe);changed['camera']['azimuth_deg']=42
        second=self.store.save(changed,first['id'],first['revision'],thumbnail=thumbnail('blue'))
        self.assertEqual((second['id'],second['title'],second['revision']),(first['id'],'Mountains',2))
        self.assertEqual(self.store.get(first['id'],1)['recipe'],self.recipe)
        duplicate=self.store.save(changed,name='Mountains')
        third=self.store.save(changed,name='Mountains (2)')
        self.assertEqual([duplicate['title'],third['title']],['Mountains (2)','Mountains (3)'])
        self.assertEqual(duplicate['recipe'],changed) # A file name never changes map art.
        self.assertNotEqual(first['id'],duplicate['id'])
        with self.assertRaises(Conflict):self.store.save(changed,first['id'],1)
        self.assertEqual(self.store.get(first['id']),second)
    def test_concurrent_copies_get_distinct_names(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(lambda _:self.store.save(self.recipe,name='Map'),range(4)))
        self.assertEqual({row['title'] for row in rows},{'Map','Map (2)','Map (3)','Map (4)'})
    def test_existing_duplicate_names_are_not_renamed_by_save(self):
        first=self.store.save(self.recipe,name='Map');second=self.store.save(self.recipe,name='Map')
        # Pre-document releases allowed different saved recipes with the same name.
        second['title']='Map';atomic_json(Path(self.temp.name)/second['id']/'00000001.json',second)
        saved=self.store.save(self.recipe,first['id'],1)
        self.assertEqual(saved['title'],'Map')
    def test_thumbnails_survive_restart_and_late_results_are_revision_bound(self):
        first=self.store.save(self.recipe,name='Map')
        changed=copy.deepcopy(self.recipe);changed['camera']['azimuth_deg']=37
        second=self.store.save(changed,first['id'],1,thumbnail=thumbnail('blue'))
        self.store.set_thumbnail(first['id'],1,digest(self.recipe),thumbnail())
        with self.assertRaises(Conflict):self.store.set_thumbnail(first['id'],2,digest(self.recipe),thumbnail())
        with self.assertRaises(ValueError):self.store.set_thumbnail(first['id'],None,digest(self.recipe),thumbnail())
        reopened=Recipes(self.temp.name)
        self.assertIn('revision=2',reopened.list()[0]['thumbnail_url'])
        self.assertNotEqual(reopened.thumbnail(first['id'],1),reopened.thumbnail(first['id'],2))
        with Image.open(io.BytesIO(reopened.thumbnail(first['id']))) as image:
            self.assertEqual(image.format,'JPEG');self.assertLessEqual(image.width,384);self.assertLessEqual(image.height,288)
        self.assertEqual(reopened.get(first['id'])['recipe'],changed)
        self.assertEqual(second['recipe_hash'],digest(changed))
    def test_invalid_thumbnails_and_names_do_not_commit_revisions(self):
        for value in ['data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,bm90LWEtcG5n', 'https://example.com/a.jpg']:
            with self.subTest(value=value),self.assertRaises(ValueError):self.store.save(self.recipe,thumbnail=value)
        for name in ['', ' '*3, 'x'*101, 3]:
            with self.subTest(name=name),self.assertRaises(ValueError):self.store.save(self.recipe,name=name)
        self.assertEqual(self.store.list(),[])
        self.assertEqual(list(Path(self.temp.name).rglob('[0-9]*.json')),[])


class ThumbnailJobs:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True)
        Image.new('RGB',(800,500),'green').save(self.directory/'preview.png')
        self.calls=[];self.cancelled=[];self.hold=True
    def submit(self,recipe,quality,session,revision):
        self.calls.append((copy.deepcopy(recipe),quality,session,revision))
        return dict(id='thumbnail-job',status='running')
    def public(self,identity):return dict(id=identity,status='running' if self.hold else 'ready')
    def export(self,identity,session,revision,recipe_hash):
        assert recipe_hash==digest(self.calls[-1][0])
        return self.directory
    def cancel(self,session,revision):self.cancelled.append((session,revision))


class BackgroundThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.apps=[]
        self.store=Recipes(self.root/'state/recipes');self.value=self.store.save(default_recipe(),name='Saved before preview')
    def tearDown(self):
        for app in self.apps:app.close_thumbnails();app.preparations.close();app.print_tasks.close()
        self.temp.cleanup()
    def app(self,jobs):
        with patch('cartographer.service.Jobs',return_value=jobs),patch('cartographer.service.tile_source',side_effect=OSError('offline')) as fetch:
            app=Application(self.root/'state',self.root/'build','http://127.0.0.1:1')
            fetch.assert_not_called() # Startup and saved documents work without the map provider.
        self.apps.append(app);return app
    def until(self,predicate):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.02)
        self.fail('Thumbnail task did not reach the expected state')
    def test_saved_thumbnail_resumes_after_service_restart_without_a_browser(self):
        jobs=ThumbnailJobs(self.root/'images');app=self.app(jobs)
        self.until(lambda:jobs.calls);app.close_thumbnails()
        self.assertEqual(len(jobs.cancelled),1)
        self.assertIsNone(self.store.get(self.value['id'])['thumbnail_url'])
        jobs.hold=False;self.app(jobs)
        self.until(lambda:self.store.get(self.value['id'])['thumbnail_url'])
        self.assertTrue(all(call[1]=='draft' and call[2].startswith('thumbnail-') for call in jobs.calls))
        self.assertEqual(self.store.get(self.value['id'])['recipe'],self.value['recipe'])
    def test_arriving_browser_thumbnail_cancels_only_the_background_preview(self):
        jobs=ThumbnailJobs(self.root/'images');self.app(jobs);self.until(lambda:jobs.calls)
        self.store.set_thumbnail(self.value['id'],1,self.value['recipe_hash'],thumbnail('red'))
        expected=self.store.thumbnail(self.value['id']);self.until(lambda:jobs.cancelled)
        self.assertEqual(jobs.cancelled,[(jobs.calls[0][2],2)])
        self.assertEqual(self.store.thumbnail(self.value['id']),expected)


if __name__=='__main__':unittest.main()
