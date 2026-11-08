"""View-dependent elevation sampling and a conforming, adaptive heightfield.

Coverage and detail are independent. A quadtree retains coarse remote terrain,
refines potential page coverage using real elevation samples, and stitches all
shared edge vertices. Visibility uses projected spatial neighbors, never a
centroid depth sort or a growing union of the entire mesh.
"""
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import heapq
import io
import math

import numpy as np
from PIL import Image
import shapely
from shapely.strtree import STRtree

from . import sources
from .visibility import visible_faces
from .mesh_union import mesh_union
from .geography import WORLD, HALF, origin, latitude_factor, framing, projection
from .model import MM, layout
from terrain_geometry import Terrain
from build_map import parts


def plane_coefficients(xy,values):
    a=xy[:,1]-xy[:,0];b=xy[:,2]-xy[:,0];det=a[:,0]*b[:,1]-a[:,1]*b[:,0]
    safe=np.where(np.abs(det)>1e-15,det,1)
    va=values[:,1]-values[:,0];vb=values[:,2]-values[:,0]
    dx=(va*b[:,1]-vb*a[:,1])/safe;dy=(vb*a[:,0]-va*b[:,0])/safe
    return np.column_stack([dx,dy,values[:,0]-dx*xy[:,0,0]-dy*xy[:,0,1]])


