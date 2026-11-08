#!/usr/bin/env python3
"""Four Map Studio orientations through isolated PDF/HPGL preparation, never Plot."""
import argparse
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from cartographer.client import PrintClient
from cartographer.compose import compose
from cartographer.model import default_recipe,canonical
from cartographer.paper import plotter_profile
from test_cartographer import fixture_data


def paths(text):
    pen=0;point=None;run=[];result=[]
    for command in text.split(';'):
        command=command.strip();op=command[:2];arg=command[2:]
        if op in ('SP','PU') and run:
            result.append((pen,run));run=[]
        if op=='SP':pen=int(arg)
        if op not in ('PU','PD') or not arg:continue
        if op=='PD' and not run:run=[point]
        values=list(map(int,arg.split(',')))
        for xy in zip(values[::2],values[1::2]):
            point=list(xy)
            if op=='PD':run.append(point)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',required=True,type=Path)
    parser.add_argument('--driver-bin',type=Path)
    args=parser.parse_args();driver=args.driver_root.resolve()
    binary=args.driver_bin or driver/'controller/target/release/plotline-studios'
    (ROOT/'build').mkdir(exist_ok=True)
    out=Path(tempfile.mkdtemp(prefix='four-orientations-',dir=ROOT/'build'))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    log=(out/'driver.log').open('w')
    process=subprocess.Popen([str(binary),'--state',str(out/'driver-state'),'--listen',f'127.0.0.1:{port}'],cwd=driver,stdout=log,stderr=log,
        env=dict(os.environ,PLOTLINE_PDF_PROJECT=str(driver),PLOTLINE_IPP_INBOX=str(out/'empty-inbox')))
    client=PrintClient(base,out/'client-state')
    def request(path,value,method='POST',raw=False):
        req=Request(base+path,data=canonical(value),headers={'X-Plotline-Request':'1','Content-Type':'application/json'},method=method)
        with urlopen(req,timeout=30) as response:return response.read() if raw else json.load(response)
    def wait(path):
        for _ in range(300):
            value=client.read(path)
            if value['status']=='ready':return value
            if value['status'] in ('failed','error'):raise AssertionError(value)
            time.sleep(.2)
        raise AssertionError('Conversion timed out')
    results=[]
    try:
        for _ in range(100):
            try:client.check_handoff();break
            except OSError:time.sleep(.1)
        assert not client.read('/api/status')['hardware_enabled']
        for angle in [0,90,180,270]:
            size=[279.4,431.8] if angle%180 else [431.8,279.4]
            recipe=default_recipe();recipe['media']=plotter_profile(size,angle)
            recipe['features'].update(labels='none',park_treatment='outline',buildings='none')
            output=out/str(angle)
            with patch('cartographer.compose.dataset',return_value=fixture_data(recipe)):
                compose(recipe,output,version='orientation-handoff')
            intent=json.loads((output/'map.print-intent.json').read_text())
            receipt=client.submit(output/'map.pdf',intent);wait(receipt['status_url'])
            conversion=client.draft(receipt['id'],intent);wait(conversion['status_url'])
            for _ in range(100):
                document=client.read(receipt['status_url'])
                if document['draft']['status']=='ready':break
                time.sleep(.1)
            jid=document['draft']['jobs'][0]['job_id'];job=client.read('/api/jobs/'+jid);setup=client.read('/api/jobs/'+jid+'/setup')
            registration=job['pdf']['registration'];assert registration['rotation']==angle
            assert [setup['paper_x_mm'],setup['paper_y_mm']]==[431.8,279.4]
            # Compare every registered point to an independent quarter-turn and
            # translation of the original converter coordinates. No ink centering.
            original=paths((out/'driver-state/conversions'/conversion['id']/'page-0/plot.hpgl').read_text())
            registered=paths((out/'driver-state'/jid/'source.hpgl').read_text())
            matrix={0:[40,0,0,-40,-9116,5512],90:[0,40,40,0,-9116,-5664],
                    180:[-40,0,0,40,8156,-5664],270:[0,-40,-40,0,8156,5512]}[angle]
            assert registration['sheet_to_device_units']==matrix,registration
            assert len(original)==len(registered)
            a,b,c,d,e,f=matrix
            for (pen,points),(actual_pen,actual_points) in zip(original,registered):
                expected=[]
                for px,py in points:
                    x,y=px/40,size[1]-py/40
                    expected.append([math.floor(a*x+c*y+e+.5),math.floor(b*x+d*y+f+.5)])
                assert (actual_pen,actual_points)==(pen,expected)
            # Inventory is only in this disposable, hardware-disabled server.
            carousel=client.read('/api/carousel')
            for pen in job['summary']['pens']:
                carousel['slots'][str(pen['id'])].update(state='loaded',color=pen['color'],width_mm=pen['width_mm'],label='Fixture pen')
            carousel=request('/api/carousel',carousel,'PUT');setup['carousel_revision']=carousel['revision']
            setup['pen_map']={str(p['id']):p['id'] for p in job['summary']['pens']}
            hpgl=request('/api/jobs/'+jid+'/prepared.hpgl',setup,raw=True)
            (output/'prepared.hpgl').write_bytes(hpgl)
            assert len(paths(hpgl.decode()))==len(original)
            results.append(dict(angle=angle,job_id=jid,registration=registration,paths=len(original),prepared_bytes=len(hpgl)))
        (out/'results.json').write_text(json.dumps(dict(passed=True,physical_plotting=False,orientations=results),indent=2)+'\n')
        subprocess.run(['node',str(ROOT/'tools/check_cartographer_orientation.cjs'),base,str(out)],
                       env=dict(os.environ,NODE_PATH='/root/plotline/controller/node_modules'),check=True)
        print(json.dumps(dict(passed=True,directory=str(out),orientations=[r['angle'] for r in results],physical_plotting=False)))
    finally:
        process.terminate();process.wait(timeout=10);log.close()


if __name__=='__main__':main()
