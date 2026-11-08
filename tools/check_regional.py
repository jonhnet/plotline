#!/usr/bin/env python3
"""Audit the two built tabloid proofs, including their decoded city color boundary."""
import json
import re
import xml.etree.ElementTree as ET

import pymupdf
from PIL import Image
from shapely.geometry import LineString, box, shape

from check import ROOT
from regional_map import read_layers
from summarize_plot import summarize


def check(name, directory=None):
    directory = directory or ROOT / 'build' / name
    style = json.loads((ROOT / 'styles' / (name + '.json')).read_text())
    provenance = json.loads((directory / 'source/map.provenance.json').read_text())
    _, manifest = read_layers(ROOT / 'data/seattle-region-2026-10-29')
    assert provenance['style'] == style and provenance['inputs'] == manifest, 'Stale style/input provenance'
    page_size = (style['width_in'] * 72, style['height_in'] * 72)
    for filename in ['source/map.pdf', 'hpgl.pdf']:
        with pymupdf.open(directory / filename) as doc:
            assert len(doc) == 1 and tuple(doc[0].rect)[2:] == page_size, 'Wrong paper dimensions'
            assert not doc[0].get_images(), 'The map must remain vector'
    with Image.open(directory / 'hpgl-300dpi.png') as preview:
        assert preview.size == (style['width_in'] * 300, style['height_in'] * 300)
    events = json.loads((directory / 'input.events.json').read_text())
    assert events == json.loads((directory / 'source/map.events.json').read_text())
    assert all(e['color'] == [0, 0, 0] for e in events['events'] if e.get('role') == 'text')
    assert not any(s.startswith(('buildings', 'labels/addresses')) for s in events['sources'].values())
    roads = {}
    for index, source in events['sources'].items():
        if source.startswith(('roads/', 'bridges|')):
            roads.setdefault(source, []).append(events['events'][int(index)]['color'])
    assert roads and all(len(colors) == 2 and colors[0] != [1, 1, 1] and colors[1] == [1, 1, 1]
                         for colors in roads.values()), 'Roads need a casing and white core'
    required = {'Puget Sound', 'Lake Washington', 'Discovery Park'}
    required |= {'Elliott Bay', 'Downtown', 'Seward Park'} if style['city_emphasis'] else {
        'Salmon Bay', 'Ballard Bridge', 'Fremont Bridge', 'Aurora Bridge', 'University Bridge', 'Montlake Bridge'}
    assert required <= {label['text'] for label in provenance['labels']}, 'Key labels missing'
    report = json.loads((directory / 'report.json').read_text())
    validation = json.loads((directory / 'validation.json').read_text())
    assert validation['metrics'] == report['metrics'], 'Stale validation'
    assert validation['dpi'] == 216 and validation['interpretation_large_pixel_discrepancy_ratio'] < .01
    for paint in report['decoded_hpgl']:
        assert paint['outside_allowed_area_pt2'] <= 1e-7
        assert (paint['uncovered_required_area_pt2'] or 0) <= 1e-7
        assert not paint['white'] or not paint['paths']
        assert not paint['paths'] or paint['source'] != 'unclassified'
        if paint['source'].startswith('context/'):
            assert paint['pen_color'] == [0, 0, 0]
    summary = summarize(directory)  # Reconcile all mileage tables with decoded commands.
    for setting, key in [('pen_width', 'pen_width_pt'), ('hatch_spacing', 'hatch_spacing_pt'),
                         ('stroke_width_tolerance', 'stroke_width_tolerance')]:
        assert summary['settings'][setting] == style[key]
    assert summary['settings']['fill_style'] == 'crosshatch' and len(summary['pens']) == 3
    representation = summary['representation']
    assert .10 < representation['crosshatch']['ink_fraction'] < .35, 'Hatch density regressed'
    assert representation['glyph_contours']['ink_fraction'] > .80
    assert representation['glyph_contours']['paints_without_paths'] == 0
    assert representation['stroke_contours']['ink_fraction'] > .70
    # Independently inspect finite-width decoded SVG strokes, including their end caps.
    viewport = box(*provenance['viewport_pt'])
    city = shape(provenance['city_mask'])
    outside_color_area = 0
    svg = ET.parse(directory / 'hpgl.svg').getroot()
    paths = svg.findall('{http://www.w3.org/2000/svg}path')
    assert len(paths) == report['metrics']['paths']
    for path in paths:
        width = float(path.attrib['stroke-width'])
        assert width == style['pen_width_pt']
        if not style['city_emphasis'] or path.attrib['stroke'] == 'rgb(0,0,0)':
            continue
        coordinates = re.findall(r'[ML] ([-\d.]+) ([-\d.]+)', path.attrib['d'])
        line = LineString([(float(x), float(y)) for x, y in coordinates])
        ink = line.buffer(width / 2, quad_segs=32).intersection(viewport)
        outside_color_area += ink.difference(city).area
    assert outside_color_area < 1e-6, f'Colored ink escaped city mask: {outside_color_area} pt²'
    if style['city_emphasis']:
        context = [p for p in report['decoded_hpgl'] if p['source'].startswith('context/')]
        assert sum(p['ink_inside_visible_area_pt2'] for p in context) / sum(p['visible_area_pt2'] for p in context) > .5, 'Context became fragmentary'
    result = dict(page_inches=[style['width_in'], style['height_in']],
                  scale_denominator=provenance['scale_denominator'],
                  colored_ink_outside_city_pt2=outside_color_area if style['city_emphasis'] else None,
                  glyph_paints_without_paths=representation['glyph_contours']['paints_without_paths'],
                  omitted_labels=provenance['omitted_labels'],
                  pen_down_m=report['metrics']['pen_down_mm'] / 1000,
                  pen_up_m=report['metrics']['pen_up_mm'] / 1000)
    (directory / 'validation-regional.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS', name, json.dumps(result))


if __name__ == '__main__':
    for name in ['waterways-11x17', 'seattle-11x17']:
        check(name)
