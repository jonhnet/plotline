#!/usr/bin/env python3
"""Audit the built relief proof against independent per-ray mesh depths."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import pymupdf
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from check import ROOT, run
from regional_map import Drawing, read_layers
from terrain_geometry import Terrain
from pdf_native import extract
from summarize_plot import summarize


def coordinates(data):
    return [tuple(map(float,p)) for p in re.findall(r'([-\d.]+) ([-\d.]+)',data)]


def visible_coverage(data):
    """Read even-odd diagnostic rings, repairing SVG decimal-rounding crossings."""
    geometry = Polygon()
    for ring in data.split('M')[1:]:
        points = coordinates(ring)
        if len(points) >= 4:
            polygon = Polygon(points)
            # Six-decimal SVG serialization can turn nearly collinear vertices
            # into tiny self-intersections. Preserve their even-odd area rather
            # than asking GEOS to overlay an invalid ring or discarding it.
            if not polygon.is_valid:
                polygon = make_valid(polygon)
            geometry = geometry.symmetric_difference(polygon)
    return geometry


def check_line_width_fixture():
    """Flat terrain cannot justify either narrowed strokes or extra pen lifts."""
    directory = ROOT/'build/terrain-line-width'
    directory.mkdir(parents=True,exist_ok=True)
    drawing = Drawing(400,600)
    for row, elevation in enumerate([10,30,70]):
        terrain = Terrain(range(4),range(4),np.zeros((4,4)),3,215,elevation,
                          [20,20+190*row,380,180+190*row])
        line, mask = terrain.drape_line(LineString([(.2,1.0002),(2.8,1.0002)]),.65,'contour')
        assert line.buffer(.325,cap_style='flat').difference(mask).area < 1e-7
        drawing.paint(line,'terrain/contours',elevation,width=.65)
        drawing.commit(mask)
    drawing.doc.save(directory/'source.pdf')
    drawing.doc.close()
    events, _ = extract(directory/'source.pdf')
    (directory/'events.json').write_text(json.dumps(events))
    run('target/release/plotline',str(directory/'events.json'),str(directory),'.4','40',
        'crosshatch','red-green-black','10','.75')
    report = json.loads((directory/'report.json').read_text())
    assert report['metrics']['paths'] == 3
    assert all(p['paths'] == 1 and p['width_status'] == 'width_tolerance' for p in report['decoded_hpgl'])
    return dict(view_elevations_deg=[10,30,70], decoded_paths_per_line=[1,1,1])


def contour_continuity(directory, report, events):
    """Every safe contour centerline must actually be covered by decoded ink.

    This complements the geometric width test above: a faithful rendering of
    an already-notched source PDF would not detect the original mesh bug.
    """
    namespace = '{http://www.w3.org/2000/svg}'
    ink = []
    radius = report['settings']['pen_width']/2
    for element in ET.parse(directory/'hpgl.svg').getroot().findall(namespace+'path'):
        if element.attrib['stroke'] == 'rgb(0,0,0)':
            ink.append(LineString(coordinates(element.attrib['d'])).buffer(radius,quad_segs=8))
    ink = unary_union(ink)
    visible = ET.parse(directory/'visible.svg').getroot().findall(namespace+'path')
    assert len(visible) == len(report['decoded_hpgl'])
    settings = report['settings']
    guard = 2*settings['flatten_tolerance'] + math.sqrt(2)/(2*settings['units_per_mm']*25.4/72) + settings['pen_width']*.001
    expected_length = missing_length = 0.
    paths = 0
    for paint, element in zip(report['decoded_hpgl'],visible):
        if not paint['source'].startswith('terrain/contours|'):
            continue
        paths += paint['paths']
        geometry = visible_coverage(element.attrib['d'])
        event = events['events'][paint['id']]
        a,b,c,d,tx,ty = event['matrix']
        curves, points = [], []
        for command in event['path']:
            assert command['op'] in ['move','line','close'], 'Expected projected polylines'
            if command['op'] == 'move':
                if len(points) > 1:
                    curves.append(LineString(points))
                points = []
            if command['op'] in ['move','line']:
                x,y = command['p']; points.append((a*x+c*y+tx,b*x+d*y+ty))
            elif command['op'] == 'close':
                points.append(points[0])
        if len(points) > 1:
            curves.append(LineString(points))
        # Exclude legitimate endpoint retreat near later roads, labels and
        # visibility cuts, using the same physical-radius/rounding allowance.
        expected = unary_union(curves).intersection(geometry.buffer(-(radius+guard+1e-4)))
        expected_length += expected.length
        missing_length += expected.difference(ink).length
    assert expected_length > 0 and missing_length/expected_length < 1e-4, 'Visible contours have artificial pen gaps'
    return dict(decoded_contour_paths=paths, safe_centerline_length_mm=expected_length*25.4/72,
                uncovered_safe_centerline_mm=missing_length*25.4/72)


def audit_mesh(directory):
    with np.load(directory/'source/terrain.npz') as data:
        vertices = data['vertices']
        indices = data['triangles']
        projected = vertices @ data['projection'].T * data['scale'] + data['offset']
        depths = vertices @ data['camera']
    faces = projected[indices]
    full = [Polygon(points) for points in faces]
    visible_source = json.loads((directory/'source/visible-faces.geojson').read_text())['features']
    visible = [shape(f['geometry']) for f in visible_source]
    ids = [f['properties']['triangle'] for f in visible_source]
    all_tree, visible_tree = STRtree(full), STRtree(visible)
    # Barycentric depth interpolation does not use the composer's cell order,
    # accumulated union, plane coefficients or sampled visibility decisions.
    def depth(idx, xy):
        a,b,c = faces[idx]
        weights = np.linalg.solve(np.column_stack([b-a,c-a]),xy-a)
        d = depths[indices[idx]]
        return d[0] + weights[0]*(d[1]-d[0]) + weights[1]*(d[2]-d[0])
    rng = np.random.default_rng(1735)
    lower, upper = projected.min(axis=0), projected.max(axis=0)
    worst = 0.; checked = 0
    for xy in rng.uniform(lower, upper, (12000,2)):
        point = Point(xy)
        candidates = all_tree.query(point, predicate='intersects')
        if not len(candidates):
            continue
        expected = max(depth(idx,xy) for idx in candidates)
        front = visible_tree.query(point, predicate='intersects')
        assert len(front) == 1, 'Visible surface has a hole or overlapping owners'
        actual = depth(ids[front[0]],xy)
        worst = max(worst, abs(expected-actual)); checked += 1
    assert checked > 5000 and worst < 1e-3, f'Incorrect terrain occlusion: {worst} m'
    return dict(screen_rays_checked=checked, maximum_frontmost_depth_error_m=float(worst))


def check_page_fit(directory, style, report, provenance):
    """Independently check source lettering and finite decoded ink on the sheet."""
    width,height = style['width_in']*72, style['height_in']*72
    margins = style['margins_mm']
    measured = np.array([margins['left'],margins['top'],
                         style['width_in']*25.4-margins['right'],
                         style['height_in']*25.4-margins['bottom']])*72/25.4
    content = np.asarray(provenance['layout']['content_rect_pt'])
    assert np.all(content[:2] >= measured[:2]) and np.all(content[2:] <= measured[2:])
    ink = np.array([math.inf,math.inf,-math.inf,-math.inf])
    paths = ET.parse(directory/'hpgl.svg').getroot().findall('{http://www.w3.org/2000/svg}path')
    assert len(paths) == report['metrics']['paths']
    for path in paths:
        points = np.array([(float(x),float(y)) for x,y in re.findall(r'[ML] ([-\d.]+) ([-\d.]+)',path.attrib['d'])])
        radius = float(path.attrib['stroke-width'])/2
        low,high = points.min(axis=0)-radius, points.max(axis=0)+radius
        assert np.all(low >= content[:2]-1e-6) and np.all(high <= content[2:]+1e-6), 'Decoded pen footprint crosses content bounds'
        ink[:2] = np.minimum(ink[:2],low); ink[2:] = np.maximum(ink[2:],high)
    with pymupdf.open(directory/'source/map.pdf') as doc:
        assert tuple(doc[0].rect) == (0,0,width,height)
        text = doc[0].get_text()
        for required in ['SEATTLE / RELIEF','Terrain / shore','Major roads','Parks',
                         'View from the southwest.','Terrain: USGS 3DEP','Plotline']:
            assert required in text, f'Missing page furniture: {required}'
        padded = pymupdf.Rect(content.tolist())+(-.001,-.001,.001,.001)
        assert all(padded.contains(pymupdf.Rect(b[:4])) for b in doc[0].get_text('blocks')), 'Source lettering crosses content bounds'
    ink_mm = ink*25.4/72
    size_mm = ink_mm[2:]-ink_mm[:2]
    maximum = np.minimum((measured[2:]-measured[:2])*25.4/72,style['hard_clip_size_mm'])-2*style['content_inset_mm']
    assert np.all(size_mm <= maximum+1e-6), 'Ink is too large for measured paper or reported hard clip'
    result = dict(page_mm=[style['width_in']*25.4,style['height_in']*25.4],
                  measured_margins_mm=margins, hard_clip_size_mm=style['hard_clip_size_mm'],
                  content_rect_mm=(content*25.4/72).tolist(), decoded_ink_bounds_mm=ink_mm.tolist(),
                  decoded_ink_size_mm=size_mm.tolist(), source_lettering_fit=True,
                  decoded_footprints_fit=True)
    (directory/'validation-page.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main(directory=None, style_path=None):
    directory = directory or ROOT/'build/seattle-relief-11x17'
    name = directory.name
    provenance = json.loads((directory/'source/map.provenance.json').read_text())
    style = json.loads((style_path or ROOT/'styles'/f'{name}.json').read_text())
    assert style == provenance['style'], 'Stale style'
    dem = ROOT/style['elevation_file']
    source = json.loads(dem.with_suffix('.source.json').read_text())
    assert hashlib.sha256(dem.read_bytes()).hexdigest() == source['sha256']
    assert source == provenance['inputs']['elevation']
    _, vectors = read_layers(ROOT/style['vector_directory'])
    assert vectors == provenance['inputs']['vectors']
    for file in ['source/map.pdf','hpgl.pdf']:
        with pymupdf.open(directory/file) as doc:
            assert len(doc) == 1 and tuple(doc[0].rect)[2:] == (1224,792)
            assert not doc[0].get_images(), 'Relief must stay vector'
    with Image.open(directory/'hpgl-300dpi.png') as image:
        assert image.size == (5100,3300)
    events = json.loads((directory/'input.events.json').read_text())
    assert events == json.loads((directory/'source/map.events.json').read_text())
    assert all(e['color'] == [0,0,0] for e in events['events'] if e.get('role') == 'text')
    report = json.loads((directory/'report.json').read_text())
    validation = json.loads((directory/'validation.json').read_text())
    assert validation['metrics'] == report['metrics']
    assert validation['dpi'] == 216 and validation['interpretation_large_pixel_discrepancy_ratio'] < .01
    for paint in report['decoded_hpgl']:
        assert paint['outside_allowed_area_pt2'] <= 1e-7
        assert (paint['uncovered_required_area_pt2'] or 0) <= 1e-7
        assert not paint['white'] or not paint['paths']
        assert not paint['paths'] or paint['source'] != 'unclassified'
    summary = summarize(directory)
    assert len(summary['pens']) == 3
    assert summary['settings']['pen_width'] == style['pen_width_pt']
    assert summary['settings']['stroke_width_tolerance'] == style['stroke_width_tolerance']
    glyphs = summary['representation']['glyph_contours']
    assert glyphs['ink_fraction'] > .8 and glyphs['paints_without_paths'] == 0
    assert provenance['terrain']['fully_hidden_triangles'] > 1000, 'View no longer demonstrates occlusion'
    assert .3 < provenance['visible_line_fraction'] < .95
    assert {'Queen Anne','Magnolia','Capitol Hill','Lake Union'} <= {x['text'] for x in provenance['labels']}
    result = audit_mesh(directory)
    result['constant_width_fixture'] = check_line_width_fixture()
    result['contour_continuity'] = contour_continuity(directory, report, events)
    if 'margins_mm' in style:
        result['paper_fit'] = check_page_fit(directory, style, report, provenance)
    result.update(pen_down_m=report['metrics']['pen_down_mm']/1000,
                  pen_up_m=report['metrics']['pen_up_mm']/1000,
                  projected_line_fraction_hidden=1-provenance['visible_line_fraction'],
                  glyph_paints_without_paths=glyphs['paints_without_paths'])
    (directory/'validation-terrain.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS terrain proof',json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='seattle-relief-11x17')
    parser.add_argument('--style', type=Path)
    args = parser.parse_args()
    main(ROOT/'build'/args.name, args.style)
