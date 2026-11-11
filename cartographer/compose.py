"""Compose opaque vector maps using the proven GEOS/MuPDF drawing primitives."""
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
import zipfile

import numpy as np
import pymupdf
import shapely
from shapely import affinity
from shapely.geometry import LineString, box, shape

from . import ROOT
from .progress import Progress
from .build_cache import BuildCache, fingerprint
from .model import MM, ROLES, canonical, digest, layout, validate_recipe, furniture_options
from .furniture import furniture, furniture_plan, furniture_occupied
from .geography import projection, framing, footprint, origin, latitude_factor, geo_geometry, bounds_for, point
from .sources import cached, dataset, elevations, source_version, MAJOR, plan
from .contours import contours, elevation_grid as flat_elevations
from .terrain import ElevationSampler, adaptive_mesh, MeshTerrain
from .bridges import BridgeDecks, grid_mesh, road_key, project_deck
from .terrain_marks import silhouette_lines, shade_regions, grid_shade_regions, hatch_rows
from .area_marks import concentric_rings
from .area_geometry import valid_area
from build_map import BLACK, WHITE, parts
from regional_map import Drawing, clip_stream
from terrain_geometry import Terrain, stroke_clip
from print_contract import validate_intent


def road_widths(road_class,pen_mm):
    """Page-space road symbols with edges a physical pen can draw.

    White centers retain the road-class hierarchy. Each colored edge gets a
    pen diameter plus 0.06 mm for curve flattening and HPGL grid clearance on
    both sides. Unknown pens keep the provisional symbol; export still requires
    a measured diameter. Solid paths use at least the assigned pen width.
    """
    core={'motorway':2.8,'trunk':2.2,'primary':1.7,'secondary':1.3,'tertiary':1,'minor':.7,'residential':.7}.get(road_class,0)
    edge=max(.4,(pen_mm+.06)*MM) if pen_mm is not None else .4
    return core,core+2*edge if core else max(.6,(pen_mm or 0)*MM)


def road_edges(line,core,outer):
    """Author each casing as a stroke, so conversion keeps one traversal.

    Offsetting in page space preserves pen width even on oblique terrain.
    Later white cores still clear intersections and occlude lower road levels.
    """
    edge=(outer-core)/2
    for side in [-1,1]:
        yield from parts(line.offset_curve(side*(core+edge)/2,join_style='round'),'LineString')


def fit_recipe(recipe):
    recipe=validate_recipe(recipe)
    result=copy.deepcopy(recipe)
    if recipe['boundary']:
        result['bounds']=list(shape(recipe['boundary']['geometry']).bounds)
    w,s,e,n=result['bounds'];result['center']=[(w+e)/2,(s+n)/2]
    result['extent']='bounds'
    domain=footprint(result)
    raw=np.array([[x,y,0] for x,y in domain.exterior.coords]) @ projection(result).T
    low,high=raw.min(axis=0),raw.max(axis=0)
    frame=layout(result)['frame_mm']
    scale=min((frame[2]-frame[0]-4)/(high[0]-low[0]),(frame[3]-frame[1]-4)/(high[1]-low[1]))
    result['framing']['scale_denominator']=max(500,min(100000000,1000*latitude_factor(result)/scale))
    result['framing']['offset_mm']=(-(low+high)/2*scale).tolist()
    result['extent']='viewport'
    return result


def triangle_contours(triangles,interval):
    levels=np.arange(math.floor(triangles[:,:,2].min()/interval)*interval,triangles[:,:,2].max(),interval)
    if len(levels)>400:raise ValueError('More than 400 contour levels. Increase the contour interval.')
    for level in levels:
        selected=triangles[(triangles[:,:,2].min(axis=1)<level)&(triangles[:,:,2].max(axis=1)>level)]
        pieces=[]
        for tri in selected:
            points=[]
            for a,b in [(tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])]:
                if a[2]<level<=b[2] or b[2]<level<=a[2]:points.append(a[:2]+(level-a[2])/(b[2]-a[2])*(b[:2]-a[:2]))
            if len(points)==2 and np.linalg.norm(points[1]-points[0])>1e-8:pieces.append(LineString(points))
        yield float(level),shapely.line_merge(shapely.union_all(pieces))


def scene_data(data,recipe,domain,elevation,decks=None):
    lines=[]
    scale,_=framing(recipe)
    for feature in data['areas']+data['roads']+data.get('streams',[]):
        g=feature['geometry'];kind=feature['kind'] if 'kind' in feature else 'roads'
        deck=decks.geometry.get(road_key(feature)) if decks and kind=='roads' and feature.get('bridge') else None
        if deck is not None:g=deck
        if g.geom_type in ('Polygon','MultiPolygon'):g=g.boundary
        for line in parts(g if deck is not None else g.simplify(.6/scale),'LineString'):
            lines.append(dict(kind='water' if kind=='streams' else kind,points=list(line.coords),
                              cls=feature.get('cls'),bridge=feature.get('bridge',False),area_m2=feature['geometry'].area))
    result=dict(lines=lines,origin=origin(recipe).tolist(),bounds=list(domain.bounds),source=data['source'],
                warnings=data.get('warnings',[]),terrain=None)
    if elevation:
        xs,ys,z,_=elevation;result['terrain']=dict(xs=xs.tolist(),ys=ys.tolist(),z=z.tolist())
    return result


