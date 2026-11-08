#!/usr/bin/env python3
"""Audit the rebuilt portrait Letter proof against the HP7585 paper margins."""
import json
import math
import re
import xml.etree.ElementTree as ET

from PIL import Image
import pymupdf

from check import ROOT, check_artifacts
from summarize_plot import summarize


def main(directory=None):
    directory = directory or ROOT/'build/ravenna-letter-hp7585'
    style = json.loads((ROOT/'styles/ravenna-letter-hp7585.json').read_text())
    provenance = json.loads((directory/'source/map.provenance.json').read_text())
    assert provenance['style'] == style and not provenance['issues']
    # Independent hardware contract in top-left, page-down millimetres.
    allowed_mm = [15, 39, 200.9, 264.4]
    allowed = [v*72/25.4 for v in allowed_mm]
    assert all(abs(a-b) < 1e-8 for a, b in zip(allowed, provenance['printable_rect_pt']))
    for filename in ['source/map.pdf', 'hpgl.pdf']:
        with pymupdf.open(directory/filename) as doc:
            assert len(doc) == 1 and tuple(doc[0].rect) == (0, 0, 612, 792)
            if filename.startswith('source/'):
                assert style['title'] in doc[0].get_text() and 'ODbL 1.0' in doc[0].get_text()
                # Font metrics, including footer descenders, must fit without a crop.
                padded = pymupdf.Rect(allowed) + (-.001, -.001, .001, .001)
                assert all(padded.contains(pymupdf.Rect(b[:4])) for b in doc[0].get_text('blocks'))
    with Image.open(directory/'hpgl-300dpi.png') as image:
        assert image.size == (2550, 3300)

    # Frozen original Letter recipe; no old generated artifact is needed.
    old_bounds = [-122.3074347826087, 47.671, -122.303, 47.674400765380234]
    w, s, e, n = old_bounds
    old_scale = min(540/((e-w)*math.cos(math.radians((s+n)/2))), 615/(n-s))
    w, s, e, n = provenance['bounds']
    assert s == old_bounds[1] and e == old_bounds[2]
    assert w > old_bounds[0] and n < old_bounds[3], 'Crop must remove north/west context'
    x0, y0, x1, y1 = provenance['viewport_pt']
    scale = (y1-y0)/(n-s)
    assert abs(scale/old_scale-1) < 1e-8, 'Geographic scale changed'
    assert abs((x1-x0)/((e-w)*math.cos(math.radians((s+n)/2)))/scale-1) < 1e-8
    assert 'Ravenna Park' in {label['text'] for label in provenance['labels']}

    result = check_artifacts(str(directory.relative_to(ROOT/'build')), str(directory/'source/map.pdf'), dpi='72')
    summary = summarize(directory)
    assert summary['representation']['glyph_contours']['paints_without_paths'] == 0
    report = json.loads((directory/'report.json').read_text())
    events = json.loads((directory/'input.events.json').read_text())['events']
    buildings = [p for p in report['decoded_hpgl'] if p['source'].startswith('buildings|') and not p['white']]
    assert len(buildings) == 99, 'Letter building census changed'
    for paint in buildings:
        layout = paint['width_layout']
        assert layout and layout['passes'] == 1, f"House reverted to extra passes: {paint['source']}"
        assert paint['paths'] > 0
        # Independent mileage budget: integer-grid rounding may lengthen a
        # perimeter slightly; whole-object retracing must never hide in a label.
        event = events[paint['id']]
        a, b, c, d, tx, ty = event['matrix']
        points, length = [], 0.
        for command in event['path']:
            assert command['op'] in ['move', 'line', 'close']
            if command['op'] == 'move':
                points = []
            if command['op'] in ['move', 'line']:
                x, y = command['p']
                point = (a*x+c*y+tx, b*x+d*y+ty)
                if points:
                    length += math.dist(points[-1], point)
                points.append(point)
            elif command['op'] == 'close':
                length += math.dist(points[-1], points[0])
        assert paint['pen_down_mm'] <= length*25.4/72*1.02 + .05, f"House retraced: {paint['source']}"
    svg = ET.parse(directory/'hpgl.svg').getroot()
    paths = svg.findall('{http://www.w3.org/2000/svg}path')
    assert len(paths) == summary['metrics']['paths']
    ink_bounds = [math.inf, math.inf, -math.inf, -math.inf]
    # Round caps/joins: extrema of each decoded polyline plus its actual radius
    # bound the entire footprint, including endpoints (not merely centerlines).
    for path in paths:
        width = float(path.attrib['stroke-width'])
        assert width == style['pen_width_pt']
        assert path.attrib['stroke-linecap'] == path.attrib['stroke-linejoin'] == 'round'
        points = [(float(x), float(y)) for x, y in re.findall(r'[ML] ([-\d.]+) ([-\d.]+)', path.attrib['d'])]
        assert len(points) >= 2
        xs, ys = zip(*points)
        bounds = [min(xs)-width/2, min(ys)-width/2, max(xs)+width/2, max(ys)+width/2]
        assert bounds[0] >= allowed[0]-1e-6 and bounds[1] >= allowed[1]-1e-6
        assert bounds[2] <= allowed[2]+1e-6 and bounds[3] <= allowed[3]+1e-6
        ink_bounds = [min(ink_bounds[0], bounds[0]), min(ink_bounds[1], bounds[1]),
                      max(ink_bounds[2], bounds[2]), max(ink_bounds[3], bounds[3])]
    ink_mm = [v*25.4/72 for v in ink_bounds]
    fit = dict(page_mm=[215.9, 279.4], printable_rect_mm=allowed_mm,
               decoded_ink_bounds_mm=ink_mm,
               decoded_ink_size_mm=[ink_mm[2]-ink_mm[0], ink_mm[3]-ink_mm[1]],
               relative_scale_change=scale/old_scale-1,
               single_pass_buildings=len(buildings),
               locally_adjusted_buildings=sum(p['width_layout']['locally_adjusted'] for p in buildings),
               building_pen_down_mm=sum(p['pen_down_mm'] for p in buildings),
               bounds=provenance['bounds'], metrics=summary['metrics'])
    (directory/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
    (directory/'validation-letter.json').write_text(json.dumps(fit, indent=2)+'\n')
    print('PASS Letter margins, complete lettering, southeast anchor, unchanged scale, and decoded finite-width ink')
    print(json.dumps(fit, indent=2))


if __name__ == '__main__':
    main()
