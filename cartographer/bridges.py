"""Inferred bridge decks over a heightfield; heights are meters, not OSM layers.

Join bridge ways before fitting their vertical profile. Ground at the connected
span's approaches anchors a grade; an upper hull keeps it above intervening
ground without descending into valleys. This is cartographic inference, not a
survey of bridge structures or their clearance.
"""
from collections import defaultdict

import numpy as np
import shapely
from shapely.strtree import STRtree
from shapely.ops import substring

from build_map import parts


def road_key(road):
    return road['id'],road['cls'],road['level']


def grid_mesh(elevation):
    xs,ys,z,_=elevation
    xx,yy=np.meshgrid(xs,ys)
    vertices=np.stack([xx,yy,z],axis=-1).reshape(-1,3)
    a=(np.arange(len(ys)-1)[:,None]*len(xs)+np.arange(len(xs)-1)).ravel()
    indices=np.concatenate([np.column_stack([a,a+1,a+len(xs)+1]),
                            np.column_stack([a,a+len(xs)+1,a+len(xs)])])
    return vertices,indices


class GroundProfiles:
    def __init__(self,vertices,indices):
        from .terrain import plane_coefficients
        triangles=np.asarray(vertices)[np.asarray(indices)]
        self.faces=shapely.polygons(triangles[:,:,:2]);self.index=STRtree(self.faces)
        self.planes=plane_coefficients(triangles[:,:,:2],triangles[:,:,2])

    def profile(self,line):
        """All ground-surface breakpoints, so a ridge between samples is retained."""
        records=[]
        for i in self.index.query(line,predicate='intersects'):
            for piece in parts(line.intersection(self.faces[i]),'LineString'):
                xy=np.asarray(piece.coords)[:,:2]
                stations=shapely.line_locate_point(line,shapely.points(xy))
                heights=xy@self.planes[i,:2]+self.planes[i,2]
                records.extend(zip(stations,heights))
        if not records:return None
        records.sort();stations=[];heights=[]
        for station,height in records:
            if stations and station-stations[-1]<1e-6:
                heights[-1]=max(heights[-1],height)
            else:stations.append(float(station));heights.append(float(height))
        if stations[0]>1e-5 or line.length-stations[-1]>1e-5:return None
        return np.array(stations),np.array(heights)


def upper_profile(stations,heights):
    """Least concave piecewise-linear majorant, retaining approach elevations."""
    keep=[]
    for i in range(len(stations)):
        while len(keep)>=2:
            a,b=keep[-2:]
            left=(heights[b]-heights[a])/(stations[b]-stations[a])
            right=(heights[i]-heights[b])/(stations[i]-stations[b])
            if left>right+1e-10:break
            keep.pop()
        keep.append(i)
    return np.asarray(keep)


def approach_heights(axes,profiles):
    """Continue grades through bridge-way splits and branched bridge junctions.

    Degree-one ends are ground approaches. Junctions interpolate between them
    with inverse-length weights, constrained above their own ground elevation.
    Closed networks have no known approach: anchor their highest ground node.
    """
    nodes={};ground=[];edges=[];neighbors=defaultdict(list)
    for line,(s,z) in zip(axes,profiles):
        ends=[]
        for xy,height in [(line.coords[0],z[0]),(line.coords[-1],z[-1])]:
            key=tuple(round(v,3) for v in xy[:2])
            if key not in nodes:nodes[key]=len(ground);ground.append(float(height))
            node=nodes[key];ground[node]=max(ground[node],float(height));ends.append(node)
        a,b=ends;edges.append(ends)
        if a!=b:
            weight=1/max(line.length,1e-6)
            neighbors[a].append((b,weight));neighbors[b].append((a,weight))
    result=np.array(ground);unseen=set(range(len(ground)))
    while unseen:
        first=min(unseen);component={first};todo=[first];unseen.remove(first)
        while todo:
            for other,_ in neighbors[todo.pop()]:
                if other in unseen:unseen.remove(other);component.add(other);todo.append(other)
        fixed={n for n in component if len(neighbors[n])<=1}
        if not fixed:fixed={max(component,key=lambda n:ground[n])}
        # Typical connected spans have only two ends. Bound exceptional road
        # networks rather than allocating an unbounded dense linear system.
        if len(component)>256:
            free=sorted(component-fixed)
            for _ in range(1000):
                change=0.
                for n in free:
                    value=max(ground[n],sum(result[j]*w for j,w in neighbors[n])/sum(w for _,w in neighbors[n]))
                    change=max(change,abs(value-result[n]));result[n]=value
                if change<1e-4:break
            continue
        while len(fixed)<len(component):
            free=sorted(component-fixed);lookup={n:i for i,n in enumerate(free)}
            matrix=np.zeros((len(free),len(free)));rhs=np.zeros(len(free))
            for n,i in lookup.items():
                for other,weight in neighbors[n]:
                    matrix[i,i]+=weight
                    if other in lookup:matrix[i,lookup[other]]-=weight
                    else:rhs[i]+=weight*result[other]
            values=np.linalg.solve(matrix,rhs)
            constrained={n for n,v in zip(free,values) if v<ground[n]-1e-8}
            for n,v in zip(free,values):result[n]=max(v,ground[n])
            if not constrained:break
            fixed.update(constrained)
    return [(result[a],result[b]) for a,b in edges]


