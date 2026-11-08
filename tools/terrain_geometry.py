#!/usr/bin/env python3
"""Piecewise planar heightfield projection and vector visibility using GEOS.

Orthographic rays traverse regular grid cells monotonically in each horizontal
axis. That supplies a topological near-to-far order, independent of height.
Subtracting already nearer projected triangles gives exact visible polygons for
this mesh. Never sort triangles by centroid camera depth: hills break that order.
"""
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, unary_union
from shapely.strtree import STRtree

from build_map import parts


def stroke_clip(geometry, width):
    """End cuts for page-width strokes; there are no internal terrain seams."""
    return geometry.buffer(width/2+.75, cap_style='flat', quad_segs=8)


class Terrain:
    def __init__(self, xs, ys, elevations, exaggeration, azimuth, elevation, frame, progress=None):
        self.xs, self.ys = np.asarray(xs), np.asarray(ys)
        self.z = np.asarray(elevations, dtype=float)
        if len(xs) < 2 or len(ys) < 2:
            raise ValueError('Terrain needs at least two vertices on each axis')
        if self.z.shape != (len(ys), len(xs)) or not np.isfinite(self.z).all():
            raise ValueError('Expected a finite elevation at every grid vertex')
        if not (np.all(np.diff(xs) > 0) and np.all(np.diff(ys) > 0)):
            raise ValueError('Grid axes must increase east and north')
        if not (0 < elevation < 90 and exaggeration > 0):
            raise ValueError('Expected positive exaggeration and camera above ground')
        self.exaggeration = exaggeration
        az, el = np.radians([azimuth, elevation])
        self.toward = np.array([np.sin(az), np.cos(az)])
        self.camera = np.array([* (self.toward * np.cos(el)), np.sin(el)])
        # World x=east, y=north, z=up. PDF y increases downward.
        self.projection = np.array([[-np.cos(az), np.sin(az), 0],
                                    [np.sin(el)*np.sin(az), np.sin(el)*np.cos(az), -np.cos(el)]])
        xx, yy = np.meshgrid(xs, ys)
        self.vertices = np.stack([xx, yy, self.z*exaggeration], axis=-1).reshape(-1, 3)
        raw = self.vertices @ self.projection.T
        low, high = raw.min(axis=0), raw.max(axis=0)
        x0, y0, x1, y1 = frame
        if not (x1 > x0 and y1 > y0):
            raise ValueError('Expected a positive page frame')
        self.scale = min((x1-x0)/(high[0]-low[0]), (y1-y0)/(high[1]-low[1]))
        self.offset = np.array([(x0+x1)/2, (y0+y1)/2]) - (low+high)/2*self.scale
        projected = raw*self.scale+self.offset
        indices, order = [], []
        nx, ny = len(xs), len(ys)
        for j in range(ny-1):
            for i in range(nx-1):
                a = j*nx+i; b = a+1; c = a+nx; d = c+1
                rank = -np.sign(self.toward[0])*i - np.sign(self.toward[1])*j
                for tri in [(a,b,d),(a,d,c)]:
                    indices.append(tri)
                    centroid = self.vertices[list(tri), :2].mean(axis=0)
                    order.append((rank, -centroid @ self.toward, len(indices)-1))
        self.indices = np.asarray(indices)
        self.triangles = self.vertices[self.indices]
        self.flat = [Polygon(t[:,:2]) for t in self.triangles]
        # Shared projected vertices are rounded identically; union cannot create
        # sliver holes from almost-coincident shared triangle edges.
        self.projected = [shapely.set_precision(Polygon(projected[t]), 1e-7) for t in self.indices]
        self.transforms = []
        self.depth_planes = []
        for number, (tri, ids) in enumerate(zip(self.triangles, self.indices)):
            if progress and number % 64 == 0:
                progress('Building terrain mesh', number, len(indices), 'facets')
            matrix = np.column_stack([tri[:, :2], np.ones(3)])
            coefficients = np.linalg.solve(matrix, projected[ids])
            self.transforms.append([coefficients[0,0],coefficients[1,0],coefficients[0,1],
                                    coefficients[1,1],coefficients[2,0],coefficients[2,1]])
            image = np.column_stack([projected[ids], np.ones(3)])
            self.depth_planes.append(np.linalg.solve(image, tri @ self.camera)
                                     if abs(np.linalg.det(image)) > 1e-10 else np.zeros(3))
        self.index = STRtree(self.flat)
        self.visible = [shapely.GeometryCollection() for _ in indices]
        covered = shapely.GeometryCollection()
        for number, (_, _, idx) in enumerate(sorted(order)):
            if progress and number % 64 == 0:
                progress('Resolving hidden terrain', number, len(indices), 'facets')
            face = self.projected[idx]
            if face.is_empty:
                continue
            self.visible[idx] = shapely.difference(face, covered, grid_size=1e-7)
            covered = shapely.union(covered, face, grid_size=1e-7)
        self.outline = covered
        if progress:
            progress('Resolving hidden terrain', len(indices), len(indices), 'facets')
        self.stats = dict(triangles=len(indices),
                          fully_hidden_triangles=sum(g.is_empty for g in self.visible),
                          projected_face_area_pt2=sum(g.area for g in self.projected),
                          visible_face_area_pt2=sum(g.area for g in self.visible))
        self.audit_lines = []
        self.debug_contours = []

    def drape_area(self, geometry, progress=None):
        result = []
        indices = self.index.query(geometry, predicate='intersects')
        progress = progress if len(indices) >= 32 else None
        for number, idx in enumerate(indices):
            if progress and number % 32 == 0:
                progress('Projecting areas', number, len(indices), 'facets')
            if self.visible[idx].is_empty:
                continue
            piece = geometry.intersection(self.flat[idx])
            projected = affinity.affine_transform(piece, self.transforms[idx])
            result.append(projected.intersection(self.visible[idx]))
        if progress:
            progress('Joining projected areas', None, None, None)
        return unary_union(result)

    def ground_for_viewport(self, frame):
        """Conservative ground coverage of visible facets touching a page region.

        Retain whole facets, rather than inverse-clipping their polygons, so
        rounding cannot lose visible marks at the page edge. The caller pads
        the page region for finite stroke widths and other decorations.
        """
        return unary_union([ground for ground, visible in zip(self.flat, self.visible)
                            if visible.intersects(frame)])

    def drape_centerlines(self, geometry, identity, progress=None):
        """Project/cull centerlines and join surviving runs across mesh seams.

        A line is a tube-like cartographic mark, not paint confined to one mesh
        facet. Internal triangle edges must never shave its width. End cuts
        bound each visible run so round pen caps do not extend that run along
        a hidden continuation. Later PDF paint still occludes the full stroke.
        """
        lines, raw_lines = [], []
        before, after = 0., 0.
        indices = self.index.query(geometry, predicate='intersects')
        progress = progress if len(indices) >= 32 else None
        for number, idx in enumerate(indices):
            if progress and number % 32 == 0:
                progress('Projecting lines', number, len(indices), 'facets')
            piece = geometry.intersection(self.flat[idx])
            projected = affinity.affine_transform(piece, self.transforms[idx])
            before += projected.length
            if identity.startswith('terrain/contours|'):
                raw_lines.extend(parts(projected, 'LineString'))
            if self.visible[idx].is_empty:
                continue
            for line in parts(projected, 'LineString'):
                kept = line.intersection(self.visible[idx])
                lines.extend(parts(kept, 'LineString'))
                after += kept.length
        self.audit_lines.append(dict(source=identity, projected_length_pt=before, visible_length_pt=after))
        if progress:
            progress('Joining visible lines', None, None, None)
        # Tiny rounding here is 7,000 times finer than the HPGL coordinate grid.
        united = unary_union([shapely.set_precision(line, 1e-5) for line in lines])
        if united.geom_type == 'MultiLineString':
            united = linemerge(united)
        if raw_lines:
            self.debug_contours.append((unary_union(raw_lines), united))
        return united

    def drape_line(self, geometry, width, identity):
        """Return visible centerlines and their constant-width end-cut mask."""
        united = self.drape_centerlines(geometry, identity)
        # Keep the clip away from ordinary stroke edges (separate PDF clip and
        # stroke antialiasing would otherwise multiply their edge coverage).
        # Only the flat ends trim ink, at the endpoints of continuous visible
        # runs. Rejoining before constructing these masks removes mesh seams.
        return united, stroke_clip(united, width)

    def locate(self, x, y):
        point = Point(x, y)
        candidates = self.index.query(point, predicate='intersects')
        if not len(candidates):
            return None
        idx = candidates[0]
        projected = affinity.affine_transform(point, self.transforms[idx])
        is_visible = any(self.visible[i].buffer(1e-6).covers(projected) for i in candidates)
        return (projected.x, projected.y), is_visible

    def contours(self, interval):
        """Intersect the same terrain triangles with horizontal elevation planes."""
        if not np.isfinite(interval) or interval <= 0:
            raise ValueError('Contour interval must be positive')
        output = {}
        for level in np.arange(interval, self.z.max(), interval):
            pieces = []
            target = level*self.exaggeration
            for tri in self.triangles:
                if not tri[:,2].min() < target < tri[:,2].max():
                    continue
                points = []
                for a,b in [(tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])]:
                    if (a[2] < target <= b[2]) or (b[2] < target <= a[2]):
                        t = (target-a[2])/(b[2]-a[2])
                        points.append(a[:2]+t*(b[:2]-a[:2]))
                if len(points) == 2 and np.linalg.norm(points[1]-points[0]) > 1e-8:
                    pieces.append(LineString(points))
            output[float(level)] = unary_union(pieces)
        return output

    def audit_visibility(self, samples=1000):
        """Independent depth-plane test, without using cell order or its union.

        Each sampled visible point must belong to the frontmost triangle among
        ALL projected triangles covering that screen position.
        """
        tree = STRtree(self.projected)
        rng = np.random.default_rng(7831)
        available = [i for i,g in enumerate(self.visible) if g.area > 1e-6]
        chosen = rng.choice(available, min(samples, len(available)), replace=False)
        worst = 0.
        for idx in chosen:
            p = self.visible[idx].representative_point()
            v = np.array([p.x,p.y,1.])
            own_depth = self.depth_planes[idx] @ v
            for other in tree.query(p, predicate='intersects'):
                worst = max(worst, self.depth_planes[other] @ v - own_depth)
        return dict(samples=len(chosen), maximum_frontmost_depth_error_m=float(worst))
