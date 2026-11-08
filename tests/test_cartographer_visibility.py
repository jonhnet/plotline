import os
import json
import sys
from types import SimpleNamespace
from pathlib import Path
import importlib.util
import unittest
from unittest.mock import patch

import numpy as np
import shapely
from shapely.strtree import STRtree

from cartographer.visibility import visible_faces, native_backend
from cartographer.terrain import MeshTerrain


def legacy(projected,rank,frame):
    tree=STRtree(projected);visible=np.empty(len(projected),dtype=object)
    visible[:]=shapely.GeometryCollection()
    for idx in tree.query(frame,predicate='intersects'):
        pieces=shapely.get_parts(projected[idx].intersection(frame))
        face=shapely.union_all(pieces[shapely.get_type_id(pieces)==3])
        if face.is_empty:continue
        near=tree.query(face,predicate='intersects');near=near[rank[near]<rank[idx]]
        if len(near):face=face.difference(shapely.union_all(projected[near]))
        visible[idx]=face
    return visible


class VisibilityTests(unittest.TestCase):
    def compare(self,projected,rank,frame):
        expected=legacy(projected,rank,frame)
        previous=None
        backends=[('python',1)]
        if importlib.util.find_spec('_cartographer_geometry'):backends.extend([('rust',1),('rust',4)])
        for backend,workers in backends:
            with self.subTest(backend=backend,workers=workers),patch.dict(os.environ,
                    PLOTLINE_MAP_GEOMETRY=backend,PLOTLINE_MAP_GEOMETRY_THREADS=str(workers)):
                actual,stats=visible_faces(projected,rank,frame)
            self.assertTrue(shapely.is_valid(actual).all())
            self.assertTrue((shapely.is_empty(actual)==shapely.is_empty(expected)).all())
            self.assertLess(float(shapely.area(shapely.symmetric_difference(expected,actual)).sum()),1e-8)
            if previous is not None:
                self.assertEqual(list(shapely.to_wkb(actual)),previous)
            previous=list(shapely.to_wkb(actual))
        return stats

    def test_coverage_and_frame_shortcuts_preserve_small_visible_remnants(self):
        # One wholly covering nearer face, a combination of two occluders,
        # edge-only contacts, an off-page face and a tiny surviving slit.
        projected=np.array([shapely.box(0,0,10,10),shapely.box(1,1,2,2),
            shapely.box(11,0,13,10),shapely.box(13.00001,0,15,10),shapely.box(12,1,14,9),
            shapely.box(15,0,16,10),shapely.box(30,30,40,40),shapely.box(-1,0,0,10)],dtype=object)
        stats=self.compare(projected,np.arange(len(projected)),shapely.box(0,0,15,10))
        self.assertGreater(stats['frame_containment_skips'],0)
        self.assertGreater(stats['single_occluder_skips'],0)

    def test_random_triangle_occlusion_matches_original_overlay(self):
        rng=np.random.default_rng(216)
        xy=rng.uniform(-5,25,(500,1,2))+rng.uniform(-4,4,(500,3,2))
        projected=shapely.set_precision(shapely.set_precision(shapely.polygons(xy),1e-7),0)
        self.compare(projected,rng.permutation(len(projected)),shapely.box(0,0,20,20).buffer(.5))

    def test_contact_ring_must_not_expose_a_face_behind_a_nearer_mountain(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/cartographer/contact-occlusion.json').read_text())
        projected=shapely.from_wkb([bytes.fromhex(f['wkb']) for f in fixture['faces']])
        planes=np.array([f['depth_plane'] for f in fixture['faces']]);target=projected[-1]
        # Independent 3D oracle: two nearer triangles cover this entire face,
        # and their affine depth planes are ahead at all three face vertices.
        ids=[i for i,f in enumerate(fixture['faces']) if f['id'] in [19050,19057]]
        xy=np.asarray(target.exterior.coords)[:3];points=np.column_stack([xy,np.ones(3)])
        for i in ids:self.assertGreater(float(np.min(points@(planes[i]-planes[-1]))),2000)
        front=shapely.union_all(projected[ids])
        self.assertLess(target.difference(front).area,1e-8)
        self.assertGreater(target.area,2)
        leaked=MeshTerrain.__new__(MeshTerrain)
        leaked.projected=projected;leaked.depth_planes=planes
        leaked.visible=[shapely.Polygon() for _ in projected];leaked.visible[-1]=target
        audit=leaked.audit_visibility()
        self.assertEqual(audit['samples'],1)
        self.assertGreater(audit['maximum_frontmost_depth_error_m'],2000)
        backends=[('python',1)]
        if importlib.util.find_spec('_cartographer_geometry'):backends.extend([('rust',1),('rust',4)])
        for backend,workers in backends:
            with self.subTest(backend=backend,workers=workers),patch.dict(os.environ,
                    PLOTLINE_MAP_GEOMETRY=backend,PLOTLINE_MAP_GEOMETRY_THREADS=str(workers)):
                visible,_=visible_faces(projected,np.arange(len(projected)),target.envelope.buffer(20))
                self.assertTrue(visible[-1].is_empty,'A nearer mountain must hide this whole face')

    def test_stale_native_visibility_algorithm_requires_rebuild(self):
        old=SimpleNamespace(geos_version=lambda:shapely.geos_version_string,visibility_revision=1)
        with patch.dict(os.environ,PLOTLINE_MAP_GEOMETRY='rust'),patch.dict(sys.modules,{'_cartographer_geometry':old}):
            with self.assertRaisesRegex(RuntimeError,'Rebuild'):native_backend()

    @unittest.skipUnless(importlib.util.find_spec('_cartographer_geometry'),'Optional Rust kernel is not installed')
    def test_native_rejects_bad_offsets_indices_and_worker_counts(self):
        with patch.dict(os.environ,PLOTLINE_MAP_GEOMETRY='rust'):native=native_backend()
        raw=[shapely.box(0,0,1,1).wkb]
        for count in [0,17]:
            with self.assertRaises(ValueError):native.Occluders(raw,count)
        kernel=native.Occluders(raw,1)
        for offsets,near in [([0],[]),([1,1],[]),([0,2],[0]),([0,1],[5])]:
            with self.assertRaises(ValueError):kernel.resolve(raw,offsets,near)
        with self.assertRaises(RuntimeError):native.Occluders([b'bad wkb'],1)


if __name__=='__main__':unittest.main()
