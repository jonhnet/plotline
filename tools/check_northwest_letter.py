#!/usr/bin/env python3
"""Audit the northwest Letter proof with native and supersampled PDF comparisons."""
import json,math,re,sys,xml.etree.ElementTree as ET
from pathlib import Path
import pymupdf
import numpy as np
from PIL import Image
from shapely.geometry import Polygon,Point
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'tools'))
from summarize_plot import summarize
from raster_compare import render_comparison


def main():
    p=ROOT/'build/ravenna-northwest-letter-hp7585'
    r=json.loads((p/'report.json').read_text());m=json.loads((p/'source/map.provenance.json').read_text())
    base=json.loads((ROOT/'build/ravenna-letter-hp7585/source/map.provenance.json').read_text())
    s=summarize(p)
    assert m['style'] == json.loads((ROOT/'styles/ravenna-northwest-letter-hp7585.json').read_text())
    sampling=render_comparison(str(p/'source/map.pdf'),p,216)
    with pymupdf.open(p/'hpgl.svg') as svg:(p/'hpgl.pdf').write_bytes(svg.convert_to_pdf())
    with pymupdf.open(p/'hpgl.pdf') as doc:doc[0].get_pixmap(dpi=300).save(p/'hpgl-300dpi.png')
    assert not m['issues']
    assert all(x['outside_allowed_area_pt2'] <= 1e-7 and (x['uncovered_required_area_pt2'] or 0) <= 1e-7 and not(x['white'] and x['paths']) for x in r['decoded_hpgl'])
    assert s['representation']['glyph_contours']['paints_without_paths']==0
    allowed=np.array([15,39,200.9,264.4])*72/25.4
    ink=np.array([math.inf,math.inf,-math.inf,-math.inf])
    paths=ET.parse(p/'hpgl.svg').getroot().findall('{http://www.w3.org/2000/svg}path')
    assert len(paths)==r['metrics']['paths']
    for path in paths:
     pts=np.array([(float(x),float(y)) for x,y in re.findall(r'[ML] ([-\d.]+) ([-\d.]+)',path.attrib['d'])])
     radius=float(path.attrib['stroke-width'])/2
     low=pts.min(axis=0)-radius;high=pts.max(axis=0)+radius
     assert np.all(low>=allowed[:2]-1e-6) and np.all(high<=allowed[2:]+1e-6)
     ink[:2]=np.minimum(ink[:2],low);ink[2:]=np.maximum(ink[2:],high)
    with pymupdf.open(p/'source/map.pdf') as d:
     assert tuple(d[0].rect)==(0,0,612,792)
     rect=pymupdf.Rect(allowed.tolist())+(-.001,-.001,.001,.001)
     assert all(rect.contains(pymupdf.Rect(b[:4])) for b in d[0].get_text('blocks'))
    with Image.open(p/'hpgl-300dpi.png') as im:assert im.size==(2550,3300)
    w0,s0,e0,n0=base['bounds'];w,so,e,n=m['bounds']
    assert e==w0 and so==n0 and abs((n-so)-(n0-s0))<1e-10
    scales=[]
    for meta in [base,m]:
     x0,y0,x1,y1=meta['viewport_pt'];west,south,east,north=meta['bounds']
     scales.append((y1-y0)/(north-south))
    assert abs(scales[1]/scales[0]-1)<1e-8
    actual=np.array(Image.open(p/'coverage.png').convert('RGB'));ref=np.array(Image.open(p/'reference.png').convert('RGB'))
    bad=np.max(np.abs(actual.astype(int)-ref.astype(int)),axis=2)>64
    ink_pixels=np.minimum(actual.min(axis=2),ref.min(axis=2))<224
    native_ratio=float(bad.sum()/ink_pixels.sum())
    ratio=sampling['sampled']['ratio']
    # Classify the discrepancy spatially without excluding anything from the score.
    y,x=np.where(bad);coords=np.column_stack([x/3,y/3]);x0,y0,x1,y1=m['viewport_pt']
    furniture=(coords[:,1]<y0)|(coords[:,1]>y1)
    frame=np.minimum.reduce([abs(coords[:,0]-x0),abs(coords[:,0]-x1),abs(coords[:,1]-y0),abs(coords[:,1]-y1)])<1
    labels=[Polygon(l['background']) for l in m['labels']]
    label=np.array([any(g.covers(Point(point)) for g in labels) for point in coords])
    buildings=[x for x in r['decoded_hpgl'] if x['source'].startswith('buildings') and not x['white']]
    fallback=[dict(id=x['id'],source=x['source'],paths=x['paths'],visible_area_pt2=x['visible_area_pt2'],pen_down_mm=x['pen_down_mm']) for x in buildings if not x['width_layout']]
    assert len(buildings)==234, 'Northwest building census changed'
    # These two clipped boundary remnants are the only explicit exceptions.
    # A new heavy house must fail this check, even if containment still passes.
    assert {x['source'] for x in fallback}=={'buildings|way/220914318|','buildings|way/220914204|'}
    events=json.loads((p/'input.events.json').read_text())['events']
    for house in buildings:
     if not house['width_layout']:continue
     assert house['width_layout']['passes']==1 and house['paths']>0
     event=events[house['id']];a,b,c,d,tx,ty=event['matrix'];points=[];length=0.
     for command in event['path']:
      assert command['op'] in ['move','line','close']
      if command['op']=='move':points=[]
      if command['op'] in ['move','line']:
       x,y=command['p'];point=(a*x+c*y+tx,b*x+d*y+ty)
       if points:length+=math.dist(points[-1],point)
       points.append(point)
      else:length+=math.dist(points[-1],points[0])
     assert house['pen_down_mm']<=length*25.4/72*1.02+.05, f"House retraced: {house['source']}"
    result=dict(case=p.name,dpi=216,interpretation_large_pixel_discrepancy_ratio=ratio,interpretation_threshold_passed=ratio<.01,
     native_resolution_large_pixel_discrepancy_ratio=native_ratio,raster_sampling=sampling,
     note='Gate uses fixed 4x4 area samples with unchanged thresholds; native-resolution discrepancy is retained as a diagnostic.',
     native_resolution_discrepancy_pixels=dict(total=int(bad.sum()),inside_text_or_frame_regions=int((furniture|frame|label).sum())),
     sampled_discrepancy_pixels=sampling['sampled']['differing_pixels'],
     physical_footprints_passed=True,paper_and_furniture_fit_passed=True,building_mileage_passed=True,decoded_ink_bounds_mm=(ink*25.4/72).tolist(),
     geographic_shift=dict(reference='ravenna-letter-hp7585',map_widths_west=1,map_heights_north=1,relative_scale_change=scales[1]/scales[0]-1),
     bounds=m['bounds'],buildings=len(buildings),single_pass_buildings=sum(x['width_layout'] is not None for x in buildings),building_fallbacks=fallback,metrics=r['metrics'])
    (p/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    print('Unrepresented',s['completely_unrepresented_paints'],s['completely_unrepresented_area_pt2'])
    if not result['interpretation_threshold_passed']:
        raise SystemExit('Physical/page checks pass, but supersampled PDF interpretation exceeds 1%; see validation.json.')


if __name__ == '__main__':
    main()
