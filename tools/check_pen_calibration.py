#!/usr/bin/env python3
"""Complete calibration API/browser acceptance on an isolated preview-only server."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scan_fixture',ROOT/'tests/test_pen_calibration.py');fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path,required=True);args=parser.parse_args()
    output=Path(tempfile.mkdtemp(prefix='calibration-check-',dir=ROOT/'build'))
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    def api(path,body=None,method=None):
        data=json.dumps(body).encode() if body is not None else None
        req=Request(base+path,data=data,method=method,headers={'Content-Type':'application/json','X-Plotline-Request':'1'})
        with urlopen(req,timeout=30) as response:return json.load(response)
    with (output/'server.log').open('w') as log:
        env=dict(os.environ,PLOTLINE_PDF_PROJECT=str(ROOT),PLOTLINE_IPP_INBOX=str(output/'no-inbox'))
        process=subprocess.Popen([str(args.binary),'--state',str(output/'state'),'--listen',f'127.0.0.1:{port}'],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('Isolated server exited: '+(output/'server.log').read_text())
                try:api('/api/status');break
                except OSError:time.sleep(.1)
            inventory=api('/api/carousel')
            for slot,color,width in [(2,'#ff0000',None),(5,'#5d9b31',.7)]:inventory['slots'][str(slot)].update(state='loaded',label='Test '+str(slot),color=color,width_mm=width,measured_color='#5c5a58')
            api('/api/carousel',inventory,'PUT')
            # Browser creates the sheet itself, then pauses while we synthesize
            # a scan from that exact target. All inventory writes remain isolated.
            node=subprocess.Popen(['node',str(ROOT/'tools/check_pen_calibration.cjs'),base,str(output)],env=dict(env,NODE_PATH='/root/plotline/controller/node_modules'),cwd=ROOT)
            try:
                for _ in range(300):
                    if node.poll() is not None:raise RuntimeError('Browser failed before target creation')
                    if (output/'target.json').exists():break
                    time.sleep(.1)
                target=json.loads((output/'target.json').read_text())
                # The physical ink differs from its schematic design swatch.
                target['carousel']['slots']['2']['color']='#466b8f'
                fixture.synthetic_scan(target,output/'scan.png',dpi=600,angle=-3,widths={1:.3,2:.3,5:.7})
                (output/'scan.ready').write_text('ready')
                if node.wait(timeout=90):raise RuntimeError('Browser calibration check failed')
            finally:
                if node.poll() is None:node.terminate();node.wait()
            result=json.loads((output/'results.json').read_text());print(json.dumps(dict(result,directory=str(output))))
        finally:process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