class InkDrawing(Drawing):
    """Keep ink identity in OCG names, independently of RGB and source feature ID."""
    def __init__(self, recipe):
        super().__init__(*(np.array(recipe['media']['sheet_size_mm'])*MM))
        self.recipe = recipe
        self.inks = {i['id']: i for i in recipe['palette']['inks']}
        self.bindings = {}
        self.properties = {}
        self.active_property = None
        self.active_ink = recipe['ink_roles']['labels']
        self.region = ''
        self.style = recipe
        self.streams = []
        self.current_mask = None
        self.defer_path_colors = False

    def commit(self, mask=None):
        # MuPDF 2.0 Shape.commit reparses ALL preceding paint for q/Q balance.
        # Thousands of independent terrain cuts would make that quadratic.
        # Shape.finish/insert_text already emit balanced groups; batch those
        # engine-generated streams and preserve their order and resources.
        content = (self.canvas.totalcont+self.canvas.text_cont).encode()
        if mask is not self.current_mask:
            if self.current_mask is not None:
                self.streams.append(b'\nQ')
            if mask is not None:
                self.streams.append(clip_stream(mask, self.height))
            self.current_mask = mask
        if content:
            self.streams.append(content)
        self.canvas = self.page.new_shape()

    def flush(self):
        self.commit()
        self.ink_usage()  # Validate fresh and replayed paint before publishing.
        # Register OCG resources once. MuPDF's per-mark OCG lookup scans the
        # full resource dictionary; thousands of feature groups make it O(n²).
        resources = int(self.doc.xref_get_key(self.page.xref, 'Resources')[1].split()[0])
        self.doc.xref_set_key(resources, 'Properties', '<< '+' '.join(
            f'/{name} {xref} 0 R' for name, xref in self.properties.items())+' >>')
        xref = self.doc.get_new_xref()
        self.doc.update_object(xref, '<< >>')
        self.doc.update_stream(xref, b'\n'.join(self.streams))
        self.page.set_contents(xref)

    def role_name(self, category, style=None):
        style = self.style if style is None else style
        root = category.split('/')[0]
        if category.startswith('terrain/hillshade'):root='hillshade'
        if category.startswith('terrain/silhouette'):root='silhouettes'
        if root == 'legend': root = category.split('/')[-1]
        if root not in ROLES or root not in style['ink_roles']:
            raise ValueError(f'Drawing category {category!r} has no ink role')
        if root == 'bridges' and not style['features']['bridges']: root = 'roads'
        return root

    def role(self, category):
        return self.style['ink_roles'][self.role_name(category)]

    def ink_usage(self):
        """Every PDF layer, including cache replay, must resolve through a role."""
        used = {}
        for binding in self.bindings.values():
            category = binding['feature_group']
            region = 'outside' if category.startswith('outside/') else 'inside'
            style = self.recipe['outside'] if region == 'outside' else self.recipe
            role = self.role_name(category.removeprefix('outside/'), style)
            ink_id = binding['ink_id']
            if ink_id != style['ink_roles'][role] or ink_id not in self.inks:
                raise ValueError(f'Drawing category {category!r} does not match its {role} ink role')
            use = used.setdefault(ink_id, dict(id=ink_id, label=self.inks[ink_id]['label'], roles=[]))
            assignment = dict(region=region, role=role)
            if assignment not in use['roles']: use['roles'].append(assignment)
        return list(used.values())

    def color(self, identity):
        h = self.inks[identity]['color_srgb'].lstrip('#')
        return tuple(int(h[i:i+2], 16)/255 for i in (0, 2, 4))

    def color_code(self, identity, operator):
        # Use the engine's own numeric formatting for identical cached/fresh PDF.
        return pymupdf.ColorCode(self.color(identity), 'c' if operator == b'RG' else 'f').encode()

    def rebind_layer(self, key, binding):
        category = binding['feature_group']
        style = self.recipe['outside'] if category.startswith('outside/') else self.recipe
        identity = style['ink_roles'][self.role_name(category.removeprefix('outside/'), style)]
        key = key.rsplit('|ink=', 1)[0]+'|ink='+identity
        return key, dict(binding, ocg_name=key, ink_id=identity)

    def layer(self, category, identity, name=''):
        if self.active_ink != self.role(category):
            raise ValueError(f'Drawing category {category!r} has no active role binding')
        category = self.region+category
        key = '|'.join([category, str(identity), name.replace('|', '/'), 'ink='+self.active_ink])
        if key not in self.layers:
            self.layers[key] = self.doc.add_ocg(key)
            self.bindings[key] = dict(ocg_name=key, ink_id=self.active_ink, feature_group=category)
            self.properties[f'PL{self.layers[key]}'] = self.layers[key]
        self.active_property = f'PL{self.layers[key]}'
        return 0  # The caller wraps the engine-generated paint in this group.

    def bind_stream(self, attribute, before):
        stream = getattr(self.canvas, attribute)
        emitted = stream[before:]
        if emitted:
            setattr(self.canvas, attribute, stream[:before]+f'/OC /{self.active_property} BDC\n'+emitted+'\nEMC\n')

    def paint(self, geometry, category, identity, color=BLACK, width=.65, fill=None, name='', dashes=None):
        self.active_ink = self.role(category)
        def mapped(value):
            return value if value is None or tuple(value) == WHITE else self.color(self.active_ink)
        before = len(self.canvas.totalcont)
        super().paint(geometry, category, identity, mapped(color), width, mapped(fill), name, dashes)
        if self.defer_path_colors and len(self.canvas.totalcont) > before:
            # These are engine-generated path streams, never arbitrary PDF.
            # Replace only semantic ink operators: literal white knockouts
            # remain white even when a document ink itself has a white swatch.
            emitted = self.canvas.totalcont[before:]
            for value, operator in ((color if width else None, b'RG'), (fill, b'rg')):
                if value is None or tuple(value) == WHITE: continue
                code = self.color_code(self.active_ink, operator).decode()
                if emitted.count(code) != 1:
                    raise ValueError('Unexpected engine path color encoding')
                emitted = emitted.replace(code, f'%PLINK /{self.active_property} {operator.decode()}\n')
            self.canvas.totalcont = self.canvas.totalcont[:before]+emitted
        self.bind_stream('totalcont', before)

    def text(self, text, p, size=10, font='sans', color=BLACK, category='labels/furniture', identity='map', angle=0):
        self.active_ink = self.role(category)
        before = len(self.canvas.text_cont)
        super().text(text, p, size, font, self.color(self.active_ink), category, identity, angle)
        self.bind_stream('text_cont', before)

    def halo_text(self,text,p,size=10,font='sans',identity='map',halo_width=2.5):
        self.active_ink=self.role('labels/furniture')
        before=len(self.canvas.text_cont)
        self.canvas.insert_text(p,text,fontsize=size,fontname=font,color=WHITE,fill=WHITE,
                                render_mode=2,border_width=halo_width/size,
                                oc=self.layer('labels/furniture/background',identity,text))
        self.bind_stream('text_cont',before)
        self.text(text,p,size,font,identity=identity)


