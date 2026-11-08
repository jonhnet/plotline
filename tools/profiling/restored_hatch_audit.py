"""Find and render a regained green-hatch neighborhood in the frozen experiment."""
import json, argparse
from pathlib import Path
from shapely.geometry import LineString, box
from shapely.strtree import STRtree
from shapely.ops import unary_union
import pymupdf
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
parser.add_argument('--baseline',type=Path,help='Directory containing the previous plot.hpgl')
parser.add_argument('--candidate',type=Path,help='Directory containing the new plot.hpgl')
parser.add_argument('--output',type=Path,help='Directory for the comparison PNG and JSON')
args=parser.parse_args()
build=args.repo.resolve()/'build'
p=args.output or build/'prepare-perf-washington-3'
p.mkdir(parents=True,exist_ok=True)

def read(path):
    rows=[];pen=0;pos=(0.,0.)
    for raw in path.read_text().split(';'):
        c=raw.strip();op=c[:2];arg=c[2:]
        if op=='SP':pen=int(arg)
        elif op in ('PU','PD') and arg:
            v=[int(x)/40 for x in arg.split(',')]; pts=[tuple(v[i:i+2]) for i in range(0,len(v),2)]
            if op=='PD' and pen==3:rows.append([pos]+pts)
            pos=pts[-1]
    return rows
A=read((args.baseline or build/'prepare-perf-washington-2/insets-8')/'plot.hpgl')
B=read((args.candidate or build/'prepare-perf-washington-3/simplify-8')/'plot.hpgl')
key=lambda row:min(tuple(row),tuple(reversed(row)))
known={key(row) for row in A}
a=[LineString(row) for row in A];tree=STRtree(a)
b=[LineString(row) for row in B]
changed=[line for row,line in zip(B,b) if key(row) not in known]
changed.sort(key=lambda g:g.length,reverse=True)
for line in changed[:3000]:
    nearby=[a[int(i)] for i in tree.query(line.buffer(.8))]
    gained=line.difference(unary_union(nearby).buffer(.36))
    if gained.length>5:
        chunks=[x for x in getattr(gained,'geoms',[gained]) if x.geom_type=='LineString']
        if not chunks:continue
        part=max(chunks,key=lambda x:x.length)
        if part.length<4:continue
        # Select at most a 12 mm neighborhood of this regained centerline.
        mid=part.interpolate(.5,normalized=True);region=box(mid.x-6,mid.y-6,mid.x+6,mid.y+6)
        break
else:raise SystemExit('No substantial regained hatch found')
report={'bounds_mm':list(region.bounds),'sample_regained_length_mm':gained.intersection(region).length}
(p/'restored-hatch.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="430"><rect width="1200" height="430" fill="white"/>']
for j,(label,lines) in enumerate([('Previous plot',a),('Candidate clearance',b)]):
    tree=STRtree(lines);local=[lines[int(i)] for i in tree.query(region.buffer(.711))]
    ink=unary_union([g.buffer(.711/2,quad_segs=32) for g in local]).intersection(region)
    x0,y0,x1,y1=region.bounds;scale=350/12
    svg.append(f'<text x="{400*j+20}" y="25" font-size="20">{label}</text><g transform="translate({400*j+20},390) scale({scale},{-scale}) translate({-x0},{-y0})">')
    for g in getattr(ink,'geoms',[ink]):
        if g.geom_type!='Polygon' or g.is_empty:continue
        path=' '.join('M'+' L'.join(f'{x},{y}' for x,y in ring.coords)+' Z' for ring in [g.exterior,*g.interiors])
        svg.append(f'<path d="{path}" fill="#478626" fill-rule="evenodd"/>')
    svg.append('</g>')
svg.append('</svg>')
svg=''.join(svg);doc=pymupdf.open('svg',svg.encode());pdf=pymupdf.open('pdf',doc.convert_to_pdf())
source=pymupdf.open(build/'prepare-perf-washington/frozen/source.pdf');page=source[0];x0,y0,x1,y1=region.bounds;factor=72/25.4
clip=pymupdf.Rect(x0*factor,page.rect.height-y1*factor,x1*factor,page.rect.height-y0*factor)
display_scale=pdf[0].rect.width/1200
pdf[0].insert_text((820*display_scale,25*display_scale),"Source PDF (12 x 12 mm)",fontname="tiro",fontsize=20*display_scale)
source_pix=page.get_pixmap(matrix=pymupdf.Matrix(16,16),clip=clip,alpha=False)
pdf[0].insert_image(pymupdf.Rect(820*display_scale,40*display_scale,1170*display_scale,390*display_scale),pixmap=source_pix)
pdf[0].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5)).save(p/'restored-hatch.png')
