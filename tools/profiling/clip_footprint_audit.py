"""Offline audit of the frozen Washington fast-rectangle-clipping experiment.

Run after both original experiment HPGL files exist; see
docs/map-preparation-performance-03.md. Never connects to the plotter.
"""
import json, math
import argparse
from pathlib import Path
from shapely.geometry import LineString, box
from shapely.ops import unary_union
import pymupdf
from shapely import hausdorff_distance
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
REPO=parser.parse_args().repo.resolve()
p=REPO/'build/prepare-perf-washington-3'
r=REPO/'build/prepare-perf-washington-2'
delta=json.loads((p/'clip-segment-delta.json').read_text())
assert {row[0] for rows in delta.values() for row in rows} == {1}, 'This audit expects only the frozen black-pen changes'
changes=[LineString([(x/40,y/40) for x,y in row[1:3]]) for group in delta.values() for row in group]
clusters=list(unary_union([line.buffer(.6) for line in changes]).geoms)
regions=[box(*c.bounds) for c in clusters]
bounds=[g.bounds for g in regions]
width=.358

def collect(name):
    lines=[[] for _ in bounds];pos=(0,0);pen=0
    for raw in (r/name/'plot.hpgl').read_text().split(';'):
        cmd=raw.strip();op=cmd[:2];arg=cmd[2:]
        if op=='SP':pen=int(arg)
        elif op in ('PD','PU') and arg:
            nums=[int(x)/40 for x in arg.split(',')]; pts=[tuple(nums[i:i+2]) for i in range(0,len(nums),2)]
            if op=='PD' and pen==1:
                allpts=[pos]+pts
                lo=(min(x for x,y in allpts),min(y for x,y in allpts));hi=(max(x for x,y in allpts),max(y for x,y in allpts))
                for i,(x0,y0,x1,y1) in enumerate(bounds):
                    if hi[0]+width>x0 and lo[0]-width<x1 and hi[1]+width>y0 and lo[1]-width<y1:
                        lines[i].append(LineString(allpts))
            pos=pts[-1]
    return lines
A=collect('baseline');B=collect('candidate-8');report=[]
height=300*len(bounds)
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}"><rect width="1200" height="{height}" fill="white"/>']
for i,(a,b,region) in enumerate(zip(A,B,regions)):
    ca=unary_union(a).intersection(region);cb=unary_union(b).intersection(region)
    def physical_ink(lines):
        near=region.buffer(width)
        capsules=[]
        for line in lines:
            for u,v in zip(line.coords,list(line.coords)[1:]):
                segment=LineString([u,v])
                if near.intersects(segment):capsules.append(segment.buffer(width/2,quad_segs=32))
        return unary_union(capsules).intersection(region)
    ia=physical_ink(a);ib=physical_ink(b)
    gone=ia.difference(ib);new=ib.difference(ia)
    row={'bounds_mm':list(region.bounds),'sampled_centerline_hausdorff_mm':hausdorff_distance(ca,cb,densify=0.1), 'sampled_ink_hausdorff_mm':hausdorff_distance(ia,ib,densify=0.1),'lost_ink_mm2':gone.area,'gained_ink_mm2':new.area,'baseline_ink_mm2':ia.area}
    row={k:(None if isinstance(v,float) and not math.isfinite(v) else v) for k,v in row.items()}
    report.append(row)
    for j,(label,shapes) in enumerate([('Previous',[(ia,'#333')]),('Fast clip',[(ib,'#333')]),('Red removed / blue added',[(ia,'#ddd'),(gone,'#e44'),(new,'#2699de')])]):
        x0,y0,x1,y1=region.bounds;scale=min(350/(x1-x0),230/(y1-y0))
        svg.append(f'<text x="{400*j+20}" y="{300*i+25}" font-size="18">{label}</text>')
        svg.append(f'<text x="{400*j+20}" y="{300*i+285}" font-size="14">Region {i+1}: {x1-x0:.2f} × {y1-y0:.2f} mm</text>')
        svg.append(f'<g transform="translate({400*j+20},{300*i+265}) scale({scale},{-scale}) translate({-x0},{-y0})">')
        for shape,color in shapes:
            for poly in getattr(shape,'geoms',[shape]):
                if poly.is_empty or poly.geom_type!='Polygon':continue
                path=' '.join('M'+' L'.join(f'{x},{y}' for x,y in ring.coords)+' Z' for ring in [poly.exterior,*poly.interiors])
                svg.append(f'<path d="{path}" fill="{color}" fill-rule="evenodd"/>')
        svg.append('</g>')
svg.append('</svg>');svg=''.join(svg);(p/'clip-differences.svg').write_text(svg)
doc=pymupdf.open('svg',svg.encode());pdf=pymupdf.open('pdf',doc.convert_to_pdf());pdf[0].get_pixmap(matrix=pymupdf.Matrix(1.3,1.3)).save(p/'clip-differences.png')
(p/'clip-physical-audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps(report,indent=2,allow_nan=False))
