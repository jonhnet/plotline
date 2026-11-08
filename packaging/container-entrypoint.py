#!/usr/bin/env python3
"""Run the two Studios processes together, without a device or IPP connection."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen


def healthcheck():
    for url in ('http://127.0.0.1:9191/api/v1/capabilities',
                'http://127.0.0.1:9193/'):
        with urlopen(url, timeout=2) as response:
            if response.status != 200:
                return 1
    return 0


def main():
    if sys.argv[1:] == ['--healthcheck']:
        return healthcheck()
    if sys.argv[1:]:
        raise SystemExit('This image runs preview-only Studios; no device arguments are accepted.')
    public = os.environ.get('PLOTLINE_PUBLIC_URL', 'http://localhost:9191').rstrip('/')
    parsed = urlsplit(public)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise SystemExit('PLOTLINE_PUBLIC_URL must be an http(s) origin, e.g. http://localhost:9191')
    os.environ['PLOTLINE_MAP_PUBLIC_URL'] = public + '/map'
    for name in ('studios', 'maps', 'cache'):
        Path('/data', name).mkdir(parents=True, exist_ok=True)
    commands = [
        ['controller/target/release/plotline-studios', '--listen', '0.0.0.0:9191',
         '--state', '/data/studios'],
        ['.venv/bin/python', '-m', 'cartographer.service', '--host', '127.0.0.1',
         '--port', '9193', '--state', '/data/maps', '--build', '/data/cache/cartographer',
         '--studios', 'http://127.0.0.1:9191', '--studios-public', public],
    ]
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    result = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command, start_new_session=True))
        while not stopping:
            for child in children:
                code = child.poll()
                if code is not None:
                    print(f'Studios process exited ({code}); stopping the container.', flush=True)
                    return code or 1
            time.sleep(.2)
    finally:
        # Include conversion subprocesses; tini reaps orphaned grandchildren.
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 7
        for child in children:
            try:
                child.wait(timeout=max(.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
