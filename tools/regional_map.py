#!/usr/bin/env python3
"""Compose tabloid regional proofs from frozen public Seattle GIS layers.

Uses GEOS for projection/clipping/generalization and MuPDF for attributed vector
PDF. Feature selection is in the style files; conversion remains Rust/GEOS.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

import pymupdf
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, box, shape, mapping
from shapely.ops import linemerge, unary_union

from build_map import ROOT, parts, BLACK, RED, GREEN, WHITE
from check import run, check_artifacts
from summarize_plot import summarize


def read_layers(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'source.json').read_text())
    layers={}
    for name,source in manifest['layers'].items():
        data=gzip.decompress((directory/source['file']).read_bytes())
        if hashlib.sha256(data).hexdigest()!=source['sha256']:
            raise ValueError(f'Snapshot checksum mismatch: {name}')
        layers[name]=json.loads(data)['features']
    return layers,manifest


class Projection:
    def __init__(self, bounds, frame):
        west,south,east,north=bounds
        if not (-180<west<east<180 and -85<south<north<85 and east-west<1 and north-south<1):
            raise ValueError('Expected small-area geographic bounds')
        x0,y0,x1,y1=frame
        cosine=math.cos(math.radians((south+north)/2))
        self.scale=min((x1-x0)/((east-west)*cosine),(y1-y0)/(north-south))
        width,height=(east-west)*cosine*self.scale,(north-south)*self.scale
        x0+=(x1-x0-width)/2; y0+=(y1-y0-height)/2
        self.matrix=[cosine*self.scale,0,0,-self.scale,x0-west*cosine*self.scale,y0+north*self.scale]
        self.viewport=box(x0,y0,x0+width,y0+height)
        self.points_per_metre=self.scale/111195.0802

    def geometry(self,geometry):
        return affinity.affine_transform(geometry,self.matrix)

    def point(self,lon,lat):
        p=self.geometry(Point(lon,lat))
        return p.x,p.y


def clip_stream(geometry,height):
    commands=['q']
    for poly in parts(geometry,'Polygon'):
        for ring in [poly.exterior,*poly.interiors]:
            coords=list(ring.coords)
            commands.append(f'{coords[0][0]:.8f} {height-coords[0][1]:.8f} m')
            commands.extend(f'{x:.8f} {height-y:.8f} l' for x,y in coords[1:])
            commands.append('h')
    commands.append('W* n')
    return ('\n'.join(commands)+'\n').encode()


class Drawing:
    def __init__(self,width,height):
        self.doc=pymupdf.open(); self.page=self.doc.new_page(width=width,height=height)
        self.height=height; self.layers={}; self.counts=Counter(); self.occupied=[]; self.labels=[]; self.omitted=[]
        self.fonts={}
        for key,filename in [('sans','DejaVuSans.ttf'),('serif','DejaVuSerif.ttf')]:
            path=Path('/usr/share/fonts/truetype/dejavu')/filename
            self.fonts[key]=pymupdf.Font(fontfile=str(path))
            self.page.insert_font(fontname=key,fontfile=str(path))
        self.canvas=self.page.new_shape()

    def layer(self,category,identity,name=''):
        key='|'.join([category,str(identity),name.replace('|','/')])
        if key not in self.layers:self.layers[key]=self.doc.add_ocg(key)
        return self.layers[key]

    def paint(self,geometry,category,identity,color=BLACK,width=.65,fill=None,name='',dashes=None):
        if geometry.is_empty:return
        rings=[]
        for poly in parts(geometry,'Polygon'):rings.extend([poly.exterior,*poly.interiors])
        rings.extend(parts(geometry,'LineString'))
        if not rings:return
        for ring in rings:self.canvas.draw_polyline(list(ring.coords))
        self.canvas.finish(color=color,fill=fill,width=width,closePath=False,lineCap=1,lineJoin=1,
                           even_odd=True,dashes=dashes,oc=self.layer(category,identity,name))
        self.counts[category]+=1

    def commit(self,mask=None):
        before=set(self.page.get_contents())
        self.canvas.commit()
        if mask is not None:
            prefix=clip_stream(mask,self.height)
            for xref in set(self.page.get_contents())-before:
                self.doc.update_stream(xref,prefix+self.doc.xref_stream(xref)+b'\nQ')
        self.canvas=self.page.new_shape()

    def text(self,text,point,size=10,font='sans',color=BLACK,category='labels/furniture',identity='map',angle=0):
        self.canvas.insert_text(point,text,fontsize=size,fontname=font,color=color,
                                morph=(pymupdf.Point(*point),pymupdf.Matrix(-angle)) if angle else None,
                                oc=self.layer(category,identity,text))

    def label(self,text,anchor,viewport,size=10,font='sans',angle=0,offset=(0,0),leader=False,category='labels/places',identity=None):
        face=self.fonts[font]; w=face.text_length(text,fontsize=size)
        local=box(-w/2-2,-face.ascender*size-1.5,w/2+2,-face.descender*size+1.5)
        identity=identity or text
        for dx,dy in [(0,0),(0,-14),(0,14),(-18,0),(18,0),(0,-28),(0,28)]:
            x,y=anchor[0]+offset[0]+dx,anchor[1]+offset[1]+dy
            bg=affinity.translate(affinity.rotate(local,angle,origin=(0,0)),x,y)
            if not viewport.covers(bg) or any(bg.intersects(p) for p in self.occupied):continue
            if leader and math.dist(anchor,(x,y))>8:
                self.paint(LineString([anchor,(x,y)]).difference(bg),'labels/leaders',identity,width=.65)
            self.paint(bg,category+'/background',identity,color=None,width=0,fill=WHITE)
            a=math.radians(angle); baseline=(x-math.cos(a)*w/2,y-math.sin(a)*w/2)
            self.text(text,baseline,size,font,category=category,identity=identity,angle=angle)
            self.occupied.append(bg.buffer(1))
            self.labels.append(dict(text=text,category=category,anchor=anchor,baseline=baseline,
                                    size=size,angle=angle,background=mapping(bg)))
            return True
        self.omitted.append(text)
        return False


def merged_lines(geometries):
    union=unary_union(geometries)
    if union.geom_type in ('MultiLineString','GeometryCollection'):union=linemerge(parts(union,'LineString'))
    return union


def compose(data,style,output):
    layers,manifest=read_layers(data)
    width,height=style['width_in']*72,style['height_in']*72
    margin=style['margin_pt']
    projection=Projection(style['bounds'],(margin,margin+74,width-margin,height-margin-62))
    viewport=projection.viewport
    issues=[]
    def project(feature):
        geometry=shape(feature['geometry'])
        if not geometry.is_valid:
            issues.append(dict(id=feature['properties'].get('OBJECTID'),reason='GEOS make_valid'))
            geometry=shapely.make_valid(geometry)
        return projection.geometry(geometry)
    land=unary_union([project(f) for f in layers['landwater'] if f['properties']['POLYGON']=='land'])
    water=unary_union([project(f) for f in layers['landwater'] if f['properties']['POLYGON']=='water'])
    # Shared land/water edges exclude artificial dataset and viewport borders.
    shore=land.boundary.intersection(water.buffer(.02)).simplify(style['simplify_pt'])
    city=unary_union([project(f) for f in layers['districts']]).simplify(style['simplify_pt'],preserve_topology=True)
    inside=viewport.intersection(city) if style['city_emphasis'] else viewport
    outside=viewport.difference(city) if style['city_emphasis'] else shapely.GeometryCollection()
    draw=Drawing(width,height)
    parks=[]
    dropped=Counter()
    for f in layers['parks']:
        g=project(f).intersection(land).intersection(viewport).simplify(style['simplify_pt'],preserve_topology=True)
        if g.area<style['park_min_area_pt2']:
            dropped['small_or_off_page_parks']+=1;continue
        parks.append((f['properties'],g))
    roads=defaultdict(list)
    for f in layers['streets']:
        p=f['properties']; name=p['STNAME_ORD'] or ''; cls=p['ARTCLASS']
        if cls not in style['road_classes'] or ' RP' in name or 'RAMP' in name or 'TUNNEL' in name:
            dropped['road_class_or_ramp']+=1;continue
        g=project(f).intersection(viewport.buffer(5)).simplify(style['simplify_pt'])
        if not g.is_empty:roads[(name,cls)].append(g)
    roads=[dict(name=name,cls=cls,geometry=merged_lines(gs),bridge=bool(re.search(r'\bBR\b',name)))
           for (name,cls),gs in roads.items()]
    if not outside.is_empty:
        draw.paint(shore,'context/shorelines','shore',width=.65)
        for p,g in parks:draw.paint(g.boundary,'context/parks',p['OBJECTID'],width=.65,name=p['NAME'])
        for r in roads:
            if r['cls'] in (1,4,5,9):draw.paint(r['geometry'],'context/roads',r['name'],width=.65,name=r['name'])
        draw.commit(outside)
    # Echoes are real strokes inside water, not a full ocean fill.
    for distance in style['water_echo_offsets_pt']:
        echo=shore.buffer(distance,quad_segs=4).boundary.intersection(water).intersection(viewport)
        draw.paint(echo,'water/shore_echoes',str(distance),width=.65)
    draw.paint(shore,'water/shorelines','shore',width=style['shore_width_pt'])
    for p,g in parks:
        draw.paint(g,'parks',p['OBJECTID'],color=GREEN,width=.65,fill=GREEN,name=p['NAME'])
    draw.commit(inside)
    for bridge in (False,True):
        selected=sorted([r for r in roads if r['bridge']==bridge],key=lambda r:style['road_cores_pt'][str(r['cls'])])
        for core in (False,True):
            for r in selected:
                w=style['road_cores_pt'][str(r['cls'])]
                color=RED if bridge or r['cls'] in style['red_road_classes'] else BLACK
                draw.paint(r['geometry'],'bridges' if bridge else 'roads/'+str(r['cls']),r['name'],
                           color=WHITE if core else color,width=w if core else w+2*style['road_edge_pt'],name=r['name'])
        draw.commit(inside)
    if style['city_emphasis']:
        # Keep even the finite-width boundary symbol on the city side.
        draw.paint(city.buffer(-style['pen_width_pt']).boundary.intersection(land.buffer(.05)),
                   'city/boundary','city',color=RED,width=.65,dashes='[4 3] 0')
        draw.commit(inside)
    # Curated names/anchors are editable styling, not invented geographic shapes.
    for label in style['labels']:
        anchor=projection.point(*label['location'])
        draw.label(label['text'],anchor,viewport,size=label.get('size',11),font=label.get('font','sans'),
                   angle=label.get('angle',0),offset=label.get('offset_pt',[0,0]),leader=label.get('leader',False),
                   category='labels/'+label.get('kind','places'))
    for requested in style['bridge_labels']:
        candidates=[r['geometry'] for r in roads if r['name'] in requested['sources']]
        lines=parts(merged_lines(candidates),'LineString') if candidates else []
        if not lines:
            draw.omitted.append(requested['text']);continue
        g=max(lines,key=lambda g:g.length);p=g.interpolate(.5,normalized=True)
        draw.label(requested['text'],(p.x,p.y),viewport,size=requested.get('size',9),
                   offset=requested.get('offset_pt',[0,-18]),leader=True,category='labels/bridges')
    for requested in style['park_labels']:
        candidates=[g for p,g in parks if p['NAME']==requested['source']]
        if not candidates:
            draw.omitted.append(requested['text']);continue
        g=unary_union(candidates).intersection(inside)
        if g.is_empty:continue
        p=g.representative_point()
        draw.label(requested['text'],(p.x,p.y),viewport,size=requested.get('size',9),
                   offset=requested.get('offset_pt',[0,0]),leader=requested.get('leader',False),category='labels/parks')
    for requested in style['road_labels']:
        lines=parts(merged_lines([r['geometry'].intersection(inside) for r in roads if r['name'] in requested['sources']]),'LineString')
        if not lines:
            draw.omitted.append(requested['text']);continue
        g=max(lines,key=lambda g:g.length);p=g.interpolate(requested.get('fraction',.5),normalized=True)
        draw.label(requested['text'],(p.x,p.y),viewport,size=8.5,angle=requested.get('angle',0),category='labels/routes')
    draw.commit(viewport)
    draw.text(style['title'],(margin,margin+23),30)
    draw.text(style['subtitle'],(margin,margin+44),10)
    # Actual physical layout, a north arrow and a local-projection scale bar.
    y=height-margin-37
    draw.paint(LineString([(margin,y-3),(margin+25,y-3)]),'legend','routes',color=RED,width=.65)
    draw.text(style['red_legend'],(margin+31,y),8.5)
    x=margin+210
    draw.paint(box(x,y-9,x+20,y),'legend','parks',color=GREEN,width=.65,fill=GREEN)
    draw.text('Parks',(x+27,y),8.5)
    x=margin+320
    draw.paint(LineString([(x,y-3),(x+20,y-3)]),'legend','shore',width=.65)
    draw.text('Shore / context' if style['city_emphasis'] else 'Shore / streets',(x+27,y),8.5)
    length=style['scale_bar_m']*projection.points_per_metre
    x=margin;y=height-margin-12
    draw.paint(LineString([(x,y-3),(x,y),(x+length,y),(x+length,y-3)]),'legend','scale',width=.65)
    draw.text(f"{style['scale_bar_m']/1000:g} km",(x+length+6,y+2),8.5)
    x=width-margin-15;y=margin+32
    draw.paint(LineString([(x,y+16),(x,y),(x-3,y+5),(x,y),(x+3,y+5)]),'legend','north',width=.65)
    draw.text('N',(x-3.5,y-5),9)
    draw.text('Data: City of Seattle / SDOT / Seattle Parks and Recreation',(margin,height-18),7.5)
    draw.text('Plotline',(width-margin-45,height-18),7.5)
    draw.commit()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    draw.doc.set_metadata(dict(title=style['title'],author=manifest['attribution']+'; Plotline'))
    draw.doc.save(output/'map.pdf',deflate=True,garbage=4)
    (output/'map.svg').write_text(draw.page.get_svg_image(text_as_path=True))
    metadata=dict(style=style,inputs=manifest,viewport_pt=list(viewport.bounds),city_mask=mapping(inside),
                  projection='local equirectangular at bbox midpoint',scale_denominator=72/(.0254*projection.points_per_metre),
                  feature_counts=dict(draw.counts),dropped=dict(dropped),labels=draw.labels,omitted_labels=draw.omitted,issues=issues,
                  limitations=['Cartographic prototypes, not navigation maps.',
                               'Buildings and local residential streets omitted.',
                               'Street dataset does not encode a complete grade-separated road topology.',
                               'Outside-city context is limited to the extent of the Seattle agency layers.',
                               'Municipal mask dissolves the published 2024 council districts, including water.'])
    (output/'map.provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    draw.doc.close()
    print(json.dumps({k:metadata[k] for k in ['scale_denominator','feature_counts','omitted_labels','issues']}),flush=True)
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--style',required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--data',default='data/seattle-region-2026-10-29')
    parser.add_argument('--compose-only',action='store_true')
    args=parser.parse_args()
    if Path(args.name).name!=args.name or args.name in ('.','..'):raise ValueError('Expected a single output name')
    style=json.loads((ROOT/args.style).read_text());output=ROOT/'build'/args.name
    compose(ROOT/args.data,style,output/'source')
    if args.compose_only:return
    print(run('cargo','build','--release','--locked'))
    pdf,events=output/'source/map.pdf',output/'source/map.events.json'
    print(run('.venv/bin/python','tools/pdf_native.py',str(pdf),str(events)))
    print(run('target/release/plotline',str(events),str(output),str(style['pen_width_pt']),'40',
              'crosshatch','red-green-black',str(style['hatch_spacing_pt']),str(style['stroke_width_tolerance'])))
    validation=check_artifacts(args.name,str(pdf),dpi='216')
    (output/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    summary=summarize(output)
    print(run('mutool','draw','-q','-r','300','-o',str(output/'hpgl-300dpi.png'),str(output/'hpgl.svg')))
    print(run('mutool','draw','-q','-r','120','-o',str(output/'comparison.png'),str(output/'comparison.svg')))
    with pymupdf.open(output/'hpgl.svg') as svg:(output/'hpgl.pdf').write_bytes(svg.convert_to_pdf())
    print(f"PASS {args.name}: {summary['metrics']['pen_down_mm']/1000:.2f} m pen-down; interpretation {validation['interpretation_large_pixel_discrepancy_ratio']:.3%}")


if __name__=='__main__':main()
