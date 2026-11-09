#!/usr/bin/env python3
"""Run a temporary, isolated Web/Map test session. Never enables dev services."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "controller/state/jobs"  # Only for explicit legacy idle audits.


def run(*args):
    subprocess.run(args, check=True, cwd=ROOT)


def require_idle(state=STATE):
    """Never interrupt PDF work, a live feed, or unconfirmed physical completion."""
    for kind in ('documents', 'conversions'):
        for path in (state / kind).glob('*/record.json'):
            record = json.loads(path.read_text())
            if record.get('status') in ('received', 'inspecting', 'queued', 'running'):
                raise SystemExit(f"Plotline Studios has active PDF work ({kind}/{path.parent.name}). Wait for it to finish before restarting; the service was left running.")
    active = state / "plots/active.json"
    if not active.exists():
        return
    attempt = json.loads(active.read_text())
    # Only canonical UUIDs are valid spool paths.
    from uuid import UUID
    if str(UUID(attempt)) != attempt:
        raise SystemExit("Invalid active plot ID; inspect the spool before restarting")
    record = json.loads((state / "plots" / attempt / "record.json").read_text())
    execution = record.get("execution")
    phase = record.get("phase")
    if ((execution and execution.get("state") in ("finished", "reset_acknowledged"))
            or (phase == "failed" and not execution)):
        return
    status = (execution or {}).get("state", phase)
    raise SystemExit(f"Plotline Studios is {status}. Finish the current plot and confirm its pen is parked before restarting; the service was left running.")


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def session_environment(state, web_port, map_port):
    # Do not inherit a production connection, inbox, state path or worker URL.
    env = {key: value for key, value in os.environ.items() if not key.startswith('PLOTLINE_')}
    env.update(PLOTLINE_PDF_PROJECT=str(ROOT),
               PLOTLINE_MAP_UPSTREAM=f'http://127.0.0.1:{map_port}',
               PLOTLINE_MAP_PUBLIC_URL=f'http://127.0.0.1:{web_port}/map',
               PLOTLINE_ADMISSION_LOCK=str(state/'admission.lock'),
               PLOTLINE_TEST_URL=f'http://127.0.0.1:{web_port}',
               PLOTLINE_TEST_MAP_URL=f'http://127.0.0.1:{map_port}',
               PLOTLINE_TEST_STATE=str(state))
    return env


def read_status(url):
    with urlopen(url + '/api/maintenance', timeout=3) as response:
        return json.load(response)


def wait_ready(children, urls):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError('A test service exited during startup')
        try:
            if all(read_status(url).get('admission_protocol') == 1 for url in urls):
                return
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError('Test services did not become ready within 30 seconds')


def stop_session(children, state, map_url, ready=True):
    # Close admission before checking work, so a browser cannot race teardown.
    # SIGINT requests shutdown but must not interrupt an in-flight conversion.
    announced = False
    with (state/'admission.lock').open('a+b') as lock:
        while True:
            locked = False
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                if children and children[0].poll() is None:
                    require_idle(state/'studios')
                # A failed startup has fresh state and no test command/browser
                # session yet; its Map HTTP endpoint may never become ready.
                if ready and len(children) > 1 and children[1].poll() is None:
                    status = read_status(map_url)
                    if status.get('jobs') or status.get('thumbnails'):
                        raise RuntimeError('Map work is active')
                break
            except (BlockingIOError, SystemExit, RuntimeError, OSError, ValueError):
                if locked:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                if not announced:
                    print('Waiting for test work to finish before stopping. Cancel unwanted work in the test browser.', flush=True)
                    announced = True
                time.sleep(1)
        for child in reversed(children):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run'])
    parser.add_argument('--no-build', action='store_true', help='Use the existing isolated build')
    parser.add_argument('--binary', type=Path, help='Use a particular Studios binary')
    parser.add_argument('--web-port', type=int, default=0, help='Loopback browser port; default: unused port')
    parser.add_argument('--map-port', type=int, default=0, help='Loopback Map worker port; default: unused port')
    parser.add_argument('--command', nargs=argparse.REMAINDER, help='Run a test command, then stop both services even if the test fails')
    args = parser.parse_args()
    target = ROOT/'build/dev-controller-target'
    if not args.no_build:
        run('cargo', 'build', '--locked', '--release')
        run('cargo', 'build', '--locked', '--release', '--manifest-path', str(ROOT/'controller/Cargo.toml'), '--target-dir', str(target))
    binary = (args.binary or target/'release/plotline-studios').resolve(strict=True)
    (ROOT/'build').mkdir(exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix='dev-session-', dir=ROOT/'build'))
    (state/'admission.lock').touch()
    web_port, map_port = args.web_port or port(), args.map_port or port()
    if web_port == map_port:
        parser.error('Web and Map ports must differ')
    env = session_environment(state, web_port, map_port)
    commands = [
        [str(binary), '--listen', f'127.0.0.1:{web_port}', '--state', str(state/'studios')],
        [str(ROOT/'.venv/bin/python'), '-m', 'cartographer.service', '--host', '127.0.0.1',
         '--port', str(map_port), '--state', str(state/'maps'), '--build', str(state/'cache'),
         '--studios', env['PLOTLINE_TEST_URL'], '--studios-public', env['PLOTLINE_TEST_URL']],
    ]
    children = []
    ready = False
    stopping = False
    def request_stop(*_):
        nonlocal stopping
        stopping = True
    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True))
        wait_ready(children, [env['PLOTLINE_TEST_URL'], env['PLOTLINE_TEST_MAP_URL']])
        ready = True
        print(f"Test session: {env['PLOTLINE_TEST_URL']} · isolated state: {state}", flush=True)
        if args.command:
            return subprocess.run(args.command, cwd=ROOT, env=env).returncode
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(.2)
        return 0 if stopping else 1
    finally:
        try:
            stop_session(children, state, env['PLOTLINE_TEST_MAP_URL'], ready=ready)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        print('Test services stopped.', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
