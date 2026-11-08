import copy
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'tools'))
from raster_compare import measure, render_comparison

ET.register_namespace('', 'http://www.w3.org/2000/svg')
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

class RasterComparisonTests(unittest.TestCase):
    def test_thresholds_and_dimensions_are_not_relaxed(self):
        a = Image.new('RGB', (3, 1), 'white')
        b = a.copy()
        b.putdata([(191, 255, 255), (190, 255, 255), (255, 255, 255)])
        self.assertEqual(measure(a, b), dict(differing_pixels=1, ink_pixels=2, ratio=.5))
        with self.assertRaises(AssertionError):
            measure(a, Image.new('RGB', (2, 1)))

    def test_supersampling_keeps_missing_displaced_and_recolored_features_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            pdf = directory/'fixture.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page(width=160, height=120)
                page.insert_text((12.17, 24.5), 'PIXEL', fontsize=18)
                page.draw_line((0, 50), (160, 50), color=(0, 0, 0), width=8)
                page.draw_line((0, 50), (160, 50), color=(1, 1, 1), width=4)
                page.draw_rect((30.1, 70.2, 80.4, 100.3), color=None, fill=(1, 0, 0))
                refs = page.get_contents()
                data = b'q 10.17 10.23 135.41 100.19 re W n\n'
                data += b'\n'.join(doc.xref_stream(x) for x in refs)+b'\nQ'
                doc.update_stream(refs[0], data)
                page.set_contents(refs[0])
                doc.save(pdf)
                original = ET.fromstring(page.get_svg_image(text_as_path=True))
                # Our logical SVG contains already-clipped geometry. Bake this
                # simple analytic intersection too; mutool's SVG importer does
                # not implement the exported clipPath semantics faithfully.
                for element in original.iter():
                    if element.get('stroke'):
                        element.set('d', 'M10.17 70H145.58')
            for mutation in ['none', 'shift', 'missing_text', 'missing_fill', 'missing_white_core', 'wrong_color']:
                with self.subTest(mutation=mutation):
                    svg = copy.deepcopy(original)
                    changed = 0
                    if mutation == 'shift':
                        group = ET.Element('{http://www.w3.org/2000/svg}g', transform='translate(.5 0)')
                        for child in list(svg):
                            svg.remove(child)
                            group.append(child)
                        svg.append(group)
                        changed = 1
                    for parent in svg.iter():
                        for child in list(parent):
                            color = child.get('stroke') or child.get('fill')
                            remove = (mutation == 'missing_text' and child.tag.endswith('}use')
                                      or mutation == 'missing_fill' and color == '#ff0000'
                                      or mutation == 'missing_white_core' and color in ('#ffffff', '#fff'))
                            if remove:
                                parent.remove(child)
                                changed += 1
                            elif mutation == 'wrong_color' and color == '#ff0000':
                                child.set('fill', '#00ff00')
                                changed += 1
                    self.assertTrue(mutation == 'none' or changed)
                    ET.ElementTree(svg).write(directory/'coverage.svg', encoding='unicode')
                    result = render_comparison(pdf, directory)
                    if mutation == 'none':
                        self.assertGreater(result['native']['ratio'], .01)
                        self.assertLess(result['sampled']['ratio'], .01)
                    else:
                        self.assertGreater(result['sampled']['ratio'], .01, mutation)


if __name__ == '__main__':
    unittest.main()