class BridgeDecks:
    def __init__(self,roads_all,vertices,indices):
        self.geometry={};self.profiles=[]
        groups=defaultdict(list)
        for road in roads_all:
            if road['bridge']:
                groups[(road['cls'],road['level'])].append(road)
        ground=GroundProfiles(vertices,indices) if groups else None
        skipped=0;clearance=0.
        for roads in groups.values():
            # line_merge joins shared endpoints without connecting unrelated
            # crossings or parallel carriageways merely because they overlap.
            axes=parts(shapely.line_merge(shapely.MultiLineString([
                g for r in roads for g in parts(r['geometry'],'LineString')])),'LineString')
            valid=[];profiles=[]
            for axis in axes:
                if axis.length<1e-6:continue
                p=ground.profile(axis)
                if p is None:skipped+=1;continue
                valid.append(axis);profiles.append(p)
            if not valid:continue
            anchors=approach_heights(valid,profiles);decks=[]
            for axis,(s,z),(a,b) in zip(valid,profiles,anchors):
                support=z.copy();support[0]=a;support[-1]=b
                keep=upper_profile(s,support)
                xy=np.asarray(axis.coords)[:,:2]
                original=np.r_[0,np.linalg.norm(np.diff(xy,axis=0),axis=1).cumsum()]
                stations=np.unique(np.r_[original,s[keep]])
                xyz=np.column_stack([shapely.get_coordinates(shapely.line_interpolate_point(axis,stations)),
                                     np.interp(stations,s[keep],support[keep])])
                deck=shapely.LineString(xyz);decks.append(deck)
                gap=np.interp(s,s[keep],support[keep])-z
                clearance=max(clearance,float(gap.max()))
                self.profiles.append(dict(geometry=deck,ground_stations=s,ground_heights=z))
            index=STRtree(valid)
            for road in roads:
                pieces=[]
                for i in sorted(index.query(road['geometry'],predicate='intersects')):
                    # Intersect the original XY axis, then slice the 3D deck by
                    # distance. Re-intersecting resampled deck XY can discard
                    # coincident lines because of sub-grid floating differences.
                    for piece in parts(valid[i].intersection(road['geometry']),'LineString'):
                        if piece.equals(valid[i]):pieces.append(decks[i]);continue
                        a,b=[valid[i].project(shapely.Point(p[:2])) for p in [piece.coords[0],piece.coords[-1]]]
                        run=substring(decks[i],a,b)
                        if run.geom_type=='LineString':pieces.append(run)
                if pieces:
                    geometry=shapely.line_merge(shapely.MultiLineString(pieces))
                    # A source ID can contain several disconnected bridges. If
                    # one approach lies outside the elevation mesh, retain the
                    # entire original feature with ordinary terrain draping;
                    # never silently drop its unmodeled components.
                    if abs(geometry.length-road['geometry'].length)<=max(1e-5,road['geometry'].length*1e-8):
                        self.geometry[road_key(road)]=geometry
        self.stats=dict(method='terrain-anchored bridge grades',spans=len(self.profiles),
                        features=len(self.geometry),incomplete_spans=skipped,max_clearance_m=clearance,
                        fallback_features=sum(r['bridge'] and road_key(r) not in self.geometry for r in roads_all))


def project_deck(terrain,geometry,identity):
    """Project elevated centerlines, cutting only portions behind front terrain.

    Depth difference is linear on each segment/terrain face, so crossings are
    solved analytically. The spatial index avoids all-segments × all-faces work.
    """
    if not hasattr(terrain,'_bridge_view_index'):
        terrain._bridge_view_index=STRtree(terrain.visible)
    result=[];before=0.
    for line in parts(geometry,'LineString'):
        world=np.asarray(line.coords).copy();world[:,2]*=terrain.exaggeration/terrain.latitude_scale
        image=world@terrain.projection.T*terrain.scale+terrain.offset
        depth=world@terrain.camera
        for a,b,da,db in zip(image[:-1],image[1:],depth[:-1],depth[1:]):
            delta=b-a;norm=delta@delta
            if norm<1e-14:continue
            segment=shapely.LineString([a,b]);before+=segment.length;hidden=[]
            for i in terrain._bridge_view_index.query(segment,predicate='intersects'):
                plane=terrain.depth_planes[i]
                for piece in parts(segment.intersection(terrain.visible[i]),'LineString'):
                    xy=np.asarray(piece.coords)[[0,-1]]
                    t=np.clip((xy-a)@delta/norm,0,1)
                    difference=da+t*(db-da)-(xy@plane[:2]+plane[2])+1e-6
                    if (difference>=0).all():continue
                    if (difference<0).all():hidden.append(piece);continue
                    cross=xy[0]+(xy[1]-xy[0])*difference[0]/(difference[0]-difference[1])
                    hidden.append(shapely.LineString([xy[0],cross] if difference[0]<0 else [cross,xy[1]]))
            result.extend(parts(segment.difference(shapely.union_all(hidden)),'LineString'))
    united=shapely.line_merge(shapely.union_all(shapely.set_precision(result,1e-5)))
    terrain.audit_lines.append(dict(source=identity,projected_length_pt=before,visible_length_pt=united.length,bridge_deck=True))
    return united
