"""Polygon validity at geometry operations that can introduce roundoff crossings."""
import shapely

from build_map import parts


def valid_area(geometry):
    """Repair interiors and dissolve overlaps without dropping filled areas.

    Simplification and floating-point rotation can invalidate otherwise valid
    polygons. Repair components individually, then union them; repairing an
    entire MultiPolygon with even/odd linework would turn overlaps into holes.
    Collapsed line remnants cannot paint an area.
    """
    if geometry.is_valid and geometry.geom_type in ('Polygon','MultiPolygon'):
        return geometry
    polygons=[]
    for polygon in parts(geometry,'Polygon'):
        polygons.extend(parts(polygon if polygon.is_valid else shapely.make_valid(polygon),'Polygon'))
    result=shapely.union_all(polygons)
    if not result.is_valid:
        raise ValueError('Could not repair map area: '+shapely.is_valid_reason(result))
    return result
