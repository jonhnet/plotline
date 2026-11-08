#!/usr/bin/env python3
"""Install an exported Plotline revision. IaC alone selects and promotes releases."""
import argparse
import hashlib
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time

from common import (Busy, SCHEMA_VERSION, UNITS, active, admission, atomic, config,
                    idle, readiness, run, safe_path, stop_services, url)
from build import build, dependencies, NODE


def users(c):
    # Stable numeric ownership survives guest recreation with the same mount.
    try:
        group=grp.getgrnam('plotline')
        if group.gr_gid!=c['service_gid']: raise ValueError('Existing plotline group has a different GID; reconcile the account before installation')
    except KeyError: run('groupadd','--system','--gid',c['service_gid'],'plotline')
    try:
        account=pwd.getpwnam('plotline')
        if account.pw_uid!=c['service_uid'] or account.pw_gid!=c['service_gid']:
            raise ValueError('Existing plotline user has a different UID/GID; reconcile the account before installation')
    except KeyError:
        run('useradd','--system','--uid',c['service_uid'],'--gid',c['service_gid'],
            '--home-dir',Path(c['data_root'])/'home','--shell','/usr/sbin/nologin','plotline')
    for name, home in [('plotline',Path(c['data_root'])/'home'),
                       ('plotline-build',Path(c['install_root'])/'build-home')]:
        try: pwd.getpwnam(name)
        except KeyError: run('useradd','--system','--user-group','--home-dir',home,'--shell','/usr/sbin/nologin',name)
        home.mkdir(parents=True,exist_ok=True); shutil.chown(home,user=name,group=name); home.chmod(0o750)


def state(c):
    root = Path(c['data_root'])
    if not root.is_dir() or not os.path.ismount(root):
        raise ValueError(f'{root} must already be the retained mount supplied by IaC; refusing ephemeral state')
    for name in ('studios','maps','ipp','cache','cache/build','cache/maps','cache/xdg','home'):
        path = root/name; path.mkdir(parents=True,exist_ok=True)
        shutil.chown(path,user='plotline',group='plotline'); path.chmod(0o750)
    schema = root/'state-schema.json'
    if schema.exists() and json.loads(schema.read_text()).get('version') != SCHEMA_VERSION:
        raise ValueError('Unsupported state schema; restore a compatible backup/release before installation')
    if not schema.exists(): atomic(schema,dict(version=SCHEMA_VERSION))
    # No starter/demo carousel in production: this is real physical inventory.


def write_if_changed(path, text):
    if path.exists() and path.read_text() == text: return
    temporary = path.with_suffix('.new'); temporary.write_text(text); temporary.replace(path)


