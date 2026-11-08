"""Dissolve edge-matched mesh cells using their boundary, with a GEOS fallback.

Callers supply non-overlapping mesh cells, not arbitrary overlapping features.
Shared edges already encode topology: cancel them instead of asking a general
polygon overlay to rediscover every intersection. GEOS polygonizes the remaining
edges and an indexed interior-point query distinguishes land from holes.
"""
import numpy as np
import shapely


def mesh_union(geometries,grid_size=None):
    source=np.asarray(geometries,dtype=object)
    def fallback():return shapely.union_all(source,grid_size=grid_size)
    cells=shapely.set_precision(source,grid_size) if grid_size else source
    cells=shapely.get_parts(cells)
    cells=cells[(shapely.get_type_id(cells)==3)&~shapely.is_empty(cells)]
    if not len(cells):return fallback()
    # Snap first, so both sides of an interpolated shade edge have the same
    # endpoints. No intersection nodes are introduced along internal edges.
    xy,owners=shapely.get_coordinates(shapely.get_rings(cells),return_index=True)
    edges=np.stack([xy[:-1],xy[1:]],axis=1)[owners[:-1]==owners[1:]]
    reverse=(edges[:,0,0]>edges[:,1,0])|((edges[:,0,0]==edges[:,1,0])&(edges[:,0,1]>edges[:,1,1]))
    edges[reverse]=edges[reverse,::-1]
    edges,counts=np.unique(edges.reshape(-1,4),axis=0,return_counts=True)
    if np.any(counts>2):return fallback()
    boundary=edges[counts==1].reshape(-1,2,2)
    try:
        polygons=shapely.get_parts(shapely.polygonize(shapely.linestrings(boundary)))
        selected,_=shapely.STRtree(cells).query(shapely.point_on_surface(polygons),predicate='intersects')
        result=shapely.multipolygons(polygons[np.unique(selected)])
        expected=float(shapely.area(cells).sum())
        # Point contacts can produce several shells. A MultiPolygon retains
        # those contacts; never fill their holes or silently discard cells.
        if not result.is_valid or abs(result.area-expected)>max(1e-8,expected*1e-12):return fallback()
        return result
    except shapely.errors.GEOSException:
        return fallback()
