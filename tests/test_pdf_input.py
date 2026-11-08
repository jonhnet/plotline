"""Native image intake and preview resolution are independent of paper size."""
import base64
import copy
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageOps
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from pdf_input import image_pdf
from pdf_preview import render_page, filename
from pdf_driver import inspect, review_svgs, PolicyDevice, DEFAULT_POLICIES
from pdf_native import extract


class ImageInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def embedded(self, path):
        with pymupdf.open(path) as doc:
            images=doc[0].get_images(full=True);self.assertEqual(len(images),1)
            xref=images[0][0];pix=pymupdf.Pixmap(doc,xref)
            self.assertEqual(images[0][1],0,'Intake must produce opaque image paint')
            return Image.frombytes('RGB' if pix.n==3 else 'L',(pix.width,pix.height),pix.samples), doc.xref_stream_raw(xref)

    def test_jpeg_preserves_compressed_bytes_and_full_resolution_for_pen_conversion(self):
        src=self.root/'source.jpg';pdf=self.root/'source.pdf'
        Image.new('RGB',(1024,663),'#123456').save(src,dpi=(300,300))
        before=src.read_bytes();meta=image_pdf(src,pdf)
        image,compressed=self.embedded(pdf)
        self.assertEqual(compressed,before);self.assertEqual(image.size,(1024,663))
        self.assertEqual(meta['resolution_ppi'],[300,300]);self.assertEqual(src.read_bytes(),before)
        first=pdf.read_bytes();image_pdf(src,pdf);self.assertEqual(first,pdf.read_bytes())
        policies=copy.deepcopy(DEFAULT_POLICIES);policies['raster']='dither'
        device=PolicyDevice(policies,{},1)
        extract(pdf,device_factory=lambda:device)
        self.assertEqual(len(device.images),1)
        with Image.open(io.BytesIO(next(iter(device.images.values())))) as pixels:
            self.assertEqual(pixels.size,(1024,663))
        result=inspect(pdf,self.root)
        self.assertAlmostEqual(result['pages'][0]['source_size_mm'][0],1024/300*25.4,places=4)
        with Image.open(self.root/filename(0)) as preview:self.assertEqual(preview.width,1600)

    def test_png_pixels_alpha_and_default_resolution(self):
        for mode in ('RGB','RGBA','P'):
            with self.subTest(mode=mode):
                source=Image.new('RGBA',(39,27),(255,0,0,128))
                source.putpixel((0,0),(0,100,255,0))
                if mode=='RGB':source=source.convert('RGB')
                if mode=='P':source=source.convert('P')
                src=self.root/'source.png';pdf=self.root/'source.pdf';source.save(src)
                original=src.read_bytes();meta=image_pdf(src,pdf)
                with Image.open(src) as raw:
                    expected=Image.alpha_composite(Image.new('RGBA',raw.size,'white'),raw.convert('RGBA')).convert('RGB')
                actual,_=self.embedded(pdf)
                np.testing.assert_array_equal(np.asarray(actual),np.asarray(expected))
                self.assertEqual(meta['resolution_ppi'],[300,300]);self.assertEqual(src.read_bytes(),original)

    def test_exif_orientation_preserves_native_grid_and_swaps_density_axes(self):
        source=Image.new('RGB',(30,20),'white');source.paste('red',(0,0,11,7))
        for orientation in range(1,9):
            with self.subTest(orientation=orientation):
                exif=Image.Exif();exif[274]=orientation
                src=self.root/'oriented.jpg';pdf=self.root/'source.pdf'
                source.save(src,exif=exif,dpi=(150,300))
                with Image.open(src) as raw:expected=ImageOps.exif_transpose(raw).convert('RGB')
                meta=image_pdf(src,pdf);actual,_=self.embedded(pdf)
                if orientation==1:
                    # The untouched JPEG uses MuPDF's chroma upsampling; compare
                    # with that same decoder instead of Pillow's different one.
                    pix=pymupdf.Pixmap(src)
                    expected=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                np.testing.assert_allclose(np.asarray(actual),np.asarray(expected),atol=1)
                self.assertEqual(meta['pixels'],list(expected.size))
                self.assertEqual(meta['resolution_ppi'],[300,150] if orientation>=5 else [150,300])

    def test_preview_budget_is_independent_of_page_size_and_applies_to_source_comparison(self):
        for size in ((245.76,159.12),(2457.6,1591.2)):
            with pymupdf.open() as doc:
                page=doc.new_page(width=size[0],height=size[1]);pix=render_page(page)
                self.assertEqual(pix.width,1600);self.assertLessEqual(pix.height,1600)
                pdf=self.root/'preview.pdf';doc.save(pdf)
            for name in ('coverage.svg','hpgl.svg'):(self.root/name).write_text('<svg viewBox="0 0 1 1"></svg>')
            selection=dict(page_index=0,source_size_mm=[v*25.4/72 for v in size],
                           placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]),
                           target_media=dict(sheet_size_mm=[431.8,279.4],printable_rect_mm=[39,11,377.8,253.6]))
            review_svgs(self.root,pdf,selection)
            encoded=re.search('base64,([^\"]+)',(self.root/'source.svg').read_text()).group(1)
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:self.assertEqual(image.width,2200)

    def test_oriented_cmyk_jpeg_imports_at_native_dimensions(self):
        src=self.root/'cmyk.jpg';pdf=self.root/'source.pdf'
        exif=Image.Exif();exif[274]=6
        Image.new('CMYK',(30,20),(10,60,25,0)).save(src,exif=exif)
        image_pdf(src,pdf);actual,_=self.embedded(pdf)
        with Image.open(src) as original:expected=ImageOps.exif_transpose(original).convert('RGB')
        self.assertEqual(actual.size,(20,30))
        np.testing.assert_array_equal(np.asarray(actual),np.asarray(expected))

    def test_unsupported_and_animated_images_fail_without_creating_pdf(self):
        src=self.root/'source.gif';pdf=self.root/'source.pdf'
        Image.new('RGB',(4,4),'white').save(src)
        with self.assertRaisesRegex(ValueError,'PDF, JPEG or PNG'):image_pdf(src,pdf)
        src=self.root/'animated.png'
        Image.new('RGB',(4,4),'white').save(src,save_all=True,append_images=[Image.new('RGB',(4,4),'black')])
        with self.assertRaisesRegex(ValueError,'single frame'):image_pdf(src,pdf)
        self.assertFalse(pdf.exists())
