#!/usr/bin/env python3
"""Install the serial bridge on an idle Pi; retain the legacy spool as evidence.

When migrating protocol 1, --dev-state imports the last execution into Plotline Studios.
Only use --completed-attempt after observing the pen finish/park.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

REMOTE = r'''
import hashlib,json,os,shutil,socket,subprocess,time,uuid
from pathlib import Path
payload=json.loads(PAYLOAD)
source=payload['source']
namespace='plotline' if Path('/etc/plotline/device.json').exists() else 'squidprint'
service=namespace+'-device'
if namespace!='plotline':payload['unit']=payload['unit'].replace('plotline',namespace)
compile(source,f'/opt/{namespace}/device.py','exec')
def rpc(protocol):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect(f'/run/{namespace}/device.sock')
        connection.sendall((json.dumps({'protocol':protocol,'op':'status'})+'\n').encode())
        reply=json.loads(connection.makefile('rb').readline())
    if not reply.get('ok'):raise RuntimeError(reply.get('error'))
    return reply['result']
try:status=rpc(2)
except RuntimeError:status=rpc(1)
execution=status.get('execution')
if status.get('busy'):raise RuntimeError('The serial bridge is in use; stop the connection before updating')
if execution and execution['state'] not in ('finished','reset_acknowledged'):
    if execution['state'] not in ('submitted','cancelled') or payload.get('completed_attempt') != execution['attempt']:
        raise RuntimeError('Plot is active or physical completion has not been confirmed; left unchanged')
    execution=dict(execution,state='finished',completion='operator confirmed pen parked before bridge migration')
if status.get('protocol')==1 and execution and not payload['migrate_state']:
    raise RuntimeError('Supply --dev-state so the old execution is preserved in Plotline Studios')
script=Path(f'/opt/{namespace}/device.py');config_path=Path(f'/etc/{namespace}/device.json')
unit=Path(f'/etc/systemd/system/{service}.service')
config={'device':json.loads(config_path.read_text())['device']}
before=hashlib.sha256(script.read_bytes()).hexdigest();after=hashlib.sha256(source.encode()).hexdigest()
report={'before_sha256':before,'installed_sha256':after,'previous_status':status,'previous_execution':execution}
if payload['inspect']:
    report.update(ready_to_install=True);print(json.dumps(report));raise SystemExit(0)
backup=Path(f'/var/lib/{namespace}/upgrades')/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(uuid.uuid4()))
backup.mkdir(parents=True,mode=0o700)
for path in (script,config_path,unit):shutil.copy2(str(path),str(backup/path.name))
(backup/'migration.json').write_text(json.dumps(report,indent=2)+'\n')
subprocess.run(['systemctl','stop',service],check=True)
try:
    for path,text in ((script,source),(config_path,json.dumps(config,indent=2)+'\n'),(unit,payload['unit'])):
        with path.open('w') as output:output.write(text);output.flush();os.fsync(output.fileno())
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','start',service],check=True)
    for i in range(100):
        try:
            installed=rpc(2)
            if installed['protocol']!=2 or installed.get('busy') or 'tx_queue_timing_v1' not in installed.get('capabilities',[]):raise RuntimeError('Bridge timing readiness failed')
            break
        except (OSError,RuntimeError):
            if i==99:raise
            time.sleep(.2)
except BaseException:
    subprocess.run(['systemctl','stop',service],check=False)
    for path in (script,config_path,unit):shutil.copy2(str(backup/path.name),str(path))
    subprocess.run(['systemctl','daemon-reload'],check=False)
    subprocess.run(['systemctl','start',service],check=False)
    raise
report.update(backup=str(backup),device=installed)
print(json.dumps(report))
'''


def atomic(path, value):
    temp=path.with_suffix('.'+str(uuid.uuid4())+'.tmp')
    with temp.open('w') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--known-hosts',type=Path,required=True)
    parser.add_argument('--completed-attempt')
    parser.add_argument('--dev-state',type=Path,help='Plotline Studios job spool for migration; stop its service before installing')
    parser.add_argument('--inspect',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.@:-]+',args.host):parser.error('Invalid SSH destination')
    if args.dev_state and not args.dev_state.is_dir():parser.error('Dev state directory must exist')
    here=Path(__file__).parent
    payload=json.dumps({'source':(here/'device.py').read_text(),'unit':(here/'plotline-device.service').read_text(),
        'completed_attempt':args.completed_attempt,'migrate_state':bool(args.dev_state),'inspect':args.inspect})
    code='import base64\nPAYLOAD=base64.b64decode('+repr(base64.b64encode(payload.encode()).decode())+').decode()\n'+REMOTE
    output=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
        '-o','UserKnownHostsFile='+str(args.known_hosts.resolve()),args.host,'sudo','python3','-'],
        input=code,text=True,stdout=subprocess.PIPE,check=True,timeout=120)
    result=json.loads(output.stdout)
    execution=result.get('previous_execution')
    if args.dev_state and not args.inspect and execution:
        plots=args.dev_state/'plots'
        atomic(plots/'legacy-device-status.json',result)
        active=json.loads((plots/'active.json').read_text())
        if active != execution['attempt']:
            raise RuntimeError('Bridge installed, but legacy execution differs from dev active ticket; reconcile before starting the service')
        record_path=plots/active/'record.json'
        record=json.loads(record_path.read_text())
        record['execution']=execution
        record['phase']='accepted';record['error']=execution.get('error')
        atomic(record_path,record)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
