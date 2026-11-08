from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from raster_lab.comments import Comments, CommentError
from raster_lab.server import handler


class CommentsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state, self.output = self.root/'state', self.root/'output'
        self.state.mkdir(); self.output.mkdir()
        self.sha = 'a'*64; self.key = 'b'*20
        source = dict(id='test',sha256=self.sha,title='Test',artist='Artist')
        (self.state/'sources.json').write_text(json.dumps(dict(images=[source])))
        self.folder = self.output/'test'/self.key; self.folder.mkdir(parents=True)
        result = dict(key=self.key,signature=dict(source=self.sha,recipe=dict(id='am',name='AM',density=.7)))
        (self.folder/'result.json').write_text(json.dumps(result))
        (self.folder/'preview.png').write_bytes(b'preserved-preview')
        (self.folder/'paths.svg').write_text('<svg/>')
        (self.output/'test'/'source.png').write_bytes(b'preserved-original')
        self.store=Comments(self.state,self.output)
        self.payload=dict(id=str(uuid4()), text='Best in the row.\nWhy the 45° lines?',
            sample=dict(challenge_id='test',experiment_id='am',result_key=self.key,source_sha256=self.sha),
            view=dict(zoom=4,x=.2,y=.8))

    def tearDown(self):
        self.temporary.cleanup()

    def test_notes_and_exact_sample_survive_restart_and_build_removal(self):
        note=self.store.add(self.payload)
        shutil.rmtree(self.output)
        reopened=Comments(self.state,self.output)
        self.assertEqual(reopened.list(),[note])
        self.assertEqual((reopened.samples/self.key/'preview.png').read_bytes(),b'preserved-preview')
        # A second observation can target the archived result too.
        second=reopened.add(dict(self.payload,id=str(uuid4()),text='Another observation'))
        self.assertEqual(second['sample']['recipe']['density'],.7)

    def test_retries_do_not_duplicate_and_cross_sample_ids_conflict(self):
        note=self.store.add(self.payload)
        self.assertEqual(self.store.add(self.payload),note)
        self.assertEqual(self.store.add(dict(self.payload,view=dict(zoom=2,x=.5,y=.5))),note)
        with self.assertRaises(CommentError) as error:
            self.store.add(dict(self.payload,text='Changed text with the same nonce'))
        self.assertEqual(error.exception.status,409)
        self.assertEqual(len(self.store.list()),1)

    def test_simultaneous_adds_and_retry_preserve_all_notes(self):
        payloads=[dict(self.payload,id=str(uuid4()),text=f'Note {i}') for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(self.store.add,payloads+payloads))
        self.assertEqual(len(self.store.list()),12)

    def test_edit_keeps_history_and_rejects_stale_update(self):
        original=self.store.add(self.payload)
        edited=self.store.edit(original['id'],dict(text='Updated opinion',version=1))
        self.assertEqual(edited['history'][0]['text'],original['text'])
        self.assertEqual(edited['view'],original['view'])
        with self.assertRaises(CommentError) as error:
            self.store.edit(original['id'],dict(text='stale',version=1))
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.store.list()[0]['text'],'Updated opinion')

    def test_rejects_mismatched_version_identifiers_text_and_view(self):
        for field,value in [('experiment_id','other'),('challenge_id','../test'),('source_sha256','wrong'),('result_key','x'*20)]:
            with self.subTest(field=field),self.assertRaises(CommentError):
                self.store.add(dict(self.payload,sample=dict(self.payload['sample'],**{field:value})))
        for value in ['', ' '*4, 'x'*4001, 42]:
            with self.assertRaises(CommentError):self.store.add(dict(self.payload,text=value))
        for view in [dict(zoom=float('nan'),x=.5,y=.5),dict(zoom=2,x=-1,y=.5)]:
            with self.assertRaises(CommentError):self.store.add(dict(self.payload,view=view))
        self.assertEqual(self.store.list(),[])

    def test_original_can_have_its_own_comments(self):
        original=dict(self.payload['sample'],experiment_id='source',result_key=self.sha[:20]+'-source')
        note=self.store.add(dict(self.payload,sample=original))
        self.assertEqual(note['sample']['experiment_name'],'Original')
        self.assertEqual((self.store.samples/original['result_key']/'preview.png').read_bytes(),b'preserved-original')

    def test_palette_sources_share_durable_comments_without_replacing_original_manifest(self):
        original = (self.state/'sources.json').read_bytes()
        extra = dict(id='photo',sha256=self.sha,title='Photo',artist='Uploaded PDF')
        (self.state/'palette-sources.json').write_text(json.dumps(dict(images=[extra])))
        shutil.copytree(self.output/'test', self.output/'photo')
        note = self.store.add(dict(self.payload,
            sample=dict(self.payload['sample'], challenge_id='photo')))
        self.assertEqual(note['sample']['title'], 'Photo')
        self.assertEqual((self.state/'sources.json').read_bytes(), original)
        self.assertEqual((self.store.samples/self.key/'preview.png').read_bytes(),b'preserved-preview')

    def test_http_post_reload_edit_cross_origin_and_snapshot(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.output,self.state))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def send(path,payload,method='POST',origin=base):
            req=Request(base+path,data=json.dumps(payload).encode(),method=method,
                        headers={'Content-Type':'application/json','Origin':origin})
            return json.loads(urlopen(req).read())
        try:
            for origin in ['https://example.test','null']:
                with self.assertRaises(HTTPError) as error:send('/api/comments',self.payload,origin=origin)
                self.assertEqual(error.exception.code,403)
            result=send('/api/comments',self.payload)
            self.assertEqual(len(json.loads(urlopen(base+'/api/comments').read())['comments']),1)
            self.assertEqual(urlopen(base+f'/comment-samples/{self.key}/preview.png').read(),b'preserved-preview')
            changed=send('/api/comments/'+result['id'],dict(text='<b>literal text</b>',version=1),'PATCH')
            self.assertEqual(changed['text'],'<b>literal text</b>')
            with self.assertRaises(HTTPError) as error:
                send('/api/comments',dict(self.payload,text='a'*25000))
            self.assertEqual(error.exception.code,413)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
