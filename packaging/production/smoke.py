#!/usr/bin/env python3
"""Destructive acceptance test for a disposable guest; never run on production."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from common import atomic, config, idle, readiness, run, url, UNITS


def wait(fn, seconds=60):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=fn()
        if value: return value
        time.sleep(.1)
    raise AssertionError('Timed out waiting for isolated test work')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args(); c=config(args.config)
    assert Path('/etc/plotline/ISOLATED_TEST').read_text().strip()=='disposable-no-hardware'
    assert not c['device_config'] and not c['ipp_discovery'], 'No hardware or LAN advertisements in tests'
    assert not (args.source/'.git').exists()
    root=(Path(c['install_root'])/'current').resolve();data=Path(c['data_root'])
    revision=(args.source/'SOURCE_REVISION').read_text().strip()
    base=url(c,'web'); results=[]

    def request(path, method='GET', body=None, status=200):
        headers={'X-Plotline-Request':'1'}
        if isinstance(body,(dict,list)):
            body=json.dumps(body).encode();headers['Content-Type']='application/json'
        if isinstance(body,str):body=body.encode()
        try:
            with urlopen(Request(base+path,body,headers,method=method),timeout=30) as r:
                assert r.status==status,(r.status,path)
                payload=r.read()
                return json.loads(payload) if r.headers.get_content_type()=='application/json' else payload
        except HTTPError as error:
            assert error.code==status,(path,error.code,error.read())
            return None

    installer=['python3',str(args.source/'packaging/production/install.py')]
    def command(action, expected=0):
        argv=installer+[action,'--config',str(args.config)]
        if action in ('install','check'):argv+=['--revision',revision]
        if action=='install':argv+=['--source',str(args.source)]
        result=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        assert result.returncode==expected,(action,result.returncode,result.stdout)
        return result.stdout

    def pids():
        return [run('systemctl','show',u,'-p','MainPID','--value',stdout=subprocess.PIPE,text=True).stdout for u in UNITS]

    readiness(c,revision);assert not request('/api/status')['hardware_enabled']
    assert request('/api/v1/documents')['documents']==[], 'Use fresh isolated state'
    for path in ('/','/map','/pdf','/hpgl','/patterns','/mazes'):
        assert b'<html' in request(path).lower(),path
    before=pids(); command('install');assert pids()==before,'Idempotent install restarted services'
    results.append('exported source install/check/reconcile, unchanged service PIDs, all five Studio pages')

    # Install a test-only override with a helper that has no network/serial code.
    fixture=data/'cache/production-smoke';fixture.mkdir(exist_ok=True)
    atomic(fixture/'controls.json',dict(delay_ms=10,completion_hold=True))
    atomic(fixture/'connection.json',dict(fixture_dir=str(fixture),plotting_enabled=True))
    for path in [fixture,*fixture.iterdir()]:shutil.chown(path,user='plotline',group='plotline')
    override=Path('/etc/systemd/system/plotline-web.service.d/isolated-test.conf')
    override.parent.mkdir(exist_ok=True)
    override.write_text(f'[Service]\nExecStart=\nExecStart={root}/controller/target/release/plotline-studios --listen {c["web_bind"]} --state {data}/studios --device-config {fixture}/connection.json --device-helper {root}/controller/scripts/fake-plotter.py\n')
    idle(c);run('systemctl','daemon-reload');run('systemctl','restart','plotline-web')
    readiness(c,revision)
    def device():
        path=fixture/'fixture.json'
        return json.loads(path.read_text()) if path.exists() else dict(opens=0,starts=0,writes=[])
    assert device()['opens']==0
    command('check');assert device()['opens']==0,'Readiness opened serial'

    try:
        inventory=request('/api/carousel')
        inventory['slots']['1'].update(label='Acceptance black',state='loaded',color='#000000',width_mm=.3)
        inventory=request('/api/carousel','PUT',inventory)
        pdf=fixture/'input.pdf'
        run(root/'.venv/bin/python','-c',
            'import pymupdf,sys; d=pymupdf.open(); p=d.new_page(width=612,height=792); p.draw_line((150,200),(350,200),color=(0,0,0),width=.85); d.save(sys.argv[1])',pdf)
        ipp_test=fixture/'print.test'
        ipp_test.write_text('''{
OPERATION Print-Job
GROUP operation
ATTR charset attributes-charset utf-8
ATTR language attributes-natural-language en
ATTR uri printer-uri $uri
ATTR mimeMediaType document-format application/pdf
ATTR name job-name "Production acceptance.pdf"
FILE $filename
STATUS successful-ok
}
''')
        ipp_command=['ipptool','-T','3','-tv','-f',str(pdf),f'ipp://127.0.0.1:{c["ipp_port"]}/ipp/print',str(ipp_test)]
        # Exercise the real C++, Python and Rust readers of the same flock.
        with (data/'admission.lock').open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            request('/api/carousel','PUT',inventory,status=503)
            request('/map/api/recipes',status=503)
            assert request('/api/maintenance')['admission_protocol']==1
            assert request('/map/api/maintenance')['admission_protocol']==1
            rejected=subprocess.run(ipp_command,capture_output=True,text=True,timeout=8)
            assert rejected.returncode!=0 and '503' in rejected.stdout+rejected.stderr,rejected
        results.append('exclusive gate rejects HTTP/Map/IPP writes; bounded maintenance reads still work')
        run(*ipp_command,stdout=subprocess.DEVNULL)
        def inspected():
            docs=request('/api/v1/documents')['documents']
            assert len(docs)<=1
            if docs and docs[0]['status']=='error':raise AssertionError(docs[0])
            return docs and docs[0]['status']=='ready' and request(docs[0]['status_url'])
        document=wait(inspected)
        assert document['conversions']==[] and request('/api/jobs')==[]
        assert device()['opens']==0,'Receiving IPP opened serial'
        request(document['status_url']+'/draft','POST',{},status=202)
        def converted():
            row=request(document['status_url']);draft=row.get('draft') or {}
            if draft.get('status')=='error':raise AssertionError(draft)
            return draft.get('status')=='ready' and row
        wait(converted);assert len(request('/api/jobs'))==1
        assert device()['opens']==0,'Conversion opened serial'
        results.append('IPP capture → PDF review → explicit pen conversion → HPGL; zero serial opens')

        # Explicitly plot only against the fixture. Hold completion to test the
        # interval after feeding ends but before the physical device has finished.
        source='IN;PA;CO"pen id=1 r=0 g=0 b=0 width_mm=0.3";SP1;PU0,0;PD400,400;PU;SP0;'
        job=request('/api/jobs?name=Fake-only.hpgl&units_per_mm=40','POST',source,status=201)
        setup=request(f'/api/jobs/{job["id"]}/setup')
        ticket=request(f'/api/jobs/{job["id"]}/plot','POST',dict(attempt=str(uuid.uuid4()),setup=setup),status=202)
        def execution():return request('/api/plots/'+ticket['id'])['submission']['execution']['state']
        wait(lambda:execution()=='finishing')
        before=pids();output=command('install',75)
        assert 'plot '+ticket['id'] in output and pids()==before,output
        assert device()['starts']==1
        results.append('installer refuses with exit 75 during fake physical completion, without changing service PIDs')
        # Simulate a service crash, then prove restart/health never replay bytes.
        run('systemctl','kill','--kill-whom=all','--signal=KILL','plotline-web')
        time.sleep(4);readiness(c,revision)
        wait(lambda:execution()=='interrupted')
        writes=device()['writes'];command('check');time.sleep(.3)
        assert device()['writes']==writes and device()['starts']==1
        command('install',75)
        request('/api/plotter/acknowledge_reset','POST',dict(attempt=ticket['id']))
        wait(lambda:execution()=='reset_acknowledged')
        before=pids();command('install');assert pids()==before
        assert request('/api/carousel')==inventory
        results.append('interrupted fake plot is retained without replay; operator reset acknowledgment permits reconciliation')
        # Stop/start uses the same retained mount, retaining capture, jobs and ink.
        command('stop');run('systemctl','start','plotline.target');readiness(c,revision)
        assert request('/api/carousel')==inventory
        assert len(request('/api/v1/documents')['documents'])==1
        assert device()['starts']==1
        results.append('safe stop/start retains documents, inventory and plot receipts without plotting')
    finally:
        # This guest is disposable. Never leave the test transport enabled.
        run('systemctl','stop','plotline.target')
        override.unlink(missing_ok=True);run('systemctl','daemon-reload')
        run('systemctl','start','plotline.target')
    readiness(c,revision)
    assert not request('/api/status')['hardware_enabled']
    evidence=dict(revision=revision,passed=True,checks=results)
    atomic(data/'production-test-results.json',evidence)
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
