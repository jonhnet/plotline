import hashlib
from http.server import ThreadingHTTPServer
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import cv2
import numpy as np
from PIL import Image

from raster_lab import engine as e
from raster_lab.build import build
from raster_lab.server import handler


class RasterGeometryTests(unittest.TestCase):
    def test_open_source_dependency_guard(self):
        e.check_dependencies()
        for info in ['Intel IPP: 2026.0.0', 'Non-free algorithms: YES']:
            with patch.object(cv2, 'getBuildInformation', return_value=info):
                with self.assertRaisesRegex(RuntimeError, 'open-source-only'):
                    e.check_dependencies()

    def test_staircase_is_one_run_without_diagonal_shortcut_cycles(self):
        mask = np.zeros((10, 10), bool)
        for x, y in [(2,2),(3,2),(3,3),(4,3),(4,4),(5,4),(5,5),(6,5)]:
            mask[y,x] = True
        paths = e.skeleton_paths(mask)
        self.assertEqual(len(paths), 1)
        self.assertNotEqual(paths[0][0], paths[0][-1])

    def test_loop_is_traced_once_and_closed(self):
        mask = np.zeros((20,20), np.uint8)
        cv2.rectangle(mask, (4,4), (15,15), 1, 1)
        paths = e.skeleton_paths(mask)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1])
        edges = [tuple(sorted((a,b))) for a,b in zip(paths[0],paths[0][1:])]
        self.assertEqual(len(edges), len(set(edges)))

    def test_junction_arms_not_retraced(self):
        mask = np.zeros((20,20), np.uint8)
        mask[3:17,10] = 1
        mask[10,3:17] = 1
        paths=e.skeleton_paths(mask)
        edges=[tuple(sorted((a,b))) for path in paths for a,b in zip(path,path[1:])]
        self.assertEqual(len(edges), len(set(edges)))
        self.assertEqual(len(paths),4)

    def test_clip_preserves_finite_width_and_splits_excursions(self):
        drawing=e.Drawing(10,10,.4,{})
        paths=e.finish_paths([[[-2,5],[4,5],[12,5],[12,7],[4,7],[-2,7]]],drawing,.1)
        self.assertEqual(len(paths),2)
        for path in paths:
            for x,y in path:
                self.assertGreaterEqual(x,.2)
                self.assertGreaterEqual(y,.2)
                self.assertLessEqual(x,9.8)
                self.assertLessEqual(y,9.8)

    def test_blank_source_uses_no_pen_in_any_method(self):
        image=Image.new('RGB',(32,48),'white')
        recipes=json.loads(Path('raster_lab/experiments.json').read_text())
        for recipe in recipes:
            drawing=e.draw(image,recipe)
            self.assertEqual(e.metrics(drawing)['paths'],0,recipe['id'])

    def test_real_aspect_ratio_survives_analysis_resize(self):
        drawing=e.draw(Image.new('RGB',(827,1152),'white'),dict(method='edges',sigma=1,
            low=50,high=120,long_edge_in=8,pen_mm=.3,min_length_mm=.35))
        self.assertAlmostEqual(drawing.width_mm/drawing.height_mm,827/1152)
        self.assertAlmostEqual(drawing.height_mm,203.2)

    def test_hatch_density_adds_ink_and_keeps_long_runs(self):
        drawing=e.Drawing(20,20,.3,{})
        channel=np.ones((32,32),np.float32)*.7
        a=e.hatch_paths(channel,drawing,45,.35,.35)
        b=e.hatch_paths(channel,drawing,45,.7,.35)
        self.assertGreater(sum(map(e.path_length,b)),sum(map(e.path_length,a)))
        self.assertTrue(all(len(p)==2 for p in b))
        self.assertGreater(max(map(e.path_length,b)),20)

    def test_preview_agrees_with_finite_pen_width_and_area(self):
        drawing=e.Drawing(10,10,.3,{'K':[[[2,5],[8,5]]]})
        mask=e.stroke_mask(drawing,'K',59,59,3).astype(float)/255
        self.assertAlmostEqual(mask[:,30].sum()/5.9,.3,delta=.008)
        self.assertAlmostEqual(mask.sum()/5.9**2,6*.3+math.pi*.15**2,delta=.025)

    def test_color_overlaps_survive_and_metrics_count_paths(self):
        drawing=e.Drawing(10,10,.4,{'C':[[[1,5],[9,5]]],'M':[[[5,1],[5,9]]]})
        image,_=e.render(drawing)
        rgb=np.asarray(image)
        crossing=rgb[image.height//2,image.width//2]
        self.assertLess(int(crossing[0]),50)
        self.assertLess(int(crossing[1]),60)
        self.assertEqual(e.metrics(drawing)['meters'],.016)
        self.assertEqual(e.metrics(drawing)['paths'],2)
        self.assertEqual(e.svg(drawing,'A & B').count('<path '),2)
        self.assertIn('A &amp; B',e.svg(drawing,'A & B'))


class RasterArtifactTests(unittest.TestCase):
    def test_source_integrity_cache_and_private_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';out=root/'out';(state/'inputs').mkdir(parents=True)
            source=state/'inputs'/'test.jpg';Image.new('RGB',(24,32),'white').save(source)
            sha=hashlib.sha256(source.read_bytes()).hexdigest()
            (state/'sources.json').write_text(json.dumps({'images':[dict(id='test',title='Test',artist='Tester',
                file='test.jpg',sha256=sha,pixels=[24,32],source_url='https://example.test/private-token')]}))
            recipe=root/'recipes.json';recipe.write_text(json.dumps([dict(id='edges',name='Edges',method='edges',
                low=50,high=120,sigma=1,long_edge_in=1,pen_mm=.3,min_length_mm=.35)]))
            first=build(state,out,recipe)
            cell=first['cells']['test']['edges'];artifact=out/cell['artifact']/'paths.svg'
            original_stat=artifact.stat().st_mtime_ns
            second=build(state,out,recipe)
            self.assertEqual(first['cells'],second['cells'])
            self.assertEqual(original_stat,artifact.stat().st_mtime_ns)
            self.assertNotIn('private-token',(out/'portfolio.json').read_text())
            preserved = (out/'portfolio.json').read_bytes()
            focused = build(state,out,recipe,source_manifest=state/'sources.json',index='palette-portfolio.json')
            self.assertEqual(focused['cells'],first['cells'])
            self.assertEqual((out/'portfolio.json').read_bytes(),preserved)
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                build(state,out,recipe)

    def test_read_only_server_does_not_serve_private_files_or_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';state=root/'state';out.mkdir();state.mkdir()
            (state/'sources.json').write_text('PRIVATE URL')
            (out/'portfolio.json').write_text('{}')
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(out,state))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url=f'http://127.0.0.1:{server.server_port}'
                self.assertEqual(urlopen(url+'/artifacts/portfolio.json').read(),b'{}')
                for path in ['/sources.json','/originals/..%2fsources.json','/artifacts/../state/sources.json']:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(url+path)
                    self.assertEqual(error.exception.code,404)
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    unittest.main()
