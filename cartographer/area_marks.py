"""Explicit area textures in ground coordinates, before terrain projection."""
import math

import numpy as np
import shapely


def _distance_bound(target, boundary):
    """Conservative maximum shore distance, without a dense distance field.

    Distance to a fixed set is 1-Lipschitz: the center's distance plus a
    cell's half-diagonal bounds every point in that cell. Test only cells
    touching the requested water, including disconnected pieces and holes.
    """
    x0, y0, x1, y1 = target.bounds
    dx, dy = (x1-x0)/16, (y1-y0)/16
    x, y = np.meshgrid(x0+np.arange(16)*dx, y0+np.arange(16)*dy)
    cells = shapely.box(x.ravel(), y.ravel(), x.ravel()+dx, y.ravel()+dy)
    cells = cells[shapely.intersects(cells, target)]
    distance = float(np.max(shapely.distance(shapely.centroid(cells), boundary))) + math.hypot(dx, dy)/2
    # Buffer arcs use 8 steps per quadrant. Allow two steps per chord (also
    # covering rounding the segment count at joins) in the stopping bound.
    return distance/math.cos(math.pi/16)


def concentric_rings(geometry, spacing, domain=None, progress=None, *, viewport=None):
    """Yield successive inward contours, preserving islands and split lobes.

    Offsets are measured from the original boundary, not the preceding ring.
    This avoids accumulated rounding/simplification and any connecting strokes
    across land. A fetch crop is not a shoreline: for cropped polygons, expand
    only the known geographic boundary and intersect its wavefront with water.
    Complete polygons use the cheaper equivalent negative polygon buffer.
    A viewport limits output and work, never introduces a new shoreline.
    """
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError('Concentric spacing must be positive and finite')
    if geometry.is_empty:
        return
    # Tile assembly uses a 0.001 m precision grid. Remove that crop-edge
    # uncertainty without rounding the authored ring coordinates themselves.
    tolerance = max(.002, spacing*1e-8)
    crop = domain.boundary.buffer(tolerance) if domain is not None else None
    boundary = geometry.boundary
    cropped = crop is not None and boundary.intersects(crop)
    if cropped:
        boundary = boundary.difference(crop)
    if boundary.is_empty:
        return  # Open water throughout the fetched area: no known shoreline.
    target = geometry if viewport is None else geometry.intersection(viewport)
    if target.is_empty or target.area == 0:
        return
    bound = _distance_bound(target, boundary) + tolerance
    limit = math.ceil(bound/spacing)
    if cropped:
        # Keep all shore that can influence any requested ring. The new cut
        # endpoints are farther away than the largest buffer, so their caps
        # cannot enter the output. This avoids repeatedly buffering thousands
        # of remote shoreline vertices in a low-camera fetch rectangle.
        x0, y0, x1, y1 = target.bounds
        reach = limit*spacing + tolerance
        boundary = boundary.intersection(shapely.box(x0-reach, y0-reach, x1+reach, y1+reach))
    for index in range(1, limit+1):
        if progress:
            progress(f'Computing concentric outline {index} (at most {limit})', index-1, limit, 'outlines')
        distance = index*spacing
        if cropped:
            # Do not erode artificial crop edges. That would create rectangular
            # rings and make real shoreline contours disappear near the crop.
            wave = boundary.buffer(distance, quad_segs=8)
            # A ring exactly on the viewport edge still belongs to the map.
            if shapely.contains_properly(wave, target):
                break
            rings = wave.boundary.intersection(target)
        else:
            inner = geometry.buffer(-distance, quad_segs=8)
            if inner.is_empty:
                break
            if not inner.intersects(target):
                break
            rings = inner.boundary.intersection(target)
        if not rings.is_empty:
            yield index, rings
    if progress:
        progress('Concentric outlines complete', index, index, 'outlines')