def hatches(geometry, spacing, progress=None):
    """Continuous crosshatch lines, clipped on a ground-anchored lattice before projection."""
    if geometry.is_empty:return shapely.GeometryCollection()
    output = []
    for direction,angle in enumerate([-45,45],1):
        def report(detail,completed=None,total=None,unit=None):
            if progress:progress(f'Generating crosshatching · direction {direction} of 2',completed,total,unit)
        # Share hillshading's narrow-strip clipping and rotation repair. Each
        # carrier keeps its original position and is clipped as a complete row.
        for row in hatch_rows(geometry,spacing,angle,report if progress else None):
            output.extend(parts(row,'LineString'))
    # Crossing hatch directions need no planar network. Union would split both
    # strokes at every crossing, multiplying PDF paths and downstream pen lifts.
    return shapely.MultiLineString(output) if output else shapely.GeometryCollection()


def compose(recipe, output, quality='proof', cache=None, version=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with Progress(output/'progress.json') as progress:
        return _compose(recipe,output,quality,cache,version,progress)


def _compose(recipe,output,quality,cache,version,progress):
    started=time.monotonic();recipe=validate_recipe(recipe)
    progress('planning','Calculating map coverage and detail')
    cache=Path(cache or ROOT/'build/cartographer/geometry');version=version or source_version()
    build_cache=BuildCache(cache,version,progress)
    page_layout=layout(recipe);frame=box(*(np.array(page_layout['frame_mm'])*MM))
    domain=footprint(recipe);plan(recipe,domain,quality)
    elevation=None;flat_elevation=None;terrain_source=None;terrain=None;audit=None
    elevation_inputs={}
    def load_elevation(domain):
        result=elevations(recipe,domain,cache,quality,progress=progress)
        elevation_inputs.update({s['cache_key']:s for s in result[3].get('tiles',[])})
        return result
    styles=[recipe]+([recipe['outside']] if recipe['boundary'] else [])
    need_shade=any(s['features']['hillshade'] for s in styles)
    need_elevation=recipe['camera']['relief'] or any(s['features']['contours'] for s in styles) or need_shade
    if need_elevation:
        elevation=load_elevation(domain)
        # Expand the inverse viewport by the actual elevation range; fetch again
        # until the displacement is covered, so oblique views fill the paper.
        if recipe['camera']['relief'] and recipe['extent']=='viewport':
            padding=0
            for _ in range(4):
                z=elevation[2];required=max(float(z.max()-z.min()),1)
                if required<=padding:break
                padding=required*1.25;domain=footprint(recipe,padding)
                elevation=load_elevation(domain)
            else:
                raise ValueError('Terrain extent did not stabilize. Raise the camera or reduce exaggeration.')
        xs,ys,z,terrain_source=elevation
        if not recipe['camera']['relief']:
            flat_elevation=build_cache.node('Flat terrain elevation',
                [recipe['center'],list(domain.bounds),recipe['framing']['scale_denominator'],quality],
                lambda:flat_elevations(recipe,domain,cache,quality,progress),'elevation')
            detail=flat_elevation[3]
            elevation_inputs.update({s['cache_key']:s for s in detail['tiles']})
            terrain_source=dict(terrain_source,tiles=list(elevation_inputs.values()))
            for feature in ['contours','hillshade']:
                if any(s['features'][feature] for s in styles):
                    terrain_source[feature]={k:v for k,v in detail.items() if k!='tiles'}
            key=fingerprint(flat_elevation)
    data=dataset(recipe,domain,cache,quality,progress=progress)
    progress('sketch','Preparing interactive geometry')
    from .storage import atomic_json
    decks=None
    if elevation and recipe['camera']['relief']:
        decks=BridgeDecks(data['roads'],*grid_mesh(elevation))
    sketch=scene_data(data,recipe,domain,elevation,decks)
    atomic_json(output/'scene.json',sketch)
    center_height=0
    if elevation and recipe['camera']['relief']:
        xs,ys,z,_=elevation
        center_height=float(np.interp(0,ys,[np.interp(0,xs,row) for row in z]))/latitude_factor(recipe)
    scale,offset=framing(recipe,center_height)
    matrix=projection(recipe)
    flat=[scale*matrix[0,0],scale*matrix[0,1],scale*matrix[1,0],scale*matrix[1,1],*offset]
    if recipe['camera']['relief']:
        progress('visibility','Refining terrain for the visible page')
        progress.advance('visibility',0)
        key=digest([version,'adaptive-terrain-v1',recipe['center'],list(domain.bounds),recipe['camera'],recipe['framing'],page_layout,quality,terrain_source])
        detail_written=False
        def write_detail(mesh):
            nonlocal decks,detail_written
            if recipe['camera']['relief']:
                bridges=[r for r in data['roads'] if r['bridge']]
                decks=build_cache.node('Bridge deck profiles',[key,bridges],
                                      lambda:BridgeDecks(bridges,mesh['vertices'],mesh['indices']),'visibility')
            atomic_json(output/'scene-detail.json',dict(scene_data(data,recipe,domain,elevation,decks),terrain_mesh=dict(
                vertices=np.asarray(mesh['vertices']).tolist(),indices=np.asarray(mesh['indices']).tolist(),
                cells=mesh.get('cells',[]),bounds=mesh.get('bounds'),center_height_m=mesh['center_height_m'])))
            detail_written=True
        def build_terrain():
            def refine_progress(stage,detail,completed=None,total=None,unit=None):
                if total and unit=='cells':progress.advance('visibility',.45*completed/total)
                progress(stage,detail,completed,total,unit)
            def visibility_progress(stage,detail,completed=None,total=None,unit=None):
                if total:progress.advance('visibility',.5+.45*completed/total)
                progress(stage,detail,completed,total,unit)
            sampler=ElevationSampler(recipe,cache,refine_progress,256 if quality=='draft' else 768)
            mesh=adaptive_mesh(recipe,domain,elevation,sampler.sample,quality,refine_progress)
            write_detail(mesh)
            progress.advance('visibility',.5)
            source=dict(terrain_source,adaptive=mesh['stats'],tiles=list({**elevation_inputs,
                        **{s['cache_key']:s for s in sampler.inputs.values()}}.values()))
            return MeshTerrain(mesh,recipe,visibility_progress),source
        terrain,terrain_source=cached(cache,key,build_terrain,
                         on_wait=lambda:progress('visibility','Waiting for shared terrain geometry'))
        if not detail_written:write_detail(terrain.scene_mesh)
        if terrain:
            terrain.audit_lines=[];terrain.debug_contours=[]
            progress.advance('visibility',.95)
            progress('visibility','Checking terrain visibility')
            audit=build_cache.node('Terrain depth audit',[key,quality],lambda:terrain.audit_visibility(),'visibility')
            if audit['maximum_frontmost_depth_error_m']>1e-3:raise ValueError('Terrain depth audit failed')
            if recipe['extent']=='viewport' and frame.difference(terrain.outline.buffer(.01)).area>.05:
                raise ValueError('The terrain mesh does not fill the viewport. Raise the camera or reduce exaggeration.')
    surface_stats=terrain.stats if terrain else None
    progress('drawing','Preparing map features')
    draw=InkDrawing(recipe);counts=Counter();omissions=[]
    ornaments=furniture_plan(draw,recipe,data['source']['attribution'],bool(elevation))
    draw.occupied.extend(furniture_occupied(draw,ornaments))
    simplify=(.5 if quality=='draft' else .08)/scale
    def prepare_areas():
        areas=[];geometry_repairs=[]
        for a in data['areas']:
            g=valid_area(a['geometry']).intersection(domain)
            g=valid_area(g).simplify(simplify,preserve_topology=True)
            if not g.is_valid:
                geometry_repairs.append(dict(feature=a['id'],operation='area simplification',
                                             reason=shapely.is_valid_reason(g)))
                g=valid_area(g)
            if not g.is_empty:areas.append(dict(a,geometry=g))
        return areas,geometry_repairs
    areas,geometry_repairs=build_cache.node('Clipped map areas',[data['areas'],domain,simplify],prepare_areas)
    water=build_cache.node('Water geometry',[a['geometry'] for a in areas if a['kind']=='water'],
                           lambda:shapely.union_all([a['geometry'] for a in areas if a['kind']=='water']))
    land=domain.difference(water)
    shore=water.boundary.difference(domain.boundary.buffer(1e-5)) if not water.is_empty else shapely.GeometryCollection()
    # Fetch coverage includes the hills that can occlude the page. Styling all
    # that off-page ground (hundreds of km at low camera elevations) is wasted
    # work. Keep whole visible facets and a page halo for finite-width marks.
    drawing_domain=terrain.ground_for_viewport(frame.buffer(4*MM)) if terrain else domain
    def drawing_progress(*args):progress('drawing',*args)
    view_key=fingerprint([key if terrain else flat,domain,drawing_domain,recipe['media']['sheet_size_mm'],quality])
    def terrain_edges():
        return build_cache.node('Visible mountain edges',[view_key],lambda:silhouette_lines(terrain,drawing_progress))
    def shaded_land():
        def compute():
            if terrain:
                shade=shade_regions(terrain.source_vertices,terrain.indices,recipe['lighting'],
                                    latitude_factor(recipe),recipe['camera']['exaggeration'],drawing_progress,
                                    triangle_ids=np.sort(terrain.index.query(drawing_domain,predicate='intersects')))
            else:
                shade=grid_shade_regions(*flat_elevation[:3],recipe['lighting'],latitude_factor(recipe),drawing_progress)
            return [g.intersection(land).intersection(drawing_domain) for g in shade]
        return build_cache.node('Slope illumination',[key,recipe['lighting'],land,drawing_domain],compute)
    def area_project(g):return terrain.drape_area(g.intersection(drawing_domain),drawing_progress) if terrain else affinity.affine_transform(g,flat)
    def projected_runs(g,width,identity,in_drawing_domain=False):
        if terrain:
            return parts(terrain.drape_centerlines(g if in_drawing_domain else g.intersection(drawing_domain),identity,
                         drawing_progress if g.geom_type!='LineString' else None),'LineString')
        return parts(affinity.affine_transform(g,flat),'LineString')
    boundary=geo_geometry(shape(recipe['boundary']['geometry']),recipe).intersection(domain) if recipe['boundary'] else None
    # Rejoin projected facet edges on the same sub-micron page grid. Tiny
    # floating-point seams must not turn an interior label into an edge label.
    inside=build_cache.node('Projected boundary',[view_key,boundary,frame],lambda:shapely.union_all(shapely.get_parts(shapely.set_precision(area_project(boundary),1e-5)),grid_size=1e-5).intersection(frame)) if boundary is not None else frame
    regions=[('',recipe,inside)]
    if boundary is not None:regions.insert(0,('outside/',recipe['outside'],frame.difference(inside)))
    for region_index,(prefix,rules,mask) in enumerate(regions):
        if mask.is_empty or mask.area<1e-5:continue
        f=rules['features'];draw.region=prefix;draw.style=rules
        region='Outside boundary: ' if prefix else 'Inside boundary: ' if boundary is not None else ''
        def drawing_part(start,end,fraction=0):
            progress.advance('drawing',(region_index+start+(end-start)*fraction)/len(regions))
        def area(g,category,identity,fill=None,name=''):
            if g.is_empty:return
            draw.paint(area_project(g),category,identity,color=None,width=0,fill=fill,name=name)
            draw.commit(mask);counts[prefix+category]+=1
        def line(g,category,identity,width=.65,*,in_drawing_domain=False):
            if g.is_empty:return
            lines=projected_runs(g,width,prefix+category+'|'+str(identity),in_drawing_domain)
            for index,run in enumerate(lines):
                if len(lines)>64 and index%64==0:
                    progress('drawing',region+'Drawing '+{'parks':'park hatching and outlines','terrain':'contours','water':'waterways and shorelines'}.get(category.split('/')[0],category.split('/')[0]),index,len(lines),'lines')
                draw.paint(run,category,identity,width=width,name=str(identity))
                draw.commit(stroke_clip(run,width).intersection(mask) if terrain else mask)
            counts[prefix+category]+=bool(lines)
        def paint_stage(name,features,inputs,build):
            settings={k:f[k] for k in features}
            # Colors and logical ink IDs are bound at replay. Geometry-affecting
            # widths (road casings) are already explicit in the input paths.
            build_cache.paint(draw,counts,region+name,[view_key,mask_key,prefix,settings,inputs],build)
        mask_key=fingerprint(mask)
        drawing_part(0,.04)
        def concentric(g,category,identity,spacing,advance):
            def ring_progress(detail,completed,total,unit):
                if total:advance(completed/total)
                drawing_progress(detail,completed,total,unit)
            for index,rings in concentric_rings(g,spacing*MM/scale,domain,ring_progress,viewport=drawing_domain):
                line(rings,category,f'{identity}/{index}',in_drawing_domain=True)
        def build_water():
            if f['water_treatment']=='fill':area(water,'water/fill','water',fill=BLACK)
            elif f['water_treatment']=='hatch':
                line(hatches(water.intersection(drawing_domain),f['water_spacing_mm']*MM/scale,drawing_progress),'water/explicit_hatch','water')
            elif f['water_treatment']=='concentric':
                concentric(water,'water/concentric','water',f['water_spacing_mm'],lambda fraction:drawing_part(0,.04,fraction))
        paint_stage('Water fill',['water_treatment','water_spacing_mm'],water,build_water)
        parks=[a for a in areas if a['kind']=='parks' and a['geometry'].area*(scale/MM)**2>=2 and a['geometry'].intersects(drawing_domain)] if f['parks'] else []
        drawing_part(.04,.39)
        def build_forest():
            for index,a in enumerate(parks):
                drawing_part(.04,.39,index/len(parks))
                progress('drawing',region+'Drawing parks and hatching',index,len(parks),'parks')
                g=shapely.union_all(parts(a['geometry'].intersection(land),'Polygon'))
                if g.is_empty:continue
                area(g,'parks/background',a['id'],fill=BLACK if f['park_treatment']=='fill' else WHITE,name=a['name'])
                if f['park_treatment']=='hatch':line(hatches(g.intersection(drawing_domain),f['hatch_spacing_mm']*MM/scale,drawing_progress),'parks/explicit_hatch',a['id'])
                elif f['park_treatment']=='concentric':
                    concentric(g,'parks/concentric',a['id'],f['hatch_spacing_mm'],lambda fraction:drawing_part(.04,.39,(index+fraction)/len(parks)))
                line(g.boundary,'parks/boundary',a['id'])
        paint_stage('Forest paint',['park_treatment', 'hatch_spacing_mm'],[parks,water],build_forest)
        drawing_part(.39,.44)
        def build_hillshade():
            if f['hillshade']:
                for direction,(angle,g) in enumerate(zip([-45,45],shaded_land())):
                    def shade_progress(detail,completed=None,total=None,unit=None):
                        if total:drawing_part(.39,.44,(direction+completed/total)/2)
                        drawing_progress(detail,completed,total,unit)
                    for index,row in enumerate(hatch_rows(g,f['hillshade_spacing_mm']*MM/scale,angle,shade_progress)):
                        # shaded_land already clips the hatch source to drawing_domain.
                        line(row,'terrain/hillshade',f'{direction}/{index}',.55,in_drawing_domain=True)
        paint_stage('Hillshade paint',['hillshade', 'hillshade_spacing_mm'],[key if need_shade else None,recipe['lighting'],land],build_hillshade)
        # Forest backgrounds clear paper before their hatch/fill. Contours must
        # follow all land-cover paint, but remain below buildings, roads and labels.
        drawing_part(.44,.48)
        def build_contour():
            if f['contours'] and elevation:
                progress('drawing',region+'Drawing terrain contours')
                contour_z=terrain.source_vertices[:,2] if terrain else flat_elevation[2]
                contour_total=math.ceil(float(contour_z.max())/f['contour_interval_m'])-math.floor(float(contour_z.min())/f['contour_interval_m'])
                contour_lines=triangle_contours(terrain.source_vertices[terrain.indices],f['contour_interval_m']) if terrain else contours(*flat_elevation[:3],f['contour_interval_m'])
                for index,(level,g) in enumerate(contour_lines):
                    drawing_part(.44,.48,index/max(1,contour_total))
                    progress('drawing',region+f'Drawing contour at {level:g} m')
                    line(g.intersection(land),'terrain/contours',f'{level:g} m')
        paint_stage('Contour paint',['contours', 'contour_interval_m'],[key if terrain else flat_elevation,land],build_contour)
        def build_silhouette():
            if terrain and f['terrain_edges']:
                edges=terrain_edges()
                progress('drawing',region+'Drawing terrain silhouettes',0,len(edges),'lines')
                for index,run in enumerate(edges):
                    if index%128==0:progress('drawing',region+'Drawing terrain silhouettes',index,len(edges),'lines')
                    draw.paint(run,'terrain/silhouette',index,width=.8)
                    draw.commit(stroke_clip(run,.8).intersection(mask))
                counts[prefix+'terrain/silhouette']+=len(edges)
        paint_stage('Silhouette paint',['terrain_edges'],[],build_silhouette)
        buildings=[a for a in areas if a['kind']=='buildings'] if f['buildings']!='none' else []
        drawing_part(.48,.60)
        def build_building():
            for index,a in enumerate(buildings):
                drawing_part(.48,.60,index/len(buildings))
                progress('drawing',region+'Drawing buildings',index,len(buildings),'buildings')
                if a['geometry'].area*(scale/MM)**2<f['building_min_mm2']:
                    counts[prefix+'omitted/small_buildings']+=1;continue
                area(a['geometry'],'buildings/background',a['id'],fill=WHITE,name=a['name'])
                line(a['geometry'].boundary,'buildings',a['id'])
        paint_stage('Building paint',['building_min_mm2'],buildings,build_building)
        drawing_part(.60,.84)
        progress('drawing',region+'Preparing streets')
        roads=[]
        for r in data['roads'] if f['roads']!='none' else []:
            if f['roads']=='major' and r['cls'] not in MAJOR:continue
            g=r['geometry'].intersection(domain).simplify(simplify)
            if g.is_empty:continue
            # Geometry follows the same role/region binding as emitted paint.
            ink=draw.inks[draw.role('bridges' if r['bridge'] else 'roads')]
            core,outer=road_widths(r['cls'],ink['width_mm'])
            roads.append(dict(r,geometry=g,core=core,outer=outer,
                              deck=decks.geometry.get(road_key(r)) if terrain and r['bridge'] and decks else None))
        def build_road():
            road_count=0;road_work=0
            progress('drawing',region+'Drawing streets',0,len(roads)*2,'passes')
            for level in sorted({(r['level'],r['bridge']) for r in roads}):
                selected=sorted([r for r in roads if (r['level'],r['bridge'])==level],key=lambda r:r['core'])
                projected=[]
                for index,r in enumerate(selected):
                    drawing_part(.60,.84,road_work/max(1,3*len(roads)));road_work+=1
                    progress('drawing',region+'Projecting streets',index,len(selected),'streets')
                    identity=prefix+'roads|'+r['id']
                    runs=parts(project_deck(terrain,r['deck'],identity),'LineString') if r['deck'] is not None else projected_runs(r['geometry'],r['outer'],identity)
                    projected.append((r,runs))
                for core in [False,True]:
                    for r,lines in projected:
                        drawing_part(.60,.84,road_work/max(1,3*len(roads)));road_work+=1
                        progress('drawing',region+'Drawing streets',road_count,len(roads)*2,'passes')
                        road_count+=1
                        if core and not r['core']:continue
                        category=('bridges' if r['bridge'] else 'roads')+('/core' if core else '/casing')
                        for g in lines:
                            if not core and r['core']:
                                for edge in road_edges(g,r['core'],r['outer']):
                                    draw.paint(edge,category,r['id'],width=(r['outer']-r['core'])/2,name=r['name'])
                            else:
                                draw.paint(g,category,r['id'],color=WHITE if core else BLACK,width=r['core'] if core else r['outer'],name=r['name'])
                            draw.commit(stroke_clip(g,r['outer']).intersection(mask) if terrain else mask)
                        counts[prefix+category]+=bool(lines)
        paint_stage('Road paint',['bridges'],roads,build_road)
        drawing_part(.84,.95)
        def build_shore():
            if f['shore']!='none':
                progress('drawing',region+'Drawing waterways and shorelines')
                streams=data.get('streams',[]);water_total=len(streams)+1+(3 if f['shore']=='echo' else 0)
                for index,stream in enumerate(streams):
                    drawing_part(.84,.95,index/water_total)
                    line(stream['geometry'].intersection(domain),'water/streams',stream['id'])
                drawing_part(.84,.95,len(streams)/water_total)
                line(shore,'water/shore','shore',.8)
                if f['shore']=='echo':
                    for distance in [1,2,3]:
                        drawing_part(.84,.95,(len(streams)+distance)/water_total)
                        # The fill already authors these offsets. Avoid retracing
                        # them when the selected echo and fill spacings coincide.
                        multiple=distance/f['water_spacing_mm']
                        if f['water_treatment']=='concentric' and abs(multiple-round(multiple))<1e-8:continue
                        progress('drawing',region+f'Computing shoreline echo {distance} of 3')
                        distance_m=distance*MM/scale
                        # Erode the water polygon instead of buffering thousands of
                        # intersecting shore segments. Exclude artificial fetch edges.
                        echo=water.buffer(-distance_m,quad_segs=4).boundary.difference(domain.boundary.buffer(distance_m*1.01))
                        line(echo,'water/echo',distance)
        paint_stage('Shore paint',['shore']+(['water_treatment','water_spacing_mm'] if f['shore']=='echo' else []),[water,data.get('streams',[])],build_shore)
        drawing_part(.95,1)
        if f['labels']!='none':
            candidates=sorted(data.get('labels',[]),key=lambda a:a['rank'])
            candidates += [dict(name=a['name'],geometry=a['geometry'].representative_point(),kind='parks') for a in parks if a['name'] and f['park_labels']]
            candidates += [dict(name=r['name'],geometry=max(parts(r['geometry'],'LineString'),key=lambda g:g.length).interpolate(.5,normalized=True),kind='roads') for r in sorted(roads,key=lambda r:-r['geometry'].length) if r['name'] and parts(r['geometry'],'LineString')]
            seen=set();limit=35 if f['labels']=='sparse' else 140
            progress('labels',region+'Placing labels',0,len(candidates),'candidates')
            for index,a in enumerate(candidates):
                drawing_part(.95,1,index/len(candidates))
                progress('labels',region+'Placing labels',index,len(candidates),'candidates')
                if a['kind']=='peaks' and not f['peak_labels']:continue
                if a['kind']=='parks' and (not f['parks'] or not f['park_labels']):continue
                if a['kind']=='roads' and (f['roads']=='none' or f['roads']=='major' and a.get('cls') not in MAJOR):continue
                if a['name'] in seen or len(seen)>=limit:continue
                p=a['geometry']
                if not domain.covers(p):continue
                located=terrain.locate(p.x,p.y) if terrain else (affinity.affine_transform(p,flat).coords[0],True)
                if not located or not located[1]:continue
                if not mask.covers(shapely.Point(*located[0])):continue
                seen.add(a['name'])
                angle=0
                if a.get('direction'):
                    dx,dy=a['direction']
                    other=terrain.locate(p.x+dx,p.y+dy) if terrain else ((located[0][0]+scale*(matrix[0,0]*dx+matrix[0,1]*dy),located[0][1]+scale*(matrix[1,0]*dx+matrix[1,1]*dy)),True)
                    if other and other[1]:
                        angle=math.degrees(math.atan2(other[0][1]-located[0][1],other[0][0]-located[0][0]))
                        angle=(angle+90)%180-90
                draw.label(a['name'],located[0],mask,size=9 if a['kind']=='roads' else 11,angle=angle,category='labels/'+a['kind'])
                draw.commit(mask)
        drawing_part(1,1)

    draw.commit()
    # Final page clipping is AFTER geographic clipping, projection and width.
    # It is independent of the terrain's visibility masks.
    prefix = clip_stream(frame, draw.height)
    draw.streams = [prefix+b'\n'.join(draw.streams)+b'\nQ']
    draw.region = ''; draw.style = recipe
    progress('pdf','Writing vector PDF')
    furniture(draw, recipe, data['source']['attribution'], bool(elevation),ornaments)
    draw.flush()
    pdf = output/'map.pdf'
    credits=data['source']['attribution']+('; '+terrain_source['attribution'] if terrain_source and terrain_source.get('attribution') else '')
    draw.doc.set_metadata(dict(title=recipe['title'],author='Plotline; '+data['source']['attribution'],subject=credits))
    draw.doc.save(pdf, deflate=True, garbage=4, no_new_id=True)
    source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    preview_scale = min((84 if quality == 'draft' else 120)/72, 2400/max(draw.page.rect.width, draw.page.rect.height))
    progress('preview','Rendering preview image')
    draw.page.get_pixmap(matrix=pymupdf.Matrix(preview_scale, preview_scale), alpha=False).save(output/'preview.png')
    progress('checks','Checking PDF and print intent')
    text = draw.page.get_text()
    if furniture_options(recipe)['title'] and recipe['title'] not in text:
        raise ValueError('The enabled map title is missing from the source PDF')
    if draw.page.get_images():
        raise ValueError('The final map must remain vector')
    ink_usage = draw.ink_usage()
    unmeasured = [use for use in ink_usage if draw.inks[use['id']]['width_mm'] is None]
    used_ids = {use['id'] for use in ink_usage}
    output_inks = [i for i in recipe['palette']['inks'] if i['id'] in used_ids]
    # The contract requires a nonempty palette even for a blank PDF. An unused
    # measured ink satisfies that shape without adding any marks or bindings.
    if not output_inks:
        output_inks = [i for i in recipe['palette']['inks'] if i['width_mm'] is not None][:1]
    warnings = ['Source artwork preview; exact physical pen coverage and mileage are supplied by Plotline Studios.']
    warnings.extend(data.get('warnings', []))
    warnings.append(f"OSM zoom {data['source'].get('zoom', '?')}; {data['source'].get('tile_count', 0)} cached vector tiles. Scale is local to the map center.")
    if terrain:
        warnings.append('Bridge deck grades are inferred from terrain at their approaches, not surveyed elevations. Building volumes and bridge structure geometry are not modeled.')
    if quality == 'draft':
        warnings.append('Draft uses simplified geometry'+(' and terrain refined for the visible page.' if terrain else '.'))
    if surface_stats:
        warnings.append(f"Terrain: {surface_stats['active_cell_median_m']:.0f} m median cell spacing in the page region; {surface_stats['source_pixel_m']:.0f} m finest source pixels.")
        if surface_stats['budget_limited']:
            warnings.append('Terrain refinement reached this preview’s mesh budget; '+('Generate PDF for finer terrain.' if quality=='draft' else 'Some terrain remains above the requested detail tolerance.'))
    if need_shade:
        warnings.append('Hillshade uses slope illumination from the selected sun; cast shadows from neighboring terrain are not modeled.')
    if unmeasured:
        warnings.append('Print intent unavailable until these ink diameters are measured: '+', '.join(i['label'] for i in unmeasured))
    if 'calibration' not in recipe['media']:
        warnings.append('Paper profile supports authoring; physical registration has not been calibrated.')
    intent = None
    if not unmeasured and output_inks:
        intent = dict(schema='plotline.print-intent/1', title=recipe['title'], source_sha256=source_hash,
                      pages=[dict(page_index=0, source_size_mm=recipe['media']['sheet_size_mm'], target_media=recipe['media'],
                                  placement=dict(scale=1, rotation_deg=0, offset_mm=[0, 0]))],
                      palette=dict(recipe['palette'], inks=output_inks), bindings=list(draw.bindings.values()),
                      authoring=dict(application='map-studio',url=os.environ.get('PLOTLINE_MAP_PUBLIC_URL','http://localhost:9191/map'),recipe_hash=digest(recipe)),
                      policies=dict(fill=dict(mode='crosshatch', spacing_mm=recipe['features']['hatch_spacing_mm'], angle_deg=45),
                                    stroke_width_tolerance=.75, thin_detail_thickening=.75,
                                    raster='reject', gradients='omit',
                                    transparency='reject', blend_modes='reject', soft_masks='reject'))
        validate_intent(intent, pdf)
        (output/'map.print-intent.json').write_bytes(canonical(intent))
    else:
        (output/'map.print-intent.json').unlink(missing_ok=True)
    provenance = dict(recipe=recipe, source_sha256=source_hash, composer_version=version, quality=quality,
                      inputs=dict(map=data['source'], elevation=terrain_source), layout=page_layout, fetched_bounds=bounds_for(domain,recipe),
                      feature_counts=dict(counts), labels=draw.labels, omitted_labels=draw.omitted+omissions,
                      issues=data['issues'], geometry_repairs=geometry_repairs, warnings=warnings, terrain=surface_stats,
                      bridge_decks=decks.stats if terrain and decks else None,
                      depth_audit=audit if terrain else None,build_cache=build_cache.summary(), seconds=round(time.monotonic()-started, 3))
    for name, value in [('recipe.json', recipe), ('provenance.json', provenance)]:
        (output/name).write_bytes(canonical(value))
    draw.doc.close()
    progress('bundle','Packaging PDF, recipe and source tiles')
    with zipfile.ZipFile(output/'map-bundle.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name in ['map.pdf', 'recipe.json', 'provenance.json']+(['map.print-intent.json'] if intent else []):
            bundle.write(output/name, name)
        # Freeze exact remote inputs alongside the recipe; provider revisions may expire.
        inputs=data['source'].get('tiles',[])+(terrain_source or {}).get('tiles',[])
        for source in {s['cache_key']:s for s in inputs}.values():
            bundle.write(cache/'http'/source['cache_key'],'sources/'+source['cache_key'])
    result = dict(source_sha256=source_hash, recipe_hash=digest(recipe), quality=quality,
                  intent_available=bool(intent), ink_usage=ink_usage, unmeasured_inks=unmeasured,
                  feature_counts=dict(counts), label_count=len(draw.labels),
                  omitted_labels=draw.omitted+omissions, warnings=warnings, seconds=provenance['seconds'],
                  layout=page_layout, depth_audit=provenance['depth_audit'],
                  source=data['source'], fetched_bounds=bounds_for(domain,recipe),build_cache=build_cache.summary())
    (output/'result.json').write_bytes(canonical(result))
    progress('complete','Composition complete')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('recipe', type=Path); parser.add_argument('output', type=Path)
    parser.add_argument('--quality', choices=['draft', 'proof'], default='proof')
    parser.add_argument('--cache', type=Path); parser.add_argument('--version')
    args = parser.parse_args()
    print(json.dumps(compose(json.loads(args.recipe.read_text()), args.output, args.quality, args.cache, args.version)))
