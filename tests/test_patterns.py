"""Exact role/width/page handoff and rejection of unbacked or unsafe strokes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf
from pattern_pdf import export, check_geometry_size, LIMITS
from print_contract import validate_intent


class PatternExport(unittest.TestCase):
    def scene(self):
        media = json.loads((Path(__file__).resolve().parents[1]/'contracts/v1/examples/hp7585-tabloid-landscape.json').read_text())
        return dict(hash='a'*64, document_id='pattern-test', recipe=dict(
            version=1, name='Two black widths', family='koch', params={}, media=media,
            inks=[dict(id='fine', label='Fine black', color_srgb='#000000', width_mm=.25),
                  dict(id='bold', label='Broad black', color_srgb='#000000', width_mm=.7)],
            roles=[dict(label='Outer folds', ink_id='fine'), dict(label='Inner folds', ink_id='bold')]),
            paths=[dict(role=0, points=[[50, 30], [90, 30], [90, 50]]),
                   dict(role=1, points=[[60, 70], [100, 70]])])

    def test_native_strokes_bind_same_color_at_different_widths_and_preserve_page(self):
        with tempfile.TemporaryDirectory() as directory:
            scene = self.scene();export(scene, directory)
            root = Path(directory);intent = json.loads((root/'intent.json').read_text())
            validate_intent(intent, root/'drawing.pdf')
            self.assertEqual(intent['authoring']['application'], 'pattern-studio')
            self.assertEqual([b['ink_id'] for b in intent['bindings']], ['fine', 'bold'])
            self.assertEqual(intent['pages'][0]['placement'], dict(scale=1, rotation_deg=0, offset_mm=[0, 0]))
            self.assertEqual(intent['pages'][0]['target_media'], scene['recipe']['media'])
            with pymupdf.open(root/'drawing.pdf') as pdf:
                page = pdf[0];self.assertAlmostEqual(page.rect.width*25.4/72,431.8,places=4)
                strokes = page.get_drawings()
                self.assertEqual(len(strokes), 2)
                for stroke, expected in zip(strokes, [.25,.7]):
                    self.assertEqual(stroke['type'], 's')
                    self.assertAlmostEqual(stroke['width']*25.4/72, expected, places=5)
                self.assertAlmostEqual(strokes[0]['items'][0][1].x*25.4/72, 50, places=4)

    def test_shared_memory_guard_and_large_pdf_keep_every_segment(self):
        check_geometry_size(178316, 356632)
        check_geometry_size(1, 800000)
        with self.assertRaisesRegex(ValueError, 'MiB of working memory'):
            check_geometry_size(1000000, 2000000)
        scene = self.scene()
        # More paths AND points than the former limits. A long path verifies
        # that PDF chunking retains shared endpoints and every line segment.
        path = dict(role=0, points=[[50+i*.01, 30+(i%2)] for i in range(5000)])
        scene['paths'] = [dict(role=1, points=[[60,70],[61,70]]) for _ in range(50001)] + [path]*61
        expected = sum(len(p['points'])-1 for p in scene['paths'])
        self.assertGreater(sum(len(p['points']) for p in scene['paths']), 400000)
        with tempfile.TemporaryDirectory() as directory:
            export(scene, directory)
            with pymupdf.open(Path(directory)/'drawing.pdf') as pdf:
                drawings = pdf[0].get_drawings()
                self.assertEqual(sum(len(d['items']) for d in drawings), expected)
                self.assertTrue(all(d['type']=='s' and d['lineJoin']==1 for d in drawings))
                self.assertEqual(set(round(d['width']*25.4/72,2) for d in drawings), {.25,.7})

    def test_every_stroke_has_a_role_and_a_finite_pen_footprint_inside_margins(self):
        invalid=[]
        scene=self.scene();scene['paths'][0]['role']=2;invalid.append(scene)
        scene=self.scene();scene['recipe']['roles'][0]['ink_id']='absent';invalid.append(scene)
        scene=self.scene();scene['recipe']['inks'][0]['width_mm']=None;invalid.append(scene)
        scene=self.scene();scene['paths'][0]['points'][0]=[39.5,20];invalid.append(scene)
        scene=self.scene();scene['paths'][0]['points'][0]=[float('nan'),20];invalid.append(scene)
        for scene in invalid:
            with self.subTest(scene=scene), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises((AssertionError, ValueError)):
                    export(copy.deepcopy(scene), directory)
                self.assertFalse((Path(directory)/'drawing.pdf').exists())


if __name__ == '__main__':
    unittest.main()