class ElevationSampler:
    def __init__(self, recipe, cache, progress, limit):
        self.origin=origin(recipe);self.cache=cache;self.progress=progress
        self.arrays={};self.inputs={};self.limit=limit

    def sample(self, points, zoom):
        points=np.asarray(points)+self.origin
        pixel=WORLD/(256*2**zoom)
        px=(points[:,0]+HALF)/pixel-.5;py=(HALF-points[:,1])/pixel-.5
        ix=np.floor(px).astype(int);iy=np.floor(py).astype(int);u=px-ix;v=py-iy
        wanted=set()
        for dx,dy in [(0,0),(1,0),(0,1),(1,1)]:
            wanted.update((zoom,int(x)%(2**zoom),int(y)) for x,y in zip(
                (ix+dx)//256,np.clip(iy+dy,0,256*2**zoom-1)//256))
        missing=sorted(wanted-self.arrays.keys())
        if len(self.arrays)+len(missing)>self.limit:
            raise ValueError('Terrain source budget exceeded. Reduce the extent or camera exaggeration.')
        def read(key):
            z,x,y=key
            raw,meta=sources.fetch(f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',self.cache)
            with Image.open(io.BytesIO(raw)) as image:a=np.asarray(image.convert('RGB'),dtype=float)
            return key,a[:,:,0]*256+a[:,:,1]+a[:,:,2]/256-32768,meta
        with ThreadPoolExecutor(max_workers=4) as pool:
            for count,(key,a,meta) in enumerate(pool.map(read,missing),1):
                self.arrays[key]=a;self.inputs[key]=meta
                self.progress('visibility','Loading detailed elevation',count,len(missing),'tiles')
        def values(dx,dy):
            gx=ix+dx;gy=np.clip(iy+dy,0,256*2**zoom-1);out=np.empty(len(points))
            tx=(gx//256)%(2**zoom);ty=gy//256
            for z,x,y in wanted:
                mask=(tx==x)&(ty==y)
                out[mask]=self.arrays[(z,x,y)][gy[mask]%256,gx[mask]%256]
            return out
        return ((1-u)*(1-v)*values(0,0)+u*(1-v)*values(1,0)+
                (1-u)*v*values(0,1)+u*v*values(1,1))


def adaptive_mesh(recipe, domain, coarse, sample, quality='draft', progress=lambda *args:None):
    """Build a crack-free mesh from sampled curvature and projected cell size.

    Integer quadtree coordinates make shared vertices exact. Every leaf uses
    a center fan containing all neighboring edge vertices, including hanging
    vertices from finer leaves. Heights at a shared vertex use its finest
    requested source level. Budgets and achieved residuals remain explicit.
    """
    xs,ys,z,_=coarse
    factor=latitude_factor(recipe);exaggeration=recipe['camera']['exaggeration']
    scale,_=framing(recipe);matrix=projection(recipe)
    target_mm=.8 if quality=='draft' else .25
    edge_mm=6 if quality=='draft' else 3
    leaf_limit=4000 if quality=='draft' else 12000
    # Fine samples must resolve the desired ground detail, independent of the
    # full coverage rectangle. Source pixel spacing is at most 1 mm / 0.4 mm.
    source_step=(1 if quality=='draft' else .4)*MM/scale
    target_zoom=max(0,min(14,math.ceil(math.log2(WORLD/(256*source_step)))))
    center_height=float(sample(np.array([[0.,0.]]),target_zoom)[0])
    _,offset=framing(recipe,center_height/factor)
    frame=np.array(layout(recipe)['frame_mm'])*MM
    halo=20*MM
    frame=frame+[-halo,-halo,halo,halo]
    x0,y0,x1,y1=domain.bounds;unit=np.array([x1-x0,y1-y0])/32768
    low=np.array([x0,y0]);heights={};leaves={};heap=[]

    def keys(cell):
        x,y,s=cell;h=s//2
        return [(x,y),(x+s,y),(x+s,y+s),(x,y+s),
                (x+h,y),(x+s,y+h),(x+h,y+s),(x,y+h),(x+h,y+h)]

    def coarse_values(points):
        i=np.clip(np.searchsorted(xs,points[:,0])-1,0,len(xs)-2)
        j=np.clip(np.searchsorted(ys,points[:,1])-1,0,len(ys)-2)
        u=(points[:,0]-xs[i])/(xs[i+1]-xs[i]);v=(points[:,1]-ys[j])/(ys[j+1]-ys[j])
        return (1-u)*(1-v)*z[j,i]+u*(1-v)*z[j,i+1]+u*v*z[j+1,i+1]+(1-u)*v*z[j+1,i]

    def evaluate(cells):
        # Initially test against the coarse surface. A generous page halo
        # admits silhouettes that move as true fine elevations arrive.
        all_keys=sorted({k for c in cells for k in keys(c)})
        missing=[k for k in all_keys if k not in heights]
        if missing:
            for k,h in zip(missing,coarse_values(low+np.array(missing)*unit)):heights[k]=(float(h),-1)
        relevant=[]
        for cell in cells:
            kk=keys(cell);h=np.array([heights[k][0] for k in kk])
            pp=np.column_stack([low+np.array(kk)*unit,h/factor*exaggeration])@matrix.T*scale+offset
            lo,hi=pp.min(axis=0),pp.max(axis=0)
            if hi[0]>=frame[0] and lo[0]<=frame[2] and hi[1]>=frame[1] and lo[1]<=frame[3]:relevant.append(cell)
        if relevant:
            size=max(unit)*relevant[0][2]
            zoom=min(target_zoom,max(0,math.ceil(math.log2(WORLD/(256*max(size/8,1))))))
            wanted=sorted({k for c in relevant for k in keys(c) if heights[k][1]<zoom})
            if wanted:
                for k,h in zip(wanted,sample(low+np.array(wanted)*unit,zoom)):heights[k]=(float(h),zoom)
        relevant=set(relevant)
        for cell in cells:
            kk=keys(cell);h=np.array([heights[k][0] for k in kk])
            pp=np.column_stack([low+np.array(kk)*unit,h/factor*exaggeration])@matrix.T*scale+offset
            lo,hi=pp.min(axis=0),pp.max(axis=0)
            active=cell in relevant and hi[0]>=frame[0] and lo[0]<=frame[2] and hi[1]>=frame[1] and lo[1]<=frame[3]
            predicted=np.array([(h[0]+h[1])/2,(h[1]+h[2])/2,(h[2]+h[3])/2,(h[3]+h[0])/2,h[:4].mean()])
            error=float(np.max(np.abs(h[4:]-predicted)))*abs(matrix[1,2])*exaggeration/factor*scale/MM
            edge=float(np.max(np.linalg.norm(np.roll(pp[:4],-1,axis=0)-pp[:4],axis=1)))/MM
            score=max(error/target_mm,edge/edge_mm) if active else 0
            if cell[2]<=2 or max(unit)*cell[2]<=WORLD/(256*2**target_zoom)*2:score=0
            leaves[cell]=(score,error,active)
            if score>1:heapq.heappush(heap,(-score,cell))

    evaluate([(x,y,4096) for x in range(0,32768,4096) for y in range(0,32768,4096)])
    def reprioritize():
        """Spend the budget on exposed slopes before already-hidden valleys.

        This is a refinement priority only, never the final visibility mask.
        Hidden cells retain nonzero priority so new silhouettes can emerge.
        """
        cells=list(leaves);coords=np.array([keys(c) for c in cells])
        hh=np.array([[heights[tuple(k)][0] for k in kk] for kk in coords])
        vertices=np.concatenate([low+coords*unit,(hh/factor*exaggeration)[:,:,None]],axis=2)
        ring=[0,4,1,5,2,6,3,7];ids=np.array([[8,a,b] for a,b in zip(ring,ring[1:]+ring[:1])])
        triangles=vertices[:,ids].reshape(-1,3,3);image=triangles@matrix.T*scale+offset
        centers=image.mean(axis=1);inside=(centers[:,0]>=frame[0])&(centers[:,0]<=frame[2])&(centers[:,1]>=frame[1])&(centers[:,1]<=frame[3])
        az,el=np.radians([recipe['camera']['azimuth_deg']+180,recipe['camera']['elevation_deg']])
        camera=np.array([np.sin(az)*np.cos(el),np.cos(az)*np.cos(el),np.sin(el)])
        depth=triangles@camera;planes=plane_coefficients(image,depth)
        points=centers[inside];which=np.flatnonzero(inside)
        visible=np.zeros(len(triangles),dtype=bool)
        if len(points):
            q,t=STRtree(shapely.polygons(image)).query(shapely.points(points),predicate='intersects')
            front=np.full(len(points),-np.inf)
            np.maximum.at(front,q,np.einsum('ij,ij->i',planes[t],np.column_stack([points[q],np.ones(len(q))])))
            visible[which]=depth[which].mean(axis=1)>=front-1e-5
        weights=.02+.98*visible.reshape(-1,8).mean(axis=1)
        heap.clear()
        for cell,weight in zip(cells,weights):
            score=leaves[cell][0]
            if score>1:heapq.heappush(heap,(-score*weight,cell))
    next_priority=128
    while heap and len(leaves)+3<=leaf_limit:
        if len(leaves)>=next_priority:
            progress('visibility','Prioritizing visible mountain detail',len(leaves),leaf_limit,'cells')
            reprioritize();next_priority*=2
        # Batch cells of one size so acquisition uses the same source level.
        _,first=heapq.heappop(heap)
        if first not in leaves:continue
        selected=[first];held=[]
        while heap and len(selected)<64 and len(leaves)+3*(len(selected)+1)<=leaf_limit:
            score,cell=heapq.heappop(heap)
            if cell not in leaves:continue
            if cell[2]==first[2]:selected.append(cell)
            else:held.append((score,cell))
        for entry in held:heapq.heappush(heap,entry)
        children=[]
        for x,y,s in selected:
            del leaves[(x,y,s)];h=s//2
            children.extend([(x,y,h),(x+h,y,h),(x,y+h,h),(x+h,y+h,h)])
        evaluate(children)
        progress('visibility','Refining visible terrain',len(leaves),leaf_limit,'cells')

    # Stitch coarse/fine boundaries by including every edge vertex, with no
    # skirts, T-junctions or independent heights on the two sides of an edge.
    row=defaultdict(set);column=defaultdict(set)
    for cell in leaves:
        for x,y in keys(cell)[:8]:row[y].add(x);column[x].add(y)
    row={k:sorted(v) for k,v in row.items()};column={k:sorted(v) for k,v in column.items()}
    def between(values,a,b):return values[bisect_left(values,a):bisect_right(values,b)]
    nodes={};vertices=[];triangles=[];scene_cells=[]
    def vertex(key):
        if key not in nodes:
            nodes[key]=len(vertices);vertices.append([*(low+np.array(key)*unit),heights[key][0]])
        return nodes[key]
    for cell in sorted(leaves):
        x,y,s=cell
        ring=([(v,y) for v in between(row[y],x,x+s)]+[(x+s,v) for v in between(column[x+s],y,y+s)[1:]]+
              [(v,y+s) for v in between(row[y+s],x,x+s)[-2::-1]]+[(x,v) for v in between(column[x],y,y+s)[-2:0:-1]])
        ids=[vertex(k) for k in ring];center=vertex((x+s//2,y+s//2))
        scene_cells.append([x,y,s,len(triangles),len(ids)])
        triangles.extend((center,a,b) for a,b in zip(ids,ids[1:]+ids[:1]))
    active=[c for c,(_,_,a) in leaves.items() if a]
    spacing=[max(unit)*c[2]*factor for c in active]
    residual=max((e for _,e,a in leaves.values() if a),default=0)
    stats=dict(method='adaptive stitched quadtree',target_error_mm=target_mm,leaf_budget=leaf_limit,
               leaves=len(leaves),triangles=len(triangles),budget_limited=bool(heap),
               source_zoom_target=target_zoom,source_pixel_m=WORLD/(256*2**target_zoom)*factor,
               active_cell_median_m=float(np.median(spacing)) if spacing else 0,
               active_cell_max_m=max(spacing,default=0),sampled_residual_mm=residual)
    return dict(vertices=np.array(vertices),indices=np.array(triangles,dtype=int),center_height_m=center_height,
                cells=scene_cells,bounds=list(domain.bounds),stats=stats)


class MeshTerrain(Terrain):
    """Reuse draping and audits on a conforming mesh with local depth clipping."""
    def __init__(self, mesh, recipe, progress=lambda *args:None):
        self.scene_mesh=dict(vertices=mesh['vertices'].tolist(),indices=mesh['indices'].tolist(),
                             cells=mesh.get('cells',[]),bounds=mesh.get('bounds'),center_height_m=mesh['center_height_m'])
        self.source_vertices=mesh['vertices'];self.indices=mesh['indices']
        self.exaggeration=recipe['camera']['exaggeration'];factor=latitude_factor(recipe)
        self.latitude_scale=factor
        self.vertices=self.source_vertices.copy();self.vertices[:,2]*=self.exaggeration/factor
        self.z=self.source_vertices[:,2]/factor
        self.projection=projection(recipe);self.scale,self.offset=framing(recipe,mesh['center_height_m']/factor)
        az,el=np.radians([recipe['camera']['azimuth_deg']+180,recipe['camera']['elevation_deg']])
        self.camera=np.array([np.sin(az)*np.cos(el),np.cos(az)*np.cos(el),np.sin(el)])
        projected=self.vertices@self.projection.T*self.scale+self.offset
        self.triangles=self.vertices[self.indices]
        self.flat=shapely.polygons(self.triangles[:,:,:2])
        image=projected[self.indices]
        # Round shared vertices identically, then overlay at floating precision.
        # Re-snapping newly computed intersections independently can create
        # long, sub-grid slivers behind an occluder along its diagonal edge.
        self.projected=shapely.set_precision(shapely.set_precision(shapely.polygons(image),1e-7),0)
        cx=plane_coefficients(self.triangles[:,:,:2],image[:,:,0]);cy=plane_coefficients(self.triangles[:,:,:2],image[:,:,1])
        self.transforms=np.column_stack([cx[:,:2],cy[:,:2],cx[:,2],cy[:,2]])
        self.depth_planes=plane_coefficients(image,self.triangles@self.camera)
        self.index=STRtree(self.flat)
        # The horizontal camera direction orients the dual graph of this
        # conforming heightfield. A ray crosses a shared ground edge from the
        # nearer face to the farther one, regardless of elevation. Topological
        # order is exact; sorting face centroids by camera depth is not.
        neighbors=[[] for _ in self.indices];incoming=np.zeros(len(self.indices),dtype=int);edges={}
        for idx,ids in enumerate(self.indices):
            for a,b in zip(ids,np.roll(ids,-1)):
                key=tuple(sorted((int(a),int(b))))
                if key in edges:
                    other=edges.pop(key);delta=self.vertices[b,:2]-self.vertices[a,:2]
                    outward=np.array([delta[1],-delta[0]])
                    dot=outward@self.camera[:2]
                    if abs(dot)<1e-12:continue
                    near,far=(other,idx) if dot>0 else (idx,other)
                    neighbors[near].append(far);incoming[far]+=1
                else:edges[key]=idx
        queue=deque(np.flatnonzero(incoming==0));order=[]
        while queue:
            idx=queue.popleft();order.append(idx)
            for far in neighbors[idx]:
                incoming[far]-=1
                if incoming[far]==0:queue.append(far)
        if len(order)!=len(self.indices):raise ValueError('Terrain adjacency does not admit a ray traversal order')
        rank=np.empty(len(order),dtype=int);rank[order]=np.arange(len(order))
        frame=shapely.box(*(np.array(layout(recipe)['frame_mm'])*MM)).buffer(4*MM)
        self.visible,visibility_stats=visible_faces(self.projected,rank,frame,progress)
        self.outline=shapely.union_all(self.visible,grid_size=1e-7)
        self._visible_array=np.asarray(self.visible,dtype=object)
        self.audit_lines=[];self.debug_contours=[]
        self.stats=dict(mesh['stats'],**visibility_stats,
                        fully_hidden_triangles=sum(g.is_empty for g in self.visible),
                        projected_face_area_pt2=float(shapely.area(self.projected).sum()),
                        visible_face_area_pt2=sum(g.area for g in self.visible))
        progress('visibility','Detailed terrain visibility ready',visibility_stats['active_triangles'],visibility_stats['active_triangles'],'facets')

    def audit_visibility(self,samples=None):
        """Check frontmost depth independently at one point in every visible face.

        Bulk point queries are cheaper than the former small Python sampling
        loop, so normal composition no longer risks overlooking an isolated
        incorrectly exposed face. Explicit sample limits remain useful in tests.
        """
        visible=np.asarray(self.visible,dtype=object)
        ids=np.flatnonzero(shapely.area(visible)>1e-6)
        if samples is not None:
            ids=np.random.default_rng(7831).choice(ids,min(samples,len(ids)),replace=False)
        if not len(ids):return dict(samples=0,maximum_frontmost_depth_error_m=0.)
        points=shapely.point_on_surface(visible[ids])
        xy=np.column_stack([shapely.get_coordinates(points),np.ones(len(ids))])
        source,face=STRtree(self.projected).query(points,predicate='intersects')
        own=np.einsum('ij,ij->i',self.depth_planes[ids],xy)
        errors=np.einsum('ij,ij->i',self.depth_planes[face],xy[source])-own[source]
        return dict(samples=len(ids),maximum_frontmost_depth_error_m=float(np.max(errors,initial=0.)))

    def ground_for_viewport(self,frame):
        # Whole selected ground facets form an edge-matched coverage, even when
        # visibility leaves many holes and disconnected regions touching at points.
        active=shapely.intersects(self._visible_array,frame)
        return mesh_union(self.flat[active])

    def _project_pieces(self,geometry,kind,progress):
        """Index individual components, avoiding whole-forest × every-facet work."""
        components=np.asarray(parts(geometry,kind),dtype=object)
        if not len(components):return
        feature,facet=self.index.query(components,predicate='intersects')
        visible=getattr(self,'_visible_array',None)
        if visible is None:self._visible_array=visible=np.asarray(self.visible,dtype=object)
        for start in range(0,len(facet),1024):
            ids=facet[start:start+1024]
            if progress:progress('Projecting terrain '+('areas' if kind=='Polygon' else 'lines'),start,len(facet),'pieces')
            pieces=shapely.intersection(components[feature[start:start+1024]],self.flat[ids])
            clean=[];owners=[]
            for owner,piece in enumerate(pieces):
                for part in parts(piece,kind):clean.append(part);owners.append(owner)
            if not clean:continue
            pieces=np.asarray(clean,dtype=object);ids=ids[owners]
            full=shapely.equals(pieces,self.flat[ids]) if kind=='Polygon' else None
            xy,which=shapely.get_coordinates(pieces,return_index=True)
            c=self.transforms[ids[which]]
            projected=shapely.set_coordinates(pieces,np.column_stack([
                c[:,0]*xy[:,0]+c[:,1]*xy[:,1]+c[:,4],
                c[:,2]*xy[:,0]+c[:,3]*xy[:,1]+c[:,5]]))
            # An area covering a whole facet uses its canonical shared edges.
            # Independently reprojecting those vertices can leave sub-micron
            # holes that incorrectly reject otherwise-clear label backgrounds.
            if full is not None:projected[full]=self.projected[ids[full]]
            yield projected,shapely.intersection(projected,visible[ids])

    def drape_area(self,geometry,progress=None):
        result=[]
        for _,kept in self._project_pieces(geometry,'Polygon',progress):result.extend(kept)
        if progress:progress('Joining projected areas',None,None,None)
        return shapely.union_all(result,grid_size=1e-5)

    def drape_centerlines(self,geometry,identity,progress=None):
        result=[];raw=[];before=0.;after=0.
        for projected,kept in self._project_pieces(geometry,'LineString',progress):
            before+=float(shapely.length(projected).sum());after+=float(shapely.length(kept).sum())
            for piece in kept:result.extend(parts(piece,'LineString'))
            if identity.startswith('terrain/contours|'):raw.extend(projected)
        if progress:progress('Joining visible lines',None,None,None)
        self.audit_lines.append(dict(source=identity,projected_length_pt=before,visible_length_pt=after))
        united=shapely.union_all(shapely.set_precision(result,1e-5))
        if united.geom_type=='MultiLineString':united=shapely.line_merge(united)
        if raw:self.debug_contours.append((shapely.union_all(raw),united))
        return united
