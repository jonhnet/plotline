#!/usr/bin/env python3
"""Real direct Print API acceptance with an isolated Plotline Studios, no device setup.

Run with the cartographer environment and --driver-root pointing at e873d02 or
later, with its documented native environment and release binaries built.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from cartographer.model import canonical,digest,VECTOR_SOURCE,default_recipe
from cartographer.service import Application,Handler
from cartographer.client import Rejected,Ambiguous
from cartographer.storage import atomic_json
from make_boundary_fixture import make_fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--driver-root',type=Path,required=True)
    args=parser.parse_args();driver_root=args.driver_root.resolve()
    directory=Path(tempfile.mkdtemp(prefix='cartographer-print-',dir=ROOT/'build'))
    with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=dict(os.environ,PLOTLINE_PDF_PROJECT=str(driver_root),PLOTLINE_IPP_INBOX=str(directory/'empty-inbox'))
    log=(directory/'driver.log').open('w')
    process=subprocess.Popen([str(driver_root/'controller/target/release/plotline-studios'),'--state',str(directory/'driver-state'),
                              '--listen',f'127.0.0.1:{port}'],env=env,cwd=driver_root,stdout=log,stderr=log)
    app=server=None
    def api(url,body=None):
        request=Request(url,None if body is None else canonical(body),{} if body is None else {'Content-Type':'application/json'},method='GET' if body is None else 'POST')
        with urlopen(request,timeout=10) as response:return json.load(response)
    try:
        for _ in range(100):
            try:caps=api(base+'/api/v1/capabilities');break
            except OSError:time.sleep(.1)
        else:raise AssertionError((directory/'driver.log').read_text())
        fixture=directory/'fixture';fixture.mkdir();intent=make_fixture(fixture)
        # Historical sources deliberately disagree with this isolated driver's
        # inventory. The document's complete inks must still convert unchanged.
        for slot,ink in enumerate(intent['palette']['inks'],1):
            ink['source']=dict(kind='plotline-carousel',revision=caps['inventory_revision']+100,slot=slot)
        key=digest(['print-fixture',intent]);export=directory/'map-build/exports'/key;export.mkdir(parents=True)
        shutil.copy(fixture/'boundary.pdf',export/'map.pdf');atomic_json(export/'map.print-intent.json',intent)
        with patch('cartographer.service.tile_source',return_value=VECTOR_SOURCE):
            app=Application(directory/'map-state',directory/'map-build',base,base)
        recipe=default_recipe();recipe_hash=digest(recipe);jobid='fixture-proof'
        app.jobs.jobs[jobid]=dict(id=jobid,session='fixture-tab',revision=1,recipe_hash=recipe_hash,cache_key=key,
                                  status='ready',quality='proof',created=time.time(),finished=time.time())
        app.jobs.sessions['fixture-tab']=dict(revision=1,job=jobid)
        class QuietHandler(Handler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler);server.app=app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        studio=f'http://127.0.0.1:{server.server_port}'
        body=dict(session='fixture-tab',revision=1,recipe_hash=recipe_hash)
        phases=[]
        def terminal():
            for _ in range(600):
                receipt=api(studio+'/api/print-tasks/'+key);phases.append(receipt['phase'])
                if receipt['phase'] in ['ready','empty','failed','recovery','superseded']:return receipt
                time.sleep(.2)
            raise AssertionError('Print task timed out')
        # Simulate the old driver's known rejection once, then retry through
        # the actual HTTP endpoint after the corrected driver is available.
        post=app.print_client.post;lost_replies=[]
        def lose_upload_reply(path,*args,**kwargs):
            result=post(path,*args,**kwargs)
            if path=='/api/v1/documents' and not lost_replies:
                lost_replies.append(result['id']);raise Ambiguous('Lost acknowledgement after the real driver committed')
            return result
        with patch.object(app.print_client,'post',side_effect=lose_upload_reply), patch.object(app.print_client,'draft',side_effect=Rejected(422,'Carousel changed; refresh the palette snapshot before converting')):
            started=time.monotonic();receipt=api(studio+'/api/jobs/'+jobid+'/submit',body)
            assert time.monotonic()-started<2,'Browser request waited for conversion'
            for _ in range(3):assert api(studio+'/api/jobs/'+jobid+'/submit',body)['export_key']==key
            receipt=terminal();assert receipt['phase']=='failed' and receipt['retryable'],receipt
        app.print_tasks.save(key,retryable=False) # Old persisted receipt.
        # A new map revision must not submit the older job again. Its already
        # authorized background task can still finish as the pinned artwork.
        app.jobs.cancel('fixture-tab',2)
        try:api(studio+'/api/jobs/'+jobid+'/submit',body);raise AssertionError('Stale submit was accepted')
        except HTTPError as error:assert error.code==409
        retry_url=studio+'/api/print-tasks/'+key+'/retry'
        try:api(retry_url,dict(recipe_hash=digest('different recipe')));raise AssertionError('Mismatched retry was accepted')
        except HTTPError as error:assert error.code==409
        api(retry_url,dict(recipe_hash=recipe_hash))
        receipt=terminal()
        assert receipt['phase']=='ready',receipt
        assert len(receipt['jobs'])==2,receipt
        documents=api(base+'/api/v1/documents')['documents'];assert len(documents)==1
        assert lost_replies==[receipt['document_id']]
        submission_key=app.print_tasks.load(key)['submission_key']
        assert api(base+'/api/v1/submissions/'+submission_key)['id']==receipt['document_id']
        document=api(base+'/api/v1/documents/'+receipt['document_id'])
        assert len(document['conversions'])==1
        with urlopen(base+'/api/v1/documents/'+receipt['document_id']+'/source.pdf') as source:
            assert source.read()==(export/'map.pdf').read_bytes()
        conversion=api(base+'/api/v1/conversions/'+receipt['conversion_id'])
        assert conversion['intent']==intent,'Authored paper, placement, inks, bindings or policies changed'
        jobs=api(base+'/api/jobs')
        for page,job in zip(intent['pages'],sorted(jobs,key=lambda j:j['pdf']['page_index'])):
            assert job['pdf']['registration']['sheet_size_mm']==page['target_media']['sheet_size_mm']
            assert job['pdf']['conversion_id']==receipt['conversion_id']
        for job in receipt['jobs']:
            assert job['url']==base+'/#'+job['job_id']
            with urlopen(job['url'].split('#')[0]) as response:assert response.status==200
        # The new one-action entry point recovers this exact design's already
        # completed handoff; it must not render or upload again after reload.
        prepared=api(studio+'/api/prepare',dict(recipe=recipe))
        assert prepared['phase']=='ready' and prepared['jobs']==receipt['jobs'],prepared
        assert len(app.jobs.jobs)==1,'An existing plot caused another map render'
        # A different design goes through the complete preparation chain. Seed
        # a valid composer cache to keep this HTTP test offline and deterministic.
        next_recipe={**recipe,'title':'Second design'};next_key=digest([app.version,next_recipe,'proof'])
        next_export=directory/'map-build/exports'/next_key;next_export.mkdir(parents=True)
        shutil.copy(export/'map.pdf',next_export/'map.pdf')
        atomic_json(next_export/'map.print-intent.json',{**intent,'title':next_recipe['title']})
        atomic_json(next_export/'result.json',dict(recipe_hash=digest(next_recipe),intent_available=True))
        prepared=api(studio+'/api/prepare',dict(recipe=next_recipe));preparation_id=prepared['id']
        for _ in range(3):assert api(studio+'/api/prepare',dict(recipe=next_recipe))['id']==preparation_id
        for _ in range(600):
            prepared=api(studio+'/api/preparations/'+preparation_id)
            if prepared['phase'] in ['ready','failed','recovery','superseded']:break
            time.sleep(.2)
        assert prepared['phase']=='ready' and len(prepared['jobs'])==2,prepared
        assert len(api(base+'/api/v1/documents')['documents'])==2,'Duplicate upload'
        assert api(base+'/api/v1/conversions/'+prepared['conversion_id'])['intent']=={**intent,'title':next_recipe['title']}
        result=dict(passed=True,directory=str(directory),receipt=receipt,phases=list(dict.fromkeys(phases)),
                    checks=['exact source bytes','complete print intent including historical carousel provenance','multi-page HP7585 draft','short submit','repeat clicks','lost upload reply recovers by submission key','stale revision guard','old rejection retries uploaded PDF without another render','retry hash guard','no device configured'])
        atomic_json(directory/'results.json',result);print(json.dumps(dict(passed=True,directory=str(directory),phases=result['phases'])))
    finally:
        if server:server.shutdown();server.server_close()
        if app:app.close_thumbnails();app.preparations.close();app.print_tasks.close();app.jobs.close()
        process.terminate();process.wait(timeout=10);log.close()


if __name__=='__main__':main()
