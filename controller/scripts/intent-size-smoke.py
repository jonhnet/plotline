#!/usr/bin/env python3
"""Large map intent through upload, recipes, conversion and draft; no hardware.

Run with .venv/bin/python controller/scripts/intent-size-smoke.py after building
the release controller. Evidence and isolated state remain under build/.
"""
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

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
from print_contract import DIRECTORY, validate_intent


def fixture(directory):
    """Many real PDF layer bindings, but just two strokes to keep the test fast."""
    pdf = directory/'layers.pdf'
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    refs, bindings = [], []
    for index in range(20000):
        name = f'features/roads/local/{index}/centerline/inside/explicit-ink-binding'
        xref = doc.get_new_xref()
        doc.update_object(xref, f'<< /Type /OCG /Name ({name}) >>')
        refs.append(xref)
        bindings.append(dict(ocg_name=name, ink_id='fine-black' if index%2 == 0 else 'broad-black',
                             feature_group='roads'))
    # Register the OCG list together; add_ocg repeatedly scans prior layers.
    array = '[ '+' '.join(f'{xref} 0 R' for xref in refs)+' ]'
    doc.xref_set_key(doc.pdf_catalog(), 'OCProperties',
                     f'<< /OCGs {array} /D << /ON {array} /Order {array} >> >>')
    for index, width in enumerate((.15, .6)):
        page.draw_line((180, 180+index*30), (400, 180+index*30),
                       color=(0, 0, 0), width=width*72/25.4, oc=refs[index])
    doc.save(pdf, deflate=True)
    intent = dict(schema='plotline.print-intent/1', title='Large layered map fixture',
        source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        pages=[dict(page_index=0, source_size_mm=[431.8, 279.4],
                    target_media=json.loads((DIRECTORY/'examples/hp7585-tabloid-landscape.json').read_text()),
                    placement=dict(scale=1, rotation_deg=0, offset_mm=[0, 0]))],
        palette=json.loads((DIRECTORY/'examples/palette.json').read_text()), bindings=bindings,
        policies=dict(fill=dict(mode='none'), stroke_width_tolerance=.75, raster='reject', gradients='omit',
                      transparency='reject', blend_modes='reject', soft_masks='reject'))
    validate_intent(intent, pdf)
    data = json.dumps(intent, separators=(',', ':')).encode()
    assert len(data) > 2*1024*1024
    (directory/'intent.json').write_bytes(data)
    return pdf.read_bytes(), intent, data


def main():
    directory = Path(tempfile.mkdtemp(prefix='intent-size-', dir=ROOT/'build'))
    pdf, intent, data = fixture(directory)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, PLOTLINE_PDF_PROJECT=str(ROOT))
    env.pop('PLOTLINE_IPP_INBOX', None)
    log = (directory/'server.log').open('w')
    server = subprocess.Popen([str(ROOT/'controller/target/release/plotline-studios'),
                               '--listen', f'127.0.0.1:{port}', '--state', str(directory/'state')],
                              env=env, stdout=log, stderr=log, start_new_session=True)

    def request(path, body=None, mime='application/json', extra=None):
        headers = {'X-Plotline-Request': '1', 'Content-Type': mime, **(extra or {})}
        with urlopen(Request(base+path, body, headers), timeout=60) as response:
            return json.load(response)

    def upload(settings=data, key=None):
        boundary = 'fixture-'+uuid.uuid4().hex
        body = b''
        for name, value in [('document', pdf), ('intent', settings)]:
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="fixture"\r\n\r\n'.encode()+value+b'\r\n'
        body += f'--{boundary}--\r\n'.encode()
        return request('/api/v1/documents', body, 'multipart/form-data; boundary='+boundary,
                       {'Idempotency-Key': key} if key else None)

    def done(path):
        for _ in range(600):
            row = request(path)
            if row['status'] not in ('received', 'inspecting', 'queued', 'running'):
                assert row['status'] == 'ready' and not row.get('intent_error'), row
                return row
            time.sleep(.1)
        raise AssertionError('Worker timed out')

    def rejects(code, operation):
        try:
            operation()
        except HTTPError as error:
            assert error.code == code, (error.code, error.read())
        else:
            raise AssertionError(f'Expected HTTP {code}')

    try:
        for _ in range(100):
            try:
                request('/api/v1/capabilities')
                break
            except OSError:
                time.sleep(.05)
        key = str(uuid.uuid4())
        row = upload(key=key)
        assert row['upload_intent_sha256'] == hashlib.sha256(data).hexdigest()
        doc = done(row['status_url'])
        assert doc['intent']['bindings'] == intent['bindings']
        assert upload(key=key)['id'] == row['id']
        prefix = '/api/v1/documents/'+row['id']
        recipe_body = json.dumps(dict(name='Detailed map', intent=intent)).encode()
        recipe = request(prefix+'/recipes', recipe_body)
        saved = request(prefix+'/recipes/'+recipe['id'])
        assert saved['intent']['bindings'] == intent['bindings']
        conversion = request(prefix+'/conversions', data)
        converted = done(conversion['status_url'])
        assert converted['intent']['bindings'] == intent['bindings']
        assert set(converted['result']['pages'][0]['by_ink']) == {'fine-black', 'broad-black'}
        draft = request(prefix+'/draft', data)
        done(draft['status_url'])
        for _ in range(100):
            doc = request(row['status_url'])
            if doc.get('draft', {}).get('status') == 'ready':
                break
            time.sleep(.1)
        assert doc['draft']['requested_conversion_id'] == draft['id']
        assert len(doc['draft']['jobs']) == 1
        # Raising one cap must not leave recipe/draft endpoints at the old cap,
        # and must not accidentally make any request type unbounded.
        oversized = data+b' '*(32*1024*1024+1-len(data))
        rejects(422, lambda: upload(oversized))
        for endpoint in ('conversions', 'draft'):
            rejects(413, lambda: request(prefix+'/'+endpoint, oversized))
        oversized_recipe = recipe_body+b' '*(32*1024*1024+64*1024+1-len(recipe_body))
        rejects(413, lambda: request(prefix+'/recipes', oversized_recipe))
        result = dict(passed=True, bindings=len(intent['bindings']), intent_bytes=len(data),
                      checks=['upload + exact idempotent receipt', 'save/reopen recipe',
                              'conversion + both explicit pen bindings', 'ready HPGL draft',
                              'oversized uploads/recipes/conversions/drafts rejected'],
                      physical_plotting=False)
        (directory/'results.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(dict(result, directory=str(directory))))
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        server.wait(timeout=15)
        log.close()


if __name__ == '__main__':
    main()
