#!/usr/bin/env python3
"""Exercise durable PDF submission receipts against an isolated, device-free server.

Run with .venv/bin/python controller/scripts/submission-smoke.py after rebuilding
the release controller. Retains its PDF, state and evidence under build/.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from make_boundary_fixture import make_fixture
from print_contract import validate_schema


def main():
    directory = Path(tempfile.mkdtemp(prefix='submission-', dir=ROOT / 'build'))
    fixture = directory / 'fixture'; fixture.mkdir()
    intent = make_fixture(fixture)
    pdf = (fixture / 'boundary.pdf').read_bytes()
    intent_bytes = json.dumps(intent, sort_keys=True, separators=(',', ':')).encode()
    state = directory / 'state'
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0)); port = reserve.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, PLOTLINE_PDF_PROJECT=str(ROOT))
    env.pop('PLOTLINE_IPP_INBOX', None)  # Recovery must work without IPP enabled.
    process = None
    log = (directory / 'server.log').open('w')

    def get(path):
        with urlopen(base + path, timeout=5) as response:
            return json.load(response)

    def start():
        nonlocal process
        process = subprocess.Popen([str(ROOT / 'controller/target/release/plotline-studios'),
                                    '--listen', f'127.0.0.1:{port}', '--state', str(state)],
                                   cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
        for _ in range(100):
            try:return get('/api/v1/capabilities')
            except OSError:time.sleep(.05)
        raise AssertionError('Isolated server did not start')

    def stop():
        nonlocal process
        if process is not None:
            os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=10); process = None

    def upload(key, source=pdf, settings=intent_bytes):
        boundary = 'fixture-' + uuid.uuid4().hex
        data = []
        for name, content in [('document', source), ('intent', settings)]:
            if content is not None:
                data.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="fixture"\r\n\r\n'.encode(), content, b'\r\n'])
        data.append(f'--{boundary}--\r\n'.encode())
        headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'X-Plotline-Request': '1'}
        if key is not None:headers['Idempotency-Key'] = key
        with urlopen(Request(base + '/api/v1/documents', b''.join(data), headers), timeout=10) as response:
            assert response.status == 202
            return json.load(response)

    def rejects(code, operation):
        try:operation()
        except HTTPError as error:
            assert error.code == code, (error.code, error.read())
        else:raise AssertionError(f'Expected HTTP {code}')

    def ready(row):
        for _ in range(300):
            current = get(row['status_url'])
            if current['status'] not in ('received', 'inspecting'):
                assert current['status'] == 'ready' and not current.get('intent_error'), current
                return current
            time.sleep(.1)
        raise AssertionError('Inspection did not finish')

    try:
        caps = start(); validate_schema('capabilities', caps)
        assert caps['implemented']['pdf_submission_idempotency'] is True
        key = str(uuid.uuid4()); lookup = '/api/v1/submissions/' + key
        rejects(404, lambda:get(lookup))
        rejects(400, lambda:upload('not-a-uuid'))
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _:upload(key), range(8)))
        row = rows[0]
        assert {r['id'] for r in rows} == {row['id']}
        assert get(lookup)['id'] == row['id']
        assert row['source_sha256'] == hashlib.sha256(pdf).hexdigest()
        assert row['upload_intent_sha256'] == hashlib.sha256(intent_bytes).hexdigest()
        ready(row)
        rejects(409, lambda:upload(key, source=pdf + b'\n'))
        rejects(409, lambda:upload(key, settings=intent_bytes + b'\n'))
        rejects(409, lambda:upload(key, settings=None))
        assert len(get('/api/v1/documents')['documents']) == 1
        with urlopen(base + row['status_url'] + '/source.pdf') as response:
            assert response.read() == pdf
        stop()
        # Simulate the durable state left by a crash during inspection. The
        # real restart must resume it even with no IPP inbox configured.
        record_path = state / 'documents' / row['id'] / 'record.json'
        record = json.loads(record_path.read_text()); record['status'] = 'inspecting'
        record_path.write_text(json.dumps(record))
        start()
        assert get(lookup)['id'] == row['id']
        assert upload(key)['id'] == row['id']
        document = ready(row)
        assert document['intent'] == intent
        assert document['conversions'] == [] and get('/api/jobs') == []
        assert len(get('/api/v1/documents')['documents']) == 1
        # No key preserves ordinary uploads; a new key means a new submission.
        assert upload(str(uuid.uuid4()))['id'] != row['id']
        one, two = upload(None), upload(None)
        assert one['id'] != two['id']
        result = dict(passed=True, document_id=row['id'], checks=[
            'concurrent same-key uploads create one original', 'exact PDF and intent binding',
            'changed payload rejected with 409', 'lookup before/after restart',
            'interrupted inspection resumes without IPP', 'no conversions or physical plotting',
            'unkeyed clients unchanged'])
        (directory / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(passed=True, directory=str(directory))))
    finally:
        stop(); log.close()


if __name__ == '__main__':main()
