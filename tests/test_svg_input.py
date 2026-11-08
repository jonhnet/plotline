"""SVG intake preserves page placement, vectors, text and native raster pixels."""
import base64
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from svg_input import svg_pdf, validated_svg
from pdf_driver import DEFAULT_POLICIES, PolicyDevice
from pdf_native import extract


def mixed_svg():
    image = Image.new('RGB', (73, 41), '#285ac8')
    stream = io.BytesIO(); image.save(stream, format='JPEG')
    jpeg = stream.getvalue()
    return (f'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
 width="17in" height="11in" viewBox="0 0 1632 1056">
 <defs><clipPath id="clip"><rect x="96" y="240" width="288" height="162"/></clipPath></defs>
 <path d="M96 96 H1536" fill="none" stroke="black" stroke-width="1.2"/>
 <text x="96" y="180" font-family="DejaVu Sans" font-size="48">Hello SVG</text>
 <image x="96" y="240" width="288" height="162" clip-path="url(#clip)"
  xlink:href="data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"/>
</svg>'''.encode(), jpeg)


class SvgInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source, self.pdf = self.root/'original.svg', self.root/'source.pdf'

    def tearDown(self):
        self.temp.cleanup()

    def test_mixed_svg_keeps_page_text_strokes_and_native_image(self):
        source, jpeg = mixed_svg(); self.source.write_bytes(source)
        meta = svg_pdf(self.source, self.pdf)
        self.assertEqual(self.source.read_bytes(), source)
        self.assertTrue(meta['has_text']); self.assertEqual(meta['embedded_images'], 1)
        self.assertIn('server', meta['notes'][0])
        with pymupdf.open(self.pdf) as doc:
            self.assertEqual(len(doc), 1)
            page = doc[0]
            self.assertAlmostEqual(page.rect.width, 17*72, places=3)
            self.assertAlmostEqual(page.rect.height, 11*72, places=3)
            self.assertIn('Hello SVG', page.get_text())
            paths = page.get_drawings()
            line = next(p for p in paths if p['type']=='s')
            self.assertAlmostEqual(line['rect'].x0, 72, places=3)
            self.assertAlmostEqual(line['rect'].y0, 72, places=3)
            self.assertAlmostEqual(line['width'], .9, places=3)
            images = page.get_images(full=True)
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0][2:4], (73, 41))
            self.assertEqual(doc.xref_stream_raw(images[0][0]), jpeg)
        policies = copy.deepcopy(DEFAULT_POLICIES); policies['raster']='dither'
        device = PolicyDevice(policies, {}, 1)
        extract(self.pdf, device_factory=lambda:device)
        self.assertEqual(len(device.images), 1)
        with Image.open(io.BytesIO(next(iter(device.images.values())))) as image:
            self.assertEqual(image.size, (73, 41))
        first = self.pdf.read_bytes(); svg_pdf(self.source, self.pdf)
        self.assertEqual(self.pdf.read_bytes(), first)

    def test_svg_pixels_use_96_per_inch_and_nonzero_viewbox_keeps_placement(self):
        for dimensions in ('width="192" height="96"', ''):
            with self.subTest(dimensions=dimensions):
                self.source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {dimensions} viewBox="10 20 192 96"><path d="M34 44 H130" stroke="black"/></svg>')
                meta=svg_pdf(self.source, self.pdf)
                self.assertFalse(meta['has_text']); self.assertEqual(meta['notes'], [])
                with pymupdf.open(self.pdf) as doc:
                    self.assertEqual(tuple(doc[0].rect), (0, 0, 144, 72))
                    self.assertEqual(tuple(doc[0].get_drawings()[0]['rect']), (18, 18, 90, 18))

    def test_external_resources_and_active_content_fail_before_export(self):
        for body in (
            '<image href="file:///tmp/not-a-laptop-image.png"/>',
            '<image href="https://example.com/image.png"/>',
            '<use href="other.svg#shape"/>',
            '<rect fill="url(https://example.com/paint.svg#paint)"/>',
            '<rect style="fill:u\\72l(&quot;https://example.com/a&quot;)"/>',
            '<style>@\\69mport "https://example.com/a.css";</style>',
            '<style>@font-face {font-family:custom;src:local(Custom)}</style>',
            '<script>alert(1)</script>', '<foreignObject/>',
            '<g onload="alert(1)"/>',
            '<animate attributeName="href" to="file:///tmp/a"/>',
            '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>',
            '<g xml:base="file:///tmp/"><use href="#shape"/></g>',
        ):
            with self.subTest(body=body), patch('svg_input.subprocess.run') as run:
                self.source.write_text(f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>')
                with self.assertRaises(ValueError): svg_pdf(self.source, self.pdf)
                run.assert_not_called(); self.assertFalse(self.pdf.exists())

    def test_malformed_xml_and_stylesheet_processing_instructions_fail(self):
        for content in ('<html/>', '<svg', '<!DOCTYPE svg [<!ENTITY x "a">]><svg/>',
                        '<?xml-stylesheet href="remote.css"?><svg xmlns="http://www.w3.org/2000/svg"/>'):
            for encoding in ('utf-8','utf-16'):
                with self.subTest(content=content, encoding=encoding):
                    with self.assertRaises(ValueError): validated_svg(content.encode(encoding))

    def test_inkscape_multiple_pages_keep_sizes_and_local_placement(self):
        self.source.write_text('''<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
 xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
 width="192" height="96" viewBox="0 0 192 96">
 <sodipodi:namedview><inkscape:page x="0" y="0" width="192" height="96"/>
 <inkscape:page x="240" y="0" width="96" height="192"/></sodipodi:namedview>
 <path d="M10 10h50 M250 10h50" fill="none" stroke="black"/></svg>''')
        svg_pdf(self.source, self.pdf)
        with pymupdf.open(self.pdf) as doc:
            self.assertEqual([tuple(p.rect) for p in doc],[(0,0,144,72),(0,0,72,144)])
            for page in doc:
                pix=page.get_pixmap()
                self.assertLess(pix.pixel(20,7)[0],255)

    def test_embedded_png_keeps_full_pixel_grid(self):
        stream=io.BytesIO(); Image.new('RGB',(139,77),'#cd8040').save(stream,format='PNG')
        content=f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><image width="96" height="96" href="data:image/png;base64,{base64.b64encode(stream.getvalue()).decode()}"/></svg>'
        self.source.write_text(content); svg_pdf(self.source,self.pdf)
        with pymupdf.open(self.pdf) as doc:
            self.assertEqual(doc[0].get_images(full=True)[0][2:4],(139,77))

    def test_absref_and_hyperlinks_are_excluded_from_import_copy_only(self):
        source,_=mixed_svg()
        source=source.replace(b'<image ',b'<image xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" sodipodi:absref="/tmp/laptop.jpg" ')
        source=source.replace(b'</svg>',b'<a href="https://example.com"><path d="M0 0H1"/></a></svg>')
        checked,_,_=validated_svg(source)
        self.assertNotIn(b'/tmp/laptop.jpg',checked)
        self.assertNotIn(b'https://example.com',checked)


if __name__ == '__main__':
    unittest.main()
