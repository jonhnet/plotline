import json
import os
from pathlib import Path
import tempfile
import unittest

from cartographer.retention import DEFAULTS, preserve_sources, sweep
from cartographer.storage import atomic_json


class RetentionTests(unittest.TestCase):
    def test_cache_eviction_preserves_authoring_inputs_and_incomplete_handoffs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'state';build=root/'build'
            for key in ('a'*64,'b'*64):
                export=build/'exports'/key
                for name,value in [('recipe.json',{'title':key}),('result.json',{'recipe_hash':key}),('map.print-intent.json',{'inks':[]})]:
                    atomic_json(export/name,value)
                (export/'map.pdf').write_bytes(b'generated PDF')
            atomic_json(state/'submissions'/('b'*64+'.json'),{'phase':'recovery'})
            task=build/'tasks'/('c'*32)
            atomic_json(task/'request.json',{'title':'unsaved experiment'})
            (task/'composer.log').write_bytes(b'failed details')
            geo=build/'geometry/decoded';geo.mkdir(parents=True)
            (geo/('d'*64+'.pickle')).write_bytes(b'decoded geometry')
            partial=geo/('e'*64+'.'+'f'*32+'.tmp');partial.write_bytes(b'interrupted cache write')
            raw=build/'geometry/http';raw.mkdir()
            (raw/'download').write_bytes(b'original source tile')
            preserve_sources(state,build)
            result=sweep(state,build,dict(DEFAULTS,export_max_bytes=0,geometry_max_bytes=0,idle_grace_seconds=0))
            self.assertEqual(result['expired_exports'],1)
            self.assertFalse((build/'exports'/('a'*64)).exists())
            self.assertTrue((build/'exports'/('b'*64)/'map.pdf').exists())
            self.assertTrue((state/'export-recipes'/('a'*64)/'recipe.json').exists())
            self.assertTrue((state/'preview-recipes'/('c'*32+'.json')).exists())
            self.assertEqual((state/'map-sources-http/download').read_bytes(),b'original source tile')
            self.assertEqual((raw/'download').read_bytes(),b'original source tile')
            preserve_sources(state,build) # Restart is idempotent.
            self.assertEqual(result['expired_geometry'],1)
            self.assertFalse(partial.exists())
            self.assertEqual(result['expired_tasks'],1)
            export=build/'exports'/('a'*64)
            atomic_json(export/'recipe.json',{'title':'a'*64})
            atomic_json(export/'result.json',{'recipe_hash':'a'*64,'seconds':42})
            (export/'map.pdf').write_bytes(b'rebuilt PDF')
            second=sweep(state,build,dict(DEFAULTS,export_max_bytes=0,idle_grace_seconds=0))
            self.assertEqual(second['expired_exports'],1)
            self.assertTrue(list((state/'export-recipes'/('a'*64)/'rebuilds').glob('*-result.json')))

    def test_recent_work_and_symlinks_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);state=root/'state';build=root/'build'
            export=build/'exports'/('a'*64)
            atomic_json(export/'recipe.json',{'title':'test'})
            atomic_json(export/'result.json',{'recipe_hash':'test'})
            (export/'map.pdf').write_bytes(b'pdf')
            result=sweep(state,build,dict(DEFAULTS,export_max_bytes=0))
            self.assertEqual(result['expired_exports'],0)
            outside=root/'outside';outside.write_text('do not delete')
            (export/'link').symlink_to(outside)
            with self.assertRaises(ValueError):
                sweep(state,build,dict(DEFAULTS,export_max_bytes=0,idle_grace_seconds=0))
            self.assertEqual(outside.read_text(),'do not delete')


if __name__=='__main__':unittest.main()