def service_files(root,c,revision):
    data = Path(c['data_root']); install = Path(c['install_root'])
    env = {
        'PLOTLINE_REVISION':revision,'PLOTLINE_ADMISSION_LOCK':str(data/'admission.lock'),
        'PLOTLINE_PDF_PROJECT':str(root),'PLOTLINE_IPP_INBOX':str(data/'ipp/captures'),
        'PLOTLINE_MAP_UPSTREAM':f'http://127.0.0.1:{c["map_port"]}',
        'PLOTLINE_MAP_PUBLIC_URL':c['public_url']+'/map',
        'PLOTLINE_CALIBRATION_PYTHON':str(root/'.calibration/bin/python'),
        'PLOTLINE_CONVERSION_WORKERS':str(c['conversion_workers']),
        'PLOTLINE_MAP_WORKERS':str(c['map_workers']),
        'PLOTLINE_GEOMETRY_WORKERS':str(c['geometry_workers']),
        'PLOTLINE_MAP_GEOMETRY_THREADS':str(c['map_geometry_threads']),
        'PLOTLINE_SINK_STATE':str(data/'ipp'),'PLOTLINE_SINK_PORT':str(c['ipp_port']),
        'PLOTLINE_SINK_BINARY':str(root/'runtime/ippeveprinter'),
        'PLOTLINE_SINK_NAME':c['ipp_name'],'PLOTLINE_SINK_HOST_ALIAS':c['ipp_hostname'],
        'PLOTLINE_SINK_DISCOVERY':'_print' if c['ipp_discovery'] else 'off',
        'PLOTLINE_SINK_INTAKE':'1','PLOTLINE_REVIEW_URL':c['public_url']+'/',
        'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
        'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
        'XDG_CACHE_HOME':str(data/'cache/xdg'),
        'PATH':f'{install}/toolchains/node-v{NODE}-linux-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    }
    directory=Path('/etc/plotline'); directory.mkdir(exist_ok=True)
    write_if_changed(directory/'runtime.env',''.join(f'{key}="{value}"\n' for key,value in env.items()))
    web=f'{root}/controller/target/release/plotline-studios --listen {c["web_bind"]} --state {data}/studios'
    if c['device_config']: web += ' --device-config '+c['device_config']
    commands={
        'web':web,
        'map':f'{root}/.venv/bin/python -m cartographer.service --host 127.0.0.1 --port {c["map_port"]} --state {data}/maps --build {data}/cache/maps --studios {url(c,"web")} --studios-public {c["public_url"]}',
        'ipp':f'{root}/tools/print_sink/run.sh',
    }
    for name,command in commands.items():
        after='network.target'+(' plotline-web.service' if name!='web' else '')
        if name=='ipp' and c['ipp_discovery']: after += ' avahi-daemon.service'
        requires='Wants=avahi-daemon.service\n' if name=='ipp' and c['ipp_discovery'] else ''
        text=f'''[Unit]
Description=Plotline Studios {name}
After={after}
{requires}PartOf=plotline.target
RequiresMountsFor={data}

[Service]
Type=simple
User=plotline
Group=plotline
WorkingDirectory={root}
EnvironmentFile=/etc/plotline/runtime.env
ExecStart={command}
Restart=on-failure
RestartSec=3
TimeoutStopSec=60
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={data}
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
LimitNOFILE=8192
TasksMax=512
'''
        write_if_changed(Path('/etc/systemd/system')/f'plotline-{name}.service',text)
    write_if_changed(Path('/etc/systemd/system/plotline.target'),'''[Unit]
Description=Plotline Studios
Wants=plotline-web.service plotline-map.service plotline-ipp.service
After=plotline-web.service plotline-map.service plotline-ipp.service

[Install]
WantedBy=multi-user.target
''')


def dependency_check(root):
    run('runuser','-u','plotline','--',str(root/'.venv/bin/python'),'-c',
        'import pymupdf, shapely, _cartographer_geometry; import cartographer.service',cwd=root)
    run('runuser','-u','plotline','--',str(root/'.calibration/bin/python'),'-c','import numpy, scipy, PIL',cwd=root)
    run('runuser','-u','plotline','--','python3','-c','from raster_lab.engine import check_dependencies; check_dependencies()',cwd=root)


def checked_receipt(c, revision):
    receipt=Path(c['data_root'])/'installation.json'
    value=json.loads(receipt.read_text())
    if value['installed_revision']!=revision:
        raise ValueError('Installation receipt revision mismatch')
    value.pop('error',None)
    try:
        checks=readiness(c,revision)
    except Exception as error:
        atomic(receipt,dict(value,ready=False,checked_at=time.time(),error=str(error)))
        raise
    atomic(receipt,dict(value,ready=True,checked_at=time.time(),checks=checks))
    return checks


def install(args,c):
    source=safe_path(str(args.source)).resolve(strict=True)
    if (source/'.git').exists(): raise ValueError('Supply an exported snapshot without .git')
    stamped=(source/'SOURCE_REVISION').read_text().strip()
    if stamped!=args.revision:
        raise ValueError('SOURCE_REVISION does not match --revision; export with git archive')
    root=Path(c['install_root']); root.mkdir(parents=True,exist_ok=True)
    if not os.path.ismount(c['data_root']): raise ValueError('IaC must mount data_root before installation')
    users(c); state(c)
    if c['device_config']:
        connection=json.loads(Path(c['device_config']).read_text())
        for path in [c['device_config'],connection['identity_file'],connection['known_hosts_file']]:
            run('runuser','-u','plotline','--','test','-r',path)
    with admission(c):
        idle(c)
        # Build under the closed admission gate. Existing services remain running
        # until the entire new build has passed its local dependency checks.
        env=dependencies(c)
        destination=build(source,args.revision,c,env)
        dependency_check(destination)
        fingerprint=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()
        receipt=Path(c['data_root'])/'installation.json'
        previous=json.loads(receipt.read_text()) if receipt.exists() else {}
        current=root/'current'
        if (current.exists() and current.resolve()==destination and previous.get('config_sha256')==fingerprint
                and all(active(unit) for unit in UNITS)):
            checks=checked_receipt(c,args.revision)
        else:
            # Recheck immediately before activation, with admission still closed.
            idle(c)
            stop_services()
            service_files(destination,c,args.revision)
            temporary=root/'current.new';temporary.unlink(missing_ok=True);temporary.symlink_to(destination);temporary.replace(current)
            atomic(receipt,dict(installed_revision=args.revision,ready=False,config_sha256=fingerprint,
                                state_schema=SCHEMA_VERSION,installed_at=time.time()))
            run('systemctl','daemon-reload')
            run('systemctl','enable','plotline.target')
            run('systemctl','start','plotline.target')
            checks=checked_receipt(c,args.revision)
    print(json.dumps(dict(installed_revision=args.revision,ready=True,checks=checks)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','check','idle','stop'])
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--revision')
    args=parser.parse_args()
    if os.geteuid()!=0: parser.error('Run as root inside the application guest')
    if args.action in ('install','check') and not re.fullmatch(r'[0-9a-f]{40}',args.revision or ''):
        parser.error('Supply an explicit full 40-character --revision')
    if args.action=='install' and args.source is None: parser.error('--source is required')
    c=config(args.config)
    if not os.path.ismount(c['data_root']):
        raise ValueError('data_root is not the retained mount; refusing to use ephemeral storage')
    try:
        if args.action=='install': install(args,c)
        elif args.action=='check':
            print(json.dumps(checked_receipt(c,args.revision)))
        else:
            with admission(c):
                status=idle(c)
                if args.action=='stop': stop_services()
            print(json.dumps(dict(idle=True,running=status,stopped=args.action=='stop')))
    except Busy as error:
        print(str(error),file=sys.stderr);return 75
    return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:
        print(f'Plotline installation failed: {error}',file=sys.stderr);raise SystemExit(1)
