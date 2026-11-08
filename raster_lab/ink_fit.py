"""Fit traced ink with GEOS; every coordinate remains a physical pen path.

The binary source is a union of pixel-cell rectangles, not an outline through
pixel centers. Fit endpoints/junctions stay fixed. Additional weight passes must
fit in source ink and overlap the preceding pass; no recognition is involved.
"""
import math
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box

from . import engine

QUADS = 12
ROUNDING = .001


@lru_cache(maxsize=2)
def _pixel_shape(data, height, width, width_mm, height_mm):
    mask = np.frombuffer(data, bool).reshape(height, width)
    dx, dy = width_mm / width, height_mm / height
    active, rectangles = {}, []
    for y in range(height + 1):
        row = mask[y] if y < height else np.zeros(width, bool)
        changes = np.diff(np.r_[False, row, False].astype(np.int8))
        runs = set(zip(np.where(changes == 1)[0], np.where(changes == -1)[0]))
        for start, end in active.keys() - runs:
            top = active.pop((start, end))
            rectangles.append(box(start*dx, top*dy, end*dx, y*dy))
        for run in runs:
            active.setdefault(run, y)
    shape = shapely.union_all(rectangles)
    shapely.prepare(shape)
    return shape


def pixel_shape(mask, drawing):
    return _pixel_shape(np.asarray(mask, bool).tobytes(), *mask.shape,
                        drawing.width_mm, drawing.height_mm)


def footprint(line, diameter):
    # Circumscribe the round cap/join approximation and reserve SVG rounding.
    radius = (diameter/2 + ROUNDING) / math.cos(math.pi/(4*QUADS))
    return line.buffer(radius, quad_segs=QUADS)


def rounded_corners(points, cut):
    """Bounded corner cuts, leaving graph endpoints and closed-loop anchors fixed."""
    points = np.asarray(points, float)
    if len(points) < 3:
        return points
    result = [points[0]]
    for a, b, c in zip(points, points[1:], points[2:]):
        left, right = np.linalg.norm(a-b), np.linalg.norm(c-b)
        if min(left, right) < 1e-9:
            result.append(b)
            continue
        step = min(cut, left/4, right/4)
        u, v = b + (a-b)*step/left, b + (c-b)*step/right
        result.append(u)
        # A quadratic corner, flattened at four fixed parameter values.
        for t in (.25, .5, .75, 1.):
            result.append((1-t)**2*u + 2*t*(1-t)*b + t*t*v)
    result.append(points[-1])
    return np.asarray(result)


def smooth_paths(paths, ink, drawing, tolerance):
    fitted, changed = [], 0
    for path in paths:
        original = LineString(path)
        closed = path[0] == path[-1]
        # Dots, short junction arms and tiny loops have nothing to spare.
        if len(path) < 4 or original.length < tolerance*8:
            fitted.append(path)
            continue
        accepted = path
        for scale in (1., .5, .25):
            epsilon = tolerance * .7 * scale
            simple = original.simplify(epsilon, preserve_topology=True)
            points = rounded_corners(simple.coords, tolerance*.3*scale)
            compact = LineString(points).simplify(.002, preserve_topology=True)
            candidates = engine.finish_paths([list(compact.coords)], drawing, 1e-6)
            if len(candidates) != 1:
                continue
            candidate = candidates[0]
            line = LineString(candidate)
            if candidate[0] != path[0] or candidate[-1] != path[-1]:
                continue
            if original.is_simple and not line.is_simple:
                continue
            if closed:
                if not line.is_ring or Polygon(line).area < Polygon(original).area*.75:
                    continue
            # The total fitting displacement is bounded by the simplification
            # tolerance plus the corner cut. Also reject a measured violation.
            if shapely.hausdorff_distance(original, line, densify=.25) > tolerance+ROUNDING:
                continue
            pen = footprint(line, drawing.pen_mm)
            if not ink.covers(pen):
                # An unsafe fit leaves the original path untouched; new
                # smoothing never spends the baseline's existing excess.
                continue
            accepted = candidate
            break
        changed += accepted != path
        fitted.append(accepted)
    return fitted, changed


def lines(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'LineString':
        return [geometry]
    if hasattr(geometry, 'geoms'):
        return [line for part in geometry.geoms for line in lines(part)]
    return []


def ink_tiles(ink, diameter, tile_mm=8.):
    radius = (diameter/2 + ROUNDING) / math.cos(math.pi/(4*QUADS))
    # Rectilinear source cells permit a conservative mitered inset, avoiding
    # dozens of arc vertices at every pixel corner. Final round-pen containment
    # is still tested against the exact source, not this working approximation.
    safe = ink.buffer(-radius, join_style='mitre')
    if safe.is_empty:
        return []
    xmin, ymin, xmax, ymax = safe.bounds
    parts = []
    for y in np.arange(math.floor(ymin/tile_mm)*tile_mm, ymax, tile_mm):
        for x in np.arange(math.floor(xmin/tile_mm)*tile_mm, xmax, tile_mm):
            tile = shapely.clip_by_rect(safe, x, y, x+tile_mm, y+tile_mm)
            if tile.is_empty:
                continue
            if not tile.is_valid:
                tile = shapely.make_valid(tile)
            parts.append(tile)
    return parts


def weight_paths(paths, ink, drawing, max_radius):
    parts = ink_tiles(ink, drawing.pen_mm)
    tree = shapely.STRtree(parts)
    step = drawing.pen_mm * .85
    result = []
    for path in paths:
        base = LineString(path)
        if base.length < drawing.pen_mm*2:
            continue
        # Clip into spatial tiles once: a connected outline can span the whole
        # image, so indexing only whole connected components is insufficient.
        bounds = box(*base.buffer(max_radius+drawing.pen_mm).bounds)
        nearby = tree.query(bounds)
        local_safe = shapely.union_all([parts[int(i)] for i in nearby]).intersection(bounds)
        if local_safe.is_empty:
            continue
        for side in (-1, 1):
            previous = base
            for level in range(1, math.floor(max_radius/step)+1):
                offset = base.offset_curve(side*level*step, quad_segs=QUADS)
                if offset.is_empty:
                    break
                # Every new pass touches/overlaps its predecessor. Clipping to
                # the inset source preserves counters and separate neighbors.
                clipped = offset.intersection(local_safe).intersection(previous.buffer(drawing.pen_mm*.95, quad_segs=QUADS))
                accepted = []
                for part in lines(clipped):
                    for candidate in engine.finish_paths([list(part.coords)], drawing, drawing.pen_mm*2):
                        line = LineString(candidate)
                        if ink.covers(footprint(line, drawing.pen_mm)):
                            result.append(candidate)
                            accepted.append(line)
                if not accepted:
                    break
                previous = shapely.union_all(accepted)
    return result


def fit(paths, mask, drawing, recipe):
    ink = pixel_shape(mask, drawing)
    smoothed, _ = smooth_paths(paths, ink, drawing, recipe['fit_tolerance_mm'])
    if recipe.get('recover_weight', False):
        return smoothed + weight_paths(smoothed, ink, drawing, recipe['weight_max_radius_mm'])
    return smoothed
