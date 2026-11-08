"""Local heightfield occlusion, with the same GEOS operations in both backends.

The caller supplies an exact near-to-far rank. The index only finds possible
occluders; page containment and complete coverage skip needless overlays.
"""
import os

import numpy as np
import shapely
from shapely.strtree import STRtree


def native_backend():
    backend=os.environ.get('PLOTLINE_MAP_GEOMETRY','auto')
    if backend not in ('auto','python','rust'):
        raise ValueError('PLOTLINE_MAP_GEOMETRY must be auto, python or rust')
    if backend=='python':return None
    try:import _cartographer_geometry as native
    except ModuleNotFoundError:
        if backend=='rust':raise
        return None
    if getattr(native,'visibility_revision',None)!=2:
        raise RuntimeError('Rebuild the map geometry extension: its visibility algorithm is outdated')
    if native.geos_version().split('-')[0]!=shapely.geos_version_string:
        raise RuntimeError('Map geometry extension and Shapely must use the same GEOS; rebuild the extension')
    return native


def python_resolve(projected,faces,offsets,near):
    """Batched reference implementation, also used without the optional extension."""
    owners=np.repeat(np.arange(len(faces)),np.diff(offsets))
    hidden=np.zeros(len(faces),dtype=bool)
    hidden[owners[shapely.covers(projected[near],faces[owners])]]=True
    # A shared edge or vertex cannot hide polygon area. Retaining such
    # contacts needlessly enlarges unions and can trigger GEOS ring degeneracy.
    keep=~shapely.touches(projected[near],faces[owners])
    near=near[keep];owners=owners[keep]
    offsets=np.r_[0,np.cumsum(np.bincount(owners,minlength=len(faces)))]
    result=faces.copy();result[hidden]=shapely.Polygon()
    for i in np.flatnonzero(~hidden & (np.diff(offsets)>0)):
        candidates=projected[near[offsets[i]:offsets[i+1]]]
        occluder=candidates[0] if len(candidates)==1 else shapely.union_all(candidates)
        result[i]=shapely.difference(faces[i],occluder)
    return result,int(hidden.sum())


def visible_faces(projected,rank,frame,progress=lambda *args:None):
    tree=STRtree(projected);active=tree.query(frame,predicate='intersects')
    visible=np.empty(len(projected),dtype=object);visible[:]=shapely.GeometryCollection()
    native=native_backend()
    workers=int(os.environ.get('PLOTLINE_MAP_GEOMETRY_THREADS','4'))
    kernel=native.Occluders(list(shapely.to_wkb(projected)),workers) if native else None
    stats=dict(visibility_backend='rust' if native else 'python',visibility_workers=workers if native else 1,
               active_triangles=len(active),visibility_candidate_pairs=0,
               frame_containment_skips=0,single_occluder_skips=0)
    shapely.prepare(frame)
    for start in range(0,len(active),4096):
        progress('visibility','Resolving detailed terrain visibility',start,len(active),'facets')
        ids=active[start:start+4096];faces=projected[ids].copy()
        inside=shapely.covers(frame,faces);stats['frame_containment_skips']+=int(inside.sum())
        faces[~inside]=shapely.intersection(faces[~inside],frame)
        # Both operands are convex polygons; a boundary-only contact can be a
        # line/point, which must not become a visible terrain face.
        faces[shapely.get_type_id(faces)!=3]=shapely.Polygon()
        owner,near=tree.query(faces,predicate='intersects')
        keep=rank[near]<rank[ids[owner]];owner=owner[keep];near=near[keep]
        offsets=np.r_[0,np.cumsum(np.bincount(owner,minlength=len(ids)))]
        stats['visibility_candidate_pairs']+=len(near)
        if kernel:
            raw,hidden=kernel.resolve(list(shapely.to_wkb(faces)),offsets.tolist(),near.tolist())
            visible[ids]=shapely.from_wkb(raw)
        else:
            visible[ids],hidden=python_resolve(projected,faces,offsets,near)
        stats['single_occluder_skips']+=hidden
    return visible,stats
