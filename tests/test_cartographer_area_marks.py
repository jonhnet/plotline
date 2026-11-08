"""Shore-following textures, crop semantics, and explicit PDF/ink handoff."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf
import shapely

from cartographer.area_marks import concentric_rings
from cartographer.compose import compose
from cartographer.model import default_recipe, validate_recipe


class ConcentricTests(unittest.TestCase):
    def assert_offsets(self, geometry, spacing, rings, boundary=None):
        boundary = geometry.boundary if boundary is None else boundary
        for level, contours in rings:
            self.assertLess(contours.difference(geometry.buffer(1e-7)).length, 1e-6)
            for part in shapely.get_parts(contours):
                for fraction in np.linspace(0, 1, 31):
                    p = part.interpolate(fraction, normalized=True)
                    self.assertAlmostEqual(boundary.distance(p), level*spacing, delta=level*spacing*.005+.003)

    def test_islands_split_lobes_and_finite_inward_spacing(self):
        lake = shapely.union_all([shapely.box(0, 0, 80, 80), shapely.box(78, 35, 110, 45), shapely.box(108, 0, 170, 80)])
        lake = lake.difference(shapely.box(25, 25, 40, 40))
        rings = list(concentric_rings(lake, 5))
        self.assertGreater(len(rings), 4)
        self.assertGreater(len(shapely.get_parts(rings[0][1])), 2, 'Narrow neck splits; island remains empty')
        self.assert_offsets(lake, 5, rings)
        for _, ring in rings:
            self.assertLess(ring.buffer(.2).difference(lake).area, 1e-6)
        self.assertEqual(list(concentric_rings(shapely.box(0, 0, 100, 1), 2)), [])

    def test_cropped_lake_follows_only_real_shore_and_preserves_near_edge_contours(self):
        domain = shapely.box(0, 0, 100, 100)
        lake = shapely.box(-100, -100, 70, 200).intersection(domain)
        rings = list(concentric_rings(lake, 10, domain))
        self.assertEqual(len(rings), 7)
        for level, ring in rings:
            self.assertLess(ring.hausdorff_distance(shapely.LineString([(70-level*10, 0), (70-level*10, 100)])), .003)
        self.assertEqual(list(concentric_rings(domain, 10, domain)), [])
        # A partial island expands into open water. There is no fake coastline
        # at the fetch edge and no rectangle-shaped ring at that edge.
        island = shapely.Point(50, 0).buffer(12, quad_segs=32)
        water = domain.difference(island)
        rings = list(concentric_rings(water, 5, domain))
        self.assertGreater(len(rings), 10)
        self.assert_offsets(water, 5, rings, island.boundary.intersection(domain))

    def test_viewport_preserves_original_shore_offsets_and_disconnected_runs(self):
        domain = shapely.box(-200, -200, 800, 300)
        land = shapely.union_all([shapely.box(-200, -200, 0, 300),
                                  shapely.Point(40, 70).buffer(12)])
        water = domain.difference(land)
        viewport = shapely.union_all([shapely.box(15, -20, 45, 40),
                                      shapely.box(180, 90, 210, 130)])
        coast = water.boundary.difference(domain.boundary.buffer(.002))
        rings = dict(concentric_rings(water, 5, domain, viewport=viewport))
        expected = {}
        # Independent original full-shore construction. Shore outside the
        # viewport must still shape its rings; empty bands between disjoint
        # viewport pieces must not end the search.
        for index in range(1, 61):
            ring = coast.buffer(index*5, quad_segs=8).boundary.intersection(viewport).intersection(water)
            if not ring.is_empty: expected[index] = ring
        self.assertEqual(rings.keys(), expected.keys())
        self.assertGreater(max(rings), 20)
        self.assertTrue(any(i not in rings for i in range(1, max(rings))))
        for index, ring in rings.items():
            self.assertLess(ring.hausdorff_distance(expected[index]), 1e-7)
        self.assert_offsets(water, 5, rings.items(), coast)
        # Complete polygons use negative offsets but obey the same viewport.
        lake = shapely.box(-100, -100, 100, 100)
        viewport = shapely.box(80, -20, 95, 20)
        actual = dict(concentric_rings(lake, 5, viewport=viewport))
        self.assertEqual(set(actual), {1, 2, 3, 4})
        for index, ring in actual.items():
            self.assertTrue(ring.equals(lake.buffer(-index*5, quad_segs=8).boundary.intersection(viewport)))

    def test_off_page_water_cannot_multiply_outline_work(self):
        counts = []
        for extent in [100, 10_000_000]:
            domain = shapely.box(0, 0, extent, 1000)
            water = shapely.box(0, 0, extent-10, 1000)
            viewport = shapely.box(extent-50, 100, extent-20, 200)
            updates = []
            rings = list(concentric_rings(water, 10, domain, lambda *a:updates.append(a), viewport=viewport))
            self.assertEqual([i for i, _ in rings], [1, 2, 3, 4])
            for index, ring in rings:
                x = extent-10-index*10
                self.assertLess(ring.hausdorff_distance(shapely.LineString([(x, 100), (x, 200)])), 1e-7)
            self.assertLess(len(updates), 10, 'Off-page rings must never be constructed')
            self.assertEqual(updates[-1][1], updates[-1][2])
            self.assertTrue(all(0 <= completed <= total for _, completed, total, _ in updates))
            counts.append(len(updates))
        self.assertEqual(*counts)
        self.assertEqual(list(concentric_rings(water, 10, domain, viewport=shapely.box(-2, -2, -1, -1))), [])

    def test_saved_water_fill_migrates_without_changing_saved_input(self):
        recipe = default_recipe()
        for enabled in [False, True]:
            for features in [recipe['features'], recipe['outside']['features']]:
                features.pop('water_treatment', None); features.pop('water_spacing_mm', None)
                features['water_fill'] = enabled
            original = copy.deepcopy(recipe)
            updated = validate_recipe(recipe)
            self.assertEqual(recipe, original)
            for features in [updated['features'], updated['outside']['features']]:
                self.assertEqual(features['water_treatment'], 'fill' if enabled else 'none')
                self.assertNotIn('water_fill', features)
        for key, value in [('water_spacing_mm', 0), ('water_treatment', 'spiral')]:
            bad = default_recipe(); bad['features'][key] = value
            with self.assertRaises(ValueError): validate_recipe(bad)

    def test_echo_lines_do_not_retrace_concentric_fill(self):
        recipe=default_recipe(); recipe['center']=[0,0]
        recipe['framing']['scale_denominator']=12000
        recipe['features'].update(water_treatment='concentric',water_spacing_mm=1,shore='echo',
                                  parks=False,roads='none',buildings='none',labels='none')
        data=dict(source={'attribution':'Fixture'},issues=[],roads=[],areas=[
            dict(kind='water',id='lake',name='',geometry=shapely.box(-800,-800,800,800))])
        with tempfile.TemporaryDirectory() as temp, patch('cartographer.compose.dataset',return_value=data):
            root=Path(temp)
            result=compose(recipe,root/'first',cache=root/'cache',version='echo-fixture')
            self.assertGreater(result['feature_counts']['water/concentric'],10)
            self.assertNotIn('water/echo',result['feature_counts'])
            recipe['features']['water_spacing_mm']=2
            result=compose(recipe,root/'second',cache=root/'cache',version='echo-fixture')
            self.assertEqual(result['feature_counts']['water/echo'],2)
            self.assertEqual([n['node'] for n in result['build_cache']['nodes'] if not n['reused']],['Water fill','Shore paint'])

    def test_vector_rings_in_both_views_bind_roles_and_reuse_other_stages(self):
        recipe = default_recipe(); recipe['center'] = [0, 0]
        recipe['framing']['scale_denominator'] = 12000
        recipe['features'].update(water_treatment='concentric', park_treatment='concentric',
                                  labels='none', roads='none', buildings='none', shore='line')
        recipe['ink_roles']['water'] = 'red'
        recipe['camera'].update(elevation_deg=45, exaggeration=1)
        lake = shapely.Point(400, 200).buffer(650).difference(shapely.Point(400, 250).buffer(100))
        data = dict(source={'attribution':'Fixture'}, issues=[], roads=[], areas=[
            dict(kind='water', id='lake', name='', geometry=lake),
            dict(kind='parks', id='park', name='', geometry=shapely.box(-1300, -1000, -400, 1000))])
        def height(points, zoom):
            p = np.asarray(points); return 100+50*np.sin(p[:, 0]/1000)
        def elevation(r, domain, *args, **kwargs):
            x0, y0, x1, y1 = domain.bounds
            xs = np.linspace(x0, x1, 17); ys = np.linspace(y0, y1, 17); xx, yy = np.meshgrid(xs, ys)
            return xs, ys, height(np.column_stack([xx.ravel(), yy.ravel()]), 0).reshape(xx.shape), {'tiles':[]}
        with tempfile.TemporaryDirectory() as temp, patch('cartographer.compose.dataset', return_value=data), \
             patch('cartographer.compose.elevations', side_effect=elevation), patch('cartographer.compose.ElevationSampler') as sampler:
            sampler.return_value.sample.side_effect=height; sampler.return_value.inputs={}
            root = Path(temp)
            for relief in [False, True]:
                recipe['camera']['relief'] = relief
                out = root/str(relief)
                result = compose(recipe, out, quality='draft', cache=root/'cache', version='area-fixture')
                self.assertGreater(result['feature_counts']['water/concentric'], 5)
                self.assertGreater(result['feature_counts']['parks/concentric'], 5)
                intent = json.loads((out/'map.print-intent.json').read_text())
                for category, ink in [('water', 'red'), ('parks', 'green')]:
                    self.assertTrue(any(b['feature_group']==category+'/concentric' and b['ink_id']==ink for b in intent['bindings']))
                with pymupdf.open(out/'map.pdf') as doc:
                    marks = [g for g in doc[0].get_drawings() if '/concentric|' in g.get('layer', '')]
                    self.assertGreater(len(marks), 10)
                    for mark in marks:
                        self.assertEqual(mark['type'], 's')
                        self.assertAlmostEqual(mark['width'], .65, places=5)
                    self.assertFalse(doc[0].get_images())
                recipe['features']['water_spacing_mm'] = 4
                changed = compose(recipe, root/(str(relief)+'-changed'), quality='draft', cache=root/'cache', version='area-fixture')
                self.assertEqual([n['node'] for n in changed['build_cache']['nodes'] if not n['reused']], ['Water fill'])
                recipe['features']['water_spacing_mm'] = 2.5
