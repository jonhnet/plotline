"""Vector terrain marks: visible apparent contours and draped slope shading.

No raster fills, triangle wireframes, or cast-shadow tracing. Lighting uses
smooth vertex normals; each hatch row retains its identity through draping.
"""
import math

import contourpy
import numpy as np
import shapely
from shapely import affinity

from build_map import parts
from .mesh_union import mesh_union
from .area_geometry import valid_area


def silhouette_lines(terrain, progress=None):
    """Shared edges separating camera-facing and back-facing slopes, occluded.

    Domain edges are omitted: a tile/fetch boundary is not a mountain ridge.
    Visibility is applied only to centerlines, then joined before stroking so
    page-width ink never inherits the individual triangle clips.
    """
    triangles=terrain.triangles
    normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    facing=normals@terrain.camera>0
    edges={};candidates=[]
    for owner,ids in enumerate(terrain.indices):
        for a,b in zip(ids,np.roll(ids,-1)):
            key=tuple(sorted((int(a),int(b))))
            if key in edges:
                other=edges.pop(key)
                if facing[owner]!=facing[other]:
                    candidates.append((key,owner if facing[owner] else other))
            else:edges[key]=owner
    projected=terrain.vertices@terrain.projection.T*terrain.scale+terrain.offset
    kept=[]
    for index,(ids,owner) in enumerate(candidates):
        if progress and index%128==0:progress('Clipping terrain silhouettes',index,len(candidates),'edges')
        if terrain.visible[owner].is_empty:continue
        line=shapely.LineString(projected[list(ids)])
        # Canonical mesh projection is rounded at 1e-7 pt. A sub-micron halo
        # admits that shared boundary without revealing a hidden ridge.
        kept.extend(parts(line.intersection(terrain.visible[owner].buffer(2e-7)),'LineString'))
    if not kept:return []
    return parts(shapely.line_merge(shapely.union_all(shapely.set_precision(kept,1e-5))),'LineString')


def vertex_illumination(vertices,indices,lighting,latitude_factor=1,exaggeration=1):
    points=vertices.copy();points[:,2]*=exaggeration/latitude_factor
    triangles=points[indices]
    normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    # Area weighting avoids bias from the extra fan triangles at quadtree seams.
    smooth=np.zeros_like(points,dtype=float)
    for corner in range(3):np.add.at(smooth,indices[:,corner],normals)
    smooth/=np.maximum(np.linalg.norm(smooth,axis=1)[:,None],1e-30)
    az,el=np.radians([lighting['azimuth_deg'],lighting['elevation_deg']])
    sun=np.array([np.sin(az)*np.cos(el),np.cos(az)*np.cos(el),np.sin(el)])
    return smooth@sun


def shade_regions(vertices,indices,lighting,latitude_factor=1,exaggeration=1,progress=None,triangle_ids=None):
    """Two nested darkness bands with interpolated, continuous boundaries.

    Thresholds relative to level ground keep flat country clear at every sun
    elevation. Dim slopes receive one direction; darker slopes receive both.
    """
    light=vertex_illumination(vertices,indices,lighting,latitude_factor,exaggeration)
    # Smooth normals need every neighbor, including off-page terrain. Cull only
    # the polygons we construct, after calculating illumination on the full mesh.
    if triangle_ids is not None:indices=indices[triangle_ids]
    reference=math.sin(math.radians(lighting['elevation_deg']))
    triangles=vertices[indices][:,:,:2]
    values=light[indices];regions=[]
    for band,threshold in enumerate([.75*reference,.25*reference]):
        selected=np.flatnonzero(np.min(values,axis=1)<threshold);polygons=[]
        for count,idx in enumerate(selected):
            if progress and count%512==0:progress(f'Computing hillshade band {band+1} of 2',count,len(selected),'facets')
            xy=triangles[idx];v=values[idx];ring=[]
            for a,b in [(0,1),(1,2),(2,0)]:
                if v[a]<threshold:ring.append(xy[a])
                if (v[a]<threshold)!=(v[b]<threshold):
                    ring.append(xy[a]+(threshold-v[a])/(v[b]-v[a])*(xy[b]-xy[a]))
            if len(ring)>=3:polygons.append(shapely.Polygon(ring))
        if progress:progress(f'Joining hillshade band {band+1} of 2',len(selected),len(selected),'facets')
        regions.append(mesh_union(polygons,grid_size=1e-5))
    return regions


def grid_shade_regions(xs,ys,z,lighting,latitude_factor=1,progress=None):
    """Flat-map shading from paper-scale elevations, independent of relief.

    A top-down camera has no projected height error, so a mesh refined for
    that error can miss entire slopes. Differentiate the detailed DEM instead;
    native filled contours join darkness bands without millions of triangles.
    Heights are real metres; horizontal Mercator units need latitude correction.
    """
    if progress:progress('Computing detailed terrain slopes',None,None,None)
    dy,dx=np.gradient(z,ys,xs)
    dx/=latitude_factor;dy/=latitude_factor
    az,el=np.radians([lighting['azimuth_deg'],lighting['elevation_deg']])
    light=(np.sin(el)-dx*np.sin(az)*np.cos(el)-dy*np.cos(az)*np.cos(el))/np.sqrt(1+dx*dx+dy*dy)
    generator=contourpy.contour_generator(x=xs,y=ys,z=light,name='serial',fill_type='OuterOffset')
    regions=[]
    for band,threshold in enumerate([.75*np.sin(el),.25*np.sin(el)]):
        if progress:progress(f'Outlining hillshade band {band+1} of 2',band,2,'bands')
        points,offsets=generator.filled(-np.inf,float(threshold))
        polygons=[]
        for xy,ends in zip(points,offsets):
            rings=[xy[a:b] for a,b in zip(ends[:-1],ends[1:])]
            polygons.append(shapely.Polygon(rings[0],rings[1:]))
        regions.append(valid_area(shapely.MultiPolygon(polygons)))
    return regions


def hatch_rows(geometry,spacing,angle,progress=None):
    """Yield a continuous ground-anchored row at a time; never node crossings."""
    if geometry.is_empty:return
    # Affine roundoff can invalidate thin holes; stripes and rows both need a
    # valid area, even when the unrotated input was already checked.
    g=valid_area(affinity.rotate(geometry,-angle,origin=(0,0)))
    x0,y0,x1,y1=g.bounds
    rows=np.arange(math.floor(y0/spacing)*spacing,y1+spacing,spacing)
    # Each stripe spans complete rows and cuts only between them. Restrict the
    # expensive polygon overlay once per stripe instead of revisiting all map
    # boundaries for every row; row coordinates, identity and continuity stay global.
    block=8 if len(rows)>16 and shapely.get_num_coordinates(g)>256 else max(1,len(rows))
    for start in range(0,len(rows),block):
        yy=rows[start:start+block]
        area=g.intersection(shapely.box(x0,yy[0]-spacing/2,x1,yy[-1]+spacing/2)) if block<len(rows) else g
        for index,y in enumerate(yy,start):
            if progress and index%32==0:progress('Drawing hillshade hatching',index,len(rows),'rows')
            row=shapely.LineString([(x0,y),(x1,y)]).intersection(area)
            if not row.is_empty:yield affinity.rotate(row,angle,origin=(0,0))
