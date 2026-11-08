#!/usr/bin/env python3
"""Persistent serial-transport fixture. No network or serial device access."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

parser=argparse.ArgumentParser()
parser.add_argument('--config',type=Path,required=True)
parser.add_argument('--control-dir')
args=parser.parse_args()
root=Path(json.loads(args.config.read_text())['fixture_dir'])
opened=False
reply=[]
started=False
completion_reply=False
reply_ready_at=0
timing_origin=None
timing_bytes=0

def controls():
    return json.loads((root/'controls.json').read_text())

def owner_alive(pid):
    try:
        return Path('/proc',str(pid),'stat').read_text().split()[2] != 'Z'
    except FileNotFoundError:
        return False

def change(fn):
    with (root/'fixture.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=root/'fixture.json'
        state=json.loads(path.read_text()) if path.exists() else {'opens':0,'starts':0,'writes':[],'queries':[],'owner':None}
        if state['owner'] and not owner_alive(state['owner']):state['owner']=None
        result=fn(state)
        path.write_text(json.dumps(state))
        return result

try:
    for line in sys.stdin:
        request=json.loads(line); c=controls(); op=request['op']
        if c.get('offline'):
            raise ConnectionError('Simulated Wi-Fi loss')
        if op=='status':
            result=change(lambda s:{'protocol':2,'serial_present':True,'busy':s['owner'] is not None,'capabilities':['serial_bridge']})
        elif op=='open':
            def begin(s):
                assert s['owner'] is None
                s['owner']=os.getpid();s['opens']+=1
            change(begin);opened=True;result={'opened':True,'protocol':2,'capabilities':[] if c.get('no_timing') else ['tx_queue_timing_v1']}
        elif op=='timing':
            assert opened and timing_origin is None
            timing_origin=time.monotonic_ns()
            result={'schema':'tx_queue_timing_v1','clock':'time.monotonic_ns','origin_ns':timing_origin,'fixture':True}
        elif op=='write':
            assert opened
            data=bytes(request['data'])
            assert 0<len(data)<=512
            if timing_origin is not None:
                elapsed=time.monotonic_ns()-timing_origin
                print(json.dumps({'event':'tx_queue_timing_v1','samples':[[elapsed,elapsed,timing_bytes,0]]}),flush=True)
                timing_bytes+=len(data)
                elapsed=time.monotonic_ns()-timing_origin
                print(json.dumps({'event':'tx_queue_timing_v1','samples':[[elapsed,elapsed,timing_bytes,0]]}),flush=True)
            if data in (b'OI;',b'OH;',b'OA;\n',b'OS;\n'):
                change(lambda s:s['queries'].append(data.decode()))
                if data in (b'OI;',b'OH;'):time.sleep(c.get('query_delay_ms',0)/1000)
            if data==b'OI;':reply=[] if c.get('plotter_off') else list(b'7585B\r')
            elif data==b'OH;':reply=list((','.join(map(str,c.get('limits',[-8000,-5000,8000,5000])))+'\r').encode())
            elif data==b'OA;\n':
                completion_reply=True
                reply_ready_at=time.monotonic()+c.get('completion_delay_ms',0)/1000
                reply=list(c.get('completion_reply','-123,456,0\r').encode())
            elif data==b'OS;\n':reply=list((str(c.get('completion_status',16))+'\r').encode())
            else:
                def record(s):
                    s['writes'].append(data.decode())
                    if not started:s['starts']+=1
                change(record);started=True
                if c.get('lose_ack'):
                    sys.exit(0)
                time.sleep(c.get('delay_ms',500)/1000)
            result={'written':len(data)}
        elif op=='read':
            assert opened
            if not reply:time.sleep(min(request.get('timeout_ms',0)/1000,.05))
            if completion_reply and c.get('completion_disconnect'):raise ConnectionError('Lost completion response')
            if completion_reply and (c.get('completion_hold') or time.monotonic()<reply_ready_at):
                time.sleep(min(request.get('timeout_ms',0)/1000,.05));result={'data':[]}
            else:
                size=c.get('completion_chunk_bytes',len(reply)) if completion_reply else len(reply)
                result={'data':reply[:size]};reply=reply[size:]
        elif op=='close':
            print(json.dumps({'ok':True,'result':{'closed':True}}),flush=True)
            break
        else:raise ValueError('Unknown transport operation')
        print(json.dumps({'ok':True,'result':result}),flush=True)
except Exception as error:
    print(json.dumps({'ok':False,'error':str(error)}),flush=True)
    sys.exit(1)
finally:
    if opened:
        def close(s):
            if s['owner']==os.getpid():s['owner']=None
        change(close)
