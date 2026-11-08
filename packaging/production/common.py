"""Production configuration and read-only checks. No plotter I/O."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import uuid

UNITS = ('plotline-ipp', 'plotline-map', 'plotline-web')
SCHEMA_VERSION = 1


class Busy(RuntimeError):
    pass


def run(*args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)


def atomic(path, value):
    path = Path(path)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temp.open('x') as output:
            json.dump(value, output, indent=2); output.write('\n')
            output.flush(); os.fsync(output.fileno())
        temp.replace(path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def safe_path(value):
    if not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
        raise ValueError('Use absolute paths without spaces or shell/systemd metacharacters')
    path = Path(value)
    if '..' in path.parts or str(path) == '/':
        raise ValueError('Unsafe path')
    return path


def config(path):
    defaults = json.loads(Path(__file__).with_name('config.example.json').read_text())
    supplied = json.loads(Path(path).read_text())
    if unknown := supplied.keys() - defaults.keys():
        raise ValueError('Unknown configuration keys: ' + ', '.join(sorted(unknown)))
    result = defaults | supplied
    for field in ('data_root', 'install_root'):
        safe_path(result[field])
    data, install = Path(result['data_root']), Path(result['install_root'])
    if data == install or data in install.parents or install in data.parents:
        raise ValueError('Retained data and rebuildable installation paths must be separate')
    url = urlsplit(result['public_url'])
    if (url.scheme not in ('http', 'https') or not url.hostname or url.path not in ('', '/')
            or url.username or url.password or url.query or url.fragment
            or any(c in result['public_url'] for c in '\n\r%"\\')):
        raise ValueError('public_url must be an HTTP(S) origin')
    result['public_url'] = result['public_url'].rstrip('/')
    if not re.fullmatch(r'(?:0\.0\.0\.0|127\.0\.0\.1|[0-9.]+):[0-9]+', result['web_bind']):
        raise ValueError('web_bind must be an IPv4 address and port')
    ports = [int(result['web_bind'].rsplit(':', 1)[1]), result['map_port'], result['ipp_port']]
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 3:
        raise ValueError('Use three distinct unprivileged ports')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', result['ipp_hostname']):
        raise ValueError('ipp_hostname must be a DNS hostname')
    if not re.fullmatch(r'[A-Za-z0-9 ._-]{1,60}', result['ipp_name']):
        raise ValueError('Use a short IPP name without control characters')
    if type(result['ipp_discovery']) is not bool:
        raise ValueError('ipp_discovery must be boolean')
    for key in ('service_uid','service_gid'):
        if type(result[key]) is not int or not 100 <= result[key] < 65534:
            raise ValueError(f'{key} must be a non-root UID/GID below 65534')
    for key, maximum in [('conversion_workers', 4), ('map_workers', 4), ('geometry_workers', 16),
                         ('map_geometry_threads', 16), ('build_jobs', 16)]:
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(f'{key} must be between 1 and {maximum}')
    if result['device_config']:
        device = safe_path(result['device_config'])
        if data not in device.parents:
            raise ValueError('device_config and credentials belong in the retained data mount')
        connection = json.loads(device.read_text())
        for key in ('identity_file', 'known_hosts_file'):
            credential = safe_path(connection[key])
            if data not in credential.parents or not credential.is_file():
                raise ValueError(f'{key} must exist inside the retained data mount')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@:-]*', connection.get('destination', '')):
            raise ValueError('Invalid SSH destination')
    return result


def url(c, component):
    port = int(c['web_bind'].rsplit(':', 1)[1]) if component == 'web' else c['map_port']
    host = c['web_bind'].rsplit(':', 1)[0] if component == 'web' else '127.0.0.1'
    if host == '0.0.0.0': host = '127.0.0.1'
    return f'http://{host}:{port}'


def read_json(url):
    with urlopen(url, timeout=3) as response:
        return json.load(response)


def active(unit):
    value = run('systemctl', 'show', unit, '--property=ActiveState', '--value',
                stdout=subprocess.PIPE, text=True).stdout.strip()
    if value in ('activating', 'deactivating', 'reloading'):
        raise Busy(f'{unit} is {value}; wait for it to settle and retry.')
    return value == 'active'


def stop_services():
    loaded = [unit for unit in ('plotline.target', *UNITS) if run(
        'systemctl','show',unit,'--property=LoadState','--value',
        stdout=subprocess.PIPE,text=True).stdout.strip() != 'not-found']
    if loaded: run('systemctl','stop',*loaded)


@contextlib.contextmanager
def admission(c):
    # Installer-owned file is never replaced: all services lock this same inode.
    path = Path(c['data_root']) / 'admission.lock'
    with path.open('a+b') as lock:
        os.chmod(path, 0o644)
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy('An HTTP request, PDF conversion or IPP upload is active. Wait for it to finish, then retry publication.') from None
        yield


def durable_busy(c, web_running=True, map_running=True):
    data = Path(c['data_root']); reasons = []
    if web_running:
        for kind in ('documents', 'conversions'):
            for path in (data/'studios'/kind).glob('*/record.json'):
                value = json.loads(path.read_text())
                if value.get('status') in ('received', 'inspecting', 'queued', 'running'):
                    reasons.append(f'{kind}/{path.parent.name} is {value["status"]}')
    pointer = data/'studios/plots/active.json'
    if pointer.exists():
        identity = json.loads(pointer.read_text())
        if str(uuid.UUID(identity)) != identity:
            raise ValueError('Invalid active plot pointer; inspect retained state')
        value = json.loads((pointer.parent/identity/'record.json').read_text())
        execution = value.get('execution') or {}
        if not (execution.get('state') in ('finished', 'reset_acknowledged')
                or value.get('phase') == 'failed' and not execution):
            reasons.append(f'plot {identity} is {execution.get("state", value.get("phase"))}; finish or resolve it in HPGL Studio and confirm the pen is parked')
    if map_running:
        for kind, phases in [('preparations', ('rendering', 'handoff')),
                             ('submissions', ('queued','uploading','inspecting','submitting','converting','preparing'))]:
            for path in (data/'maps'/kind).glob('*.json'):
                value = json.loads(path.read_text())
                if value.get('phase') in phases:
                    reasons.append(f'map {kind}/{path.stem} is {value["phase"]}')
    return reasons


def ipp(c, filename):
    result = run('ipptool', '-T', '3', '-tv', f'ipp://127.0.0.1:{c["ipp_port"]}/ipp/print',
                 Path(__file__).with_name(filename), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 text=True, timeout=8)
    return result.stdout


def idle(c):
    running = {unit: active(unit) for unit in UNITS}
    reasons = durable_busy(c, running['plotline-web'], running['plotline-map'])
    for component in ('web', 'map'):
        if not running['plotline-' + component]: continue
        try:
            state = read_json(url(c, component) + '/api/maintenance')
            if state.get('admission_protocol') != 1:
                raise ValueError('server lacks admission protocol 1')
            if component == 'map' and (state.get('jobs') or state.get('thumbnails')):
                reasons.append(f'Map Studio has {len(state.get("jobs", []))} active renders and {state.get("thumbnails", 0)} thumbnails')
        except Exception as error:
            reasons.append(f'{component} readiness cannot be checked: {error}')
    if running['plotline-ipp']:
        try:
            state = ipp(c, 'ipp-idle.test')
            if re.search(r'job-state \(enum\) = (pending|pending-held|processing|processing-stopped)', state):
                reasons.append('IPP has an incomplete or processing job; finish/cancel it in the submitting application')
        except Exception as error:
            reasons.append(f'IPP intake cannot be checked: {error}')
    if reasons:
        raise Busy('; '.join(reasons) + '. Services were left running. Retry after the work finishes.')
    return running


def readiness(c, revision, timeout=30):
    deadline = time.monotonic() + timeout; last = 'not started'
    while time.monotonic() < deadline:
        try:
            for unit in UNITS:
                if not active(unit): raise RuntimeError(unit + ' is not active')
            checks = {}
            for component in ('web','map'):
                state = read_json(url(c,component) + '/api/maintenance')
                if state.get('source_revision') != revision or state.get('admission_protocol') != 1:
                    raise RuntimeError(f'{component} revision/admission mismatch')
                checks[component] = dict(revision=state['source_revision'],ready=True)
            ipp(c, 'ipp-ready.test'); checks['ipp'] = dict(ready=True)
            storage=json.loads((Path(c['data_root'])/'studios/storage-status.json').read_text())
            if storage.get('errors') != [] or not storage.get('policy'):
                raise RuntimeError('Storage retention sweep failed; inspect studios/storage-status.json')
            checks['storage']=dict(ready=True,last_sweep=storage['last_sweep'])
            return checks
        except Exception as error:
            last = str(error); time.sleep(.25)
    raise RuntimeError('Readiness failed: ' + last)
