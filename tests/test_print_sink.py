"""Real CUPS capability → client PPD/filter → IPP capture regression checks.

Build tools/print_sink/build_server.py first. No system queues or hardware are used.
"""
import json
import http.client
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT/'build/print-sink/ippeveprinter'
FILTER = Path('/usr/lib/cups/filter/pdftopdf')
sys.path.insert(0, str(ROOT/'tools'))
from print_sink.capabilities import CUSTOM_MIN, CUSTOM_MAX


@unittest.skipUnless(BINARY.exists() and FILTER.exists() and shutil.which('driverless'),
                     'Build the IPP tool and install cups-filters before this integration test')
class PrintSinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='plotline-ipp-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            cls.port = available.getsockname()[1]
        cls.uri = f'ipp://127.0.0.1:{cls.port}/ipp/print'
        cls.log = (cls.directory/'server.log').open('w')
        cls.addClassCleanup(cls.log.close)
        cls.server = subprocess.Popen([str(ROOT/'tools/print_sink/run.sh')], cwd=ROOT,
                                     env={**os.environ, 'PLOTLINE_SINK_PORT': str(cls.port),
                                          'PLOTLINE_SINK_STATE': str(cls.directory/'state'), 'PLOTLINE_SINK_DISCOVERY':'off',
                                          'PLOTLINE_SINK_HOST_ALIAS':'dev.iac.jonh.net',
                                          'PLOTLINE_REVIEW_URL':'http://127.0.0.1:9191/'},
                                     stdout=cls.log, stderr=subprocess.STDOUT)
        cls.addClassCleanup(cls.stop_server)
        for _ in range(100):
            if cls.server.poll() is not None:
                raise RuntimeError((cls.directory/'server.log').read_text())
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise TimeoutError('IPP test server did not listen')
        cls.ppd = cls.directory/'client.ppd'
        result = subprocess.run(['driverless', cls.uri], capture_output=True, check=True, timeout=20)
        cls.ppd.write_bytes(result.stdout)

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait()

    def ipp(self, body, pdf=None):
        test = self.directory/'request.test'
        test.write_text('''{
VERSION 2.0
GROUP operation
ATTR charset attributes-charset utf-8
ATTR language attributes-natural-language en
ATTR uri printer-uri $uri
''' + body + '\n}\n')
        result = subprocess.run(['ipptool', '-tv', *(['-f', str(pdf)] if pdf else []),
                                 self.uri, str(test)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result.stdout

    def test_capabilities_are_pdf_only_with_custom_range_and_no_scaling(self):
        result = subprocess.run(['ipptool', '-tv', self.uri,
                                 '/usr/share/cups/ipptool/get-printer-attributes.test'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('document-format-supported (1setOf mimeMediaType) = application/octet-stream,application/pdf', result.stdout)
        self.assertIn('x-dimension=100-508000 y-dimension=100-508000', result.stdout)
        self.assertIn('printer-more-info (uri) = http://127.0.0.1:9191/', result.stdout)
        self.assertIn('media-top-margin=3900', result.stdout)
        self.assertIn('media-left-margin=1500', result.stdout)
        self.assertIn('print-scaling-default (keyword) = none', result.stdout)
        self.assertIn('na_arch-e_36x48in', result.stdout)
        self.assertIn('na_ledger_11x17in', result.stdout)
        ppd = self.ppd.read_text()
        self.assertRegex(ppd, r'\*ParamCustomPageSize Width: 1 points 2\.83[0-9]+ 14400')
        self.assertRegex(ppd, r'\*ParamCustomPageSize Height: 2 points 2\.83[0-9]+ 14400')
        self.assertIn('*HWMargins: "0 0 0 0"', ppd)
        filters = [line for line in ppd.splitlines() if line.startswith('*cupsFilter')]
        self.assertEqual(filters, ['*cupsFilter2: "application/vnd.cups-pdf application/pdf 0 -"'])

    def test_http_accepts_configured_dns_alias_and_rejects_unknown_hosts(self):
        for host, expected in [('127.0.0.1', 200), ('dev.iac.jonh.net', 200),
                               ('DEV.IAC.JONH.NET', 200), ('dev.local', 200),
                               ('unconfigured.example', 400)]:
            with self.subTest(host=host):
                connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
                try:
                    connection.request('GET', '/', headers={'Host': f'{host}:{self.port}'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    if expected == 200:
                        self.assertIn(b'Plotline Studios HP7585', response.read())
                finally:
                    connection.close()

    def filter_fixture(self, name, inches, options):
        source, filtered = self.directory/f'{name}-source.pdf', self.directory/f'{name}-filtered.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=inches[0]*72, height=inches[1]*72)
            for x, y in [(72, 144), (page.rect.width-172, page.rect.height-144)]:
                page.draw_line((x, y), (x+100, y), color=(1, 0, 0), width=1)
                page.insert_text((x, y-20), '100 pt ruler')
            doc.save(source)
        with filtered.open('wb') as output:
            result = subprocess.run([str(FILTER), '1', 'test', name, '1', options, str(source)],
                                    env={**os.environ, 'PPD': str(self.ppd),
                                         'CONTENT_TYPE': 'application/pdf', 'FINAL_CONTENT_TYPE': 'application/pdf'},
                                    stdout=output, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn('falling back', result.stderr.decode())
        with pymupdf.open(source) as original, pymupdf.open(filtered) as actual:
            expected, received = original[0], actual[0]
            # CUPS expresses a landscape job as /Rotate 270 on its submitted
            # page. Check that explicit rotation as well as the unrotated marks.
            rotation = 270 if 'orientation-requested=4' in options else 0
            self.assertEqual(received.rotation, rotation)
            dimensions = (expected.rect.height, expected.rect.width) if rotation else (expected.rect.width, expected.rect.height)
            self.assertAlmostEqual(received.rect.width, dimensions[0], delta=.1)
            self.assertAlmostEqual(received.rect.height, dimensions[1], delta=.1)
            self.assertEqual(received.get_text(), expected.get_text())
            self.assertFalse(any(kind == 'fill-image' for kind, *_ in received.get_bboxlog()))
            paths = received.get_drawings()
            self.assertEqual(len(paths), 2)
            for before, after in zip(expected.get_drawings(), paths):
                self.assertAlmostEqual(after['width'], 1, delta=.0001)
                self.assertEqual(after['color'], before['color'])
                for a, b in zip(before['rect'], after['rect']):
                    self.assertAlmostEqual(a, b, delta=.1)
        return filtered

    def test_client_filter_preserves_named_and_custom_paper(self):
        for name, inches, options in [
            ('letter', (8.5, 11), 'PageSize=Letter print-scaling=none'),
            ('tabloid-landscape', (17, 11), 'PageSize=Tabloid orientation-requested=4 print-scaling=none'),
            ('arch-e', (36, 48), 'PageSize=ARCHE print-scaling=none'),
            ('custom-default', (36, 63.16), 'PageSize=Custom.36x63.16in'),
            ('custom-none', (36, 63.16), 'PageSize=Custom.36x63.16in print-scaling=none'),
            ('custom-collection', (36, 63.16), 'media-col={media-size={x-dimension=91440 y-dimension=160428}} print-scaling=none'),
        ]:
            with self.subTest(name=name):
                self.filter_fixture(name, inches, options)

    def test_filtered_custom_pdf_and_ticket_arrive_intact(self):
        pdf = self.filter_fixture('transport', (36, 63.16), 'PageSize=Custom.36x63.16in print-scaling=none')
        self.ipp('''OPERATION Print-Job
ATTR mimeMediaType document-format application/pdf
ATTR boolean ipp-attribute-fidelity true
ATTR name job-name "Large vector regression"
GROUP job
ATTR keyword print-scaling none
ATTR collection media-col {
 MEMBER collection media-size { MEMBER integer x-dimension 91440 MEMBER integer y-dimension 160428 }
 MEMBER integer media-left-margin 0 MEMBER integer media-right-margin 0
 MEMBER integer media-bottom-margin 0 MEMBER integer media-top-margin 0
}
FILE $filename
STATUS successful-ok''', pdf)
        for _ in range(100):
            captures = list((self.directory/'state/captures').glob('*/capture.json'))
            if captures:
                break
            time.sleep(.05)
        self.assertEqual(len(captures), 1)
        capture = captures[0]
        self.assertEqual(capture.with_name('source.pdf').read_bytes(), pdf.read_bytes())
        report = json.loads(capture.read_text())
        self.assertEqual(report['inspection']['pages'][0]['vector_paints'], 2)
        self.assertEqual(report['inspection']['pages'][0]['image_placements'], 0)
        self.assertIn('x-dimension=91440 y-dimension=160428', report['ipp']['IPP_MEDIA_COL'])
        self.assertEqual(report['ipp']['IPP_PRINT_SCALING'], 'none')

    def test_media_fidelity_rejects_out_of_range_on_either_axis(self):
        for x, y in [(CUSTOM_MAX+1000, 30000), (30000, CUSTOM_MAX+1000), (CUSTOM_MIN-1, 30000), (30000, CUSTOM_MIN-1)]:
            with self.subTest(x=x, y=y):
                self.ipp(f'''OPERATION Validate-Job
ATTR mimeMediaType document-format application/pdf
ATTR boolean ipp-attribute-fidelity true
GROUP job
ATTR collection media-col {{
 MEMBER collection media-size {{ MEMBER integer x-dimension {x} MEMBER integer y-dimension {y} }}
}}
STATUS client-error-attributes-or-values-not-supported''')

    def test_raster_transport_is_not_negotiated(self):
        self.ipp('''OPERATION Validate-Job
ATTR mimeMediaType document-format image/pwg-raster
STATUS client-error-attributes-or-values-not-supported
EXPECT document-format IN-GROUP unsupported-attributes-tag''')


if __name__ == '__main__':
    unittest.main()
