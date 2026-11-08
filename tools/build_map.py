#!/usr/bin/env python3
"""OSM XML -> styled, attributed vector PDF and SVG. No network access during builds.

Small-area map composer: GEOS handles clipping, multipolygons and label geometry;
MuPDF handles fonts/PDF. Plotline's general PDF-to-HPGL engine remains Rust.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pymupdf
import shapely
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import linemerge, unary_union

ROOT = Path(__file__).resolve().parent.parent
BLACK, RED, GREEN, WHITE = (0, 0, 0), (1, 0, 0), (0, .5, 0), (1, 1, 1)


def parts(geometry, kind):
    if geometry.is_empty:
        return []
    if geometry.geom_type == kind:
        return [geometry]
    if hasattr(geometry, 'geoms'):
        return [p for g in geometry.geoms for p in parts(g, kind)]
    return []


def tags(element):
    return {t.get('k'): t.get('v') for t in element.findall('tag')}


def area_kind(t):
    if t.get('building') not in (None, 'no'):
        return 'buildings'
    if t.get('leisure') in ('park', 'garden', 'nature_reserve') or t.get('natural') in ('wood', 'scrub') or t.get('landuse') in ('forest', 'grass', 'meadow'):
        return 'parks'
    if t.get('natural') == 'water' or t.get('water'):
        return 'water'
    return None


def multipolygon(members, ways):
    """Require complete closed rings; never silently close a missing relation member."""
    rings = {'outer': [], 'inner': []}
    used = set()
    for member in members:
        role = member.get('role') or 'outer'
        if member.get('type') != 'way' or role not in rings:
            raise ValueError('unsupported multipolygon member')
        ref = member.get('ref')
        if ref not in ways:
            raise ValueError(f'missing multipolygon way {ref}')
        rings[role].append(ways[ref]['geometry'])
        used.add(ref)
    def polygons(lines):
        if not lines:
            return Polygon()
        # polygonize handles fragmented/reversed rings; reject dangling segments.
        collection, cuts, dangles, invalid = shapely.polygonize_full(lines)
        if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
            raise ValueError('incomplete or invalid multipolygon ring')
        return unary_union(list(collection.geoms))
    outer, inner = polygons(rings['outer']), polygons(rings['inner'])
    if outer.is_empty or (not inner.is_empty and not outer.covers(inner)):
        raise ValueError('multipolygon has no outer ring or orphan inner ring')
    return outer.difference(inner), used


def read_osm(path, bounds, frame):
    data = Path(path).read_bytes()
    if str(path).endswith('.gz'):
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    if root.tag != 'osm':
        raise ValueError('Expected OSM XML')
    if bounds is None:
        b = root.find('bounds')
        if b is None:
            raise ValueError('OSM has no bounds; supply --bbox west south east north')
        bounds = [float(b.get(k)) for k in ['minlon', 'minlat', 'maxlon', 'maxlat']]
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -85 < south < north < 85):
        raise ValueError('Invalid small-area bounds')
    if east-west > 1 or north-south > 1:
        raise ValueError('Local projection is limited to one degree; use a regional GIS for larger maps')
    cosine = math.cos(math.radians((south+north)/2))
    x0, y0, x1, y1 = frame
    scale = min((x1-x0)/((east-west)*cosine), (y1-y0)/(north-south))
    w, h = (east-west)*cosine*scale, (north-south)*scale
    x0 += ((x1-x0)-w)/2
    y0 += ((y1-y0)-h)/2
    viewport = box(x0, y0, x0+w, y0+h)
    nodes = {e.get('id'): (x0+(float(e.get('lon'))-west)*cosine*scale,
                           y0+(north-float(e.get('lat')))*scale) for e in root.findall('node')}
    ways, issues = {}, []
    for e in root.findall('way'):
        refs = [n.get('ref') for n in e.findall('nd')]
        if len(refs) < 2 or any(ref not in nodes for ref in refs):
            issues.append(dict(id='way/'+e.get('id'), reason='missing nodes or short way'))
            continue
        ways[e.get('id')] = dict(id='way/'+e.get('id'), tags=tags(e), geometry=LineString([nodes[r] for r in refs]), closed=refs[0]==refs[-1])
    areas, consumed = [], set()
    for e in root.findall('relation'):
        t = tags(e)
        if t.get('type') != 'multipolygon' or not area_kind(t):
            continue
        try:
            geometry, used = multipolygon(e.findall('member'), ways)
        except ValueError as error:
            issues.append(dict(id='relation/'+e.get('id'), reason=str(error)))
            continue
        areas.append(dict(id='relation/'+e.get('id'), tags=t, geometry=geometry))
        consumed.update(used)
    for key, way in ways.items():
        if key not in consumed and way['closed'] and area_kind(way['tags']):
            geometry = Polygon(way['geometry'].coords)
            if not geometry.is_valid:
                issues.append(dict(id=way['id'], reason='invalid polygon repaired with GEOS make_valid'))
                geometry = shapely.make_valid(geometry)
            areas.append(dict(way, geometry=geometry))
    for feature in areas:
        feature['geometry'] = feature['geometry'].intersection(viewport)
    areas = [f for f in areas if not f['geometry'].is_empty]
    metadata = dict(input_sha256=hashlib.sha256(data).hexdigest(), bounds=bounds,
                    projection='local equirectangular, cosine at bbox midpoint',
                    viewport_pt=list(viewport.bounds), source_attributes=root.attrib,
                    input_counts=dict(Counter(e.tag for e in root)), issues=issues)
    return list(ways.values()), areas, viewport, metadata


def validate_style(style):
    expected = {'title', 'width_in', 'height_in', 'pen_width_pt',
                'fill_style', 'hatch_spacing_pt', 'building_fill', 'building_width_pt',
                'park_fill', 'park_width_pt', 'road_edge_pt', 'road_core_widths_pt',
                'path_width_pt', 'skip_sidewalks', 'house_addresses', 'road_labels',
                'area_labels', 'label_size_pt', 'area_label_size_pt',
                'label_padding_pt', 'label_repeat_distance_pt', 'stroke_width_tolerance'}
    margin_keys = set(style) & {'margin_pt', 'margins_mm'}
    if len(margin_keys) != 1:
        raise ValueError('Specify exactly one of margin_pt (legacy) or margins_mm')
    expected |= margin_keys
    if set(style) != expected:
        raise ValueError(f'Style keys mismatch: missing={expected-set(style)}, unknown={set(style)-expected}')
    for key, value in style.items():
        if key == 'margins_mm':
            if not isinstance(value, dict) or set(value) != {'left', 'top', 'right', 'bottom'}:
                raise ValueError('margins_mm needs left, top, right and bottom')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in value.values()):
                raise ValueError('Margins must be nonnegative and finite')
            continue
        if key == 'stroke_width_tolerance':
            if value is not None and (isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError('stroke_width_tolerance must be null (strict) or between 0 and 1')
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be positive and finite')
    for kind, width in style['road_core_widths_pt'].items():
        if not math.isfinite(width) or width <= 0:
            raise ValueError(f'Invalid road width for {kind}')
    if style['fill_style'] not in ('hatch', 'crosshatch', 'solid'):
        raise ValueError('Unknown fill_style')
    for key in ['building_fill', 'park_fill', 'skip_sidewalks', 'house_addresses', 'road_labels', 'area_labels']:
        if not isinstance(style[key], bool):
            raise ValueError(f'{key} must be a boolean')


def page_layout(style):
    """PDF top-left coordinates; header and footer share the printable rectangle."""
    width, height = style['width_in']*72, style['height_in']*72
    if 'margins_mm' in style:
        m = {k: v*72/25.4 for k, v in style['margins_mm'].items()}
        printable = (m['left'], m['top'], width-m['right'], height-m['bottom'])
    else:
        margin = style['margin_pt']
        printable = (margin, margin, width-margin, height-margin)
    x0, y0, x1, y1 = printable
    frame = (x0, y0+65, x1, y1-40)
    if frame[0] >= frame[2] or frame[1] >= frame[3]:
        raise ValueError('Margins leave no map area')
    return width, height, printable, frame


def build(input_path, output, style, bounds=None):
    validate_style(style)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    width, height, printable, frame = page_layout(style)
    ways, areas, viewport, metadata = read_osm(input_path, bounds, frame)
    print(f"Map: {len(ways)} ways, {len(areas)} visible areas; composing vector PDF", flush=True)
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    # Embedded DejaVu Sans avoids platform-specific font fallback and supports Unicode.
    font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font = pymupdf.Font(fontfile=str(font_path))
    page.insert_font(fontname='MapFont', fontfile=str(font_path))
    canvas = page.new_shape()
    layers, counts = {}, Counter()
    def layer(category, feature):
        name = '|'.join([category, feature['id'], feature['tags'].get('name', '').replace('|', '/')])
        if name not in layers:
            layers[name] = doc.add_ocg(name)
        return layers[name]
    def paint(geometry, category, feature, color, stroke_width, fill=None, dashes=None):
        rings = []
        for p in parts(geometry, 'Polygon'):
            rings += [p.exterior, *p.interiors]
        rings += parts(geometry, 'LineString')
        if not rings:
            return
        for ring in rings:
            canvas.draw_polyline(list(ring.coords))
        canvas.finish(color=color, fill=fill, width=stroke_width, closePath=False,
                      lineCap=1, lineJoin=1, even_odd=True, dashes=dashes, oc=layer(category, feature))
        counts[category] += 1
    # Broad areas first. Hollow buildings clear underlying hatching, then get an outline.
    for kind in ['parks', 'water', 'buildings']:
        for f in sorted((f for f in areas if area_kind(f['tags']) == kind), key=lambda f: -f['geometry'].area):
            if kind == 'buildings':
                paint(f['geometry'], kind, f, RED, style['building_width_pt'], RED if style['building_fill'] else WHITE)
            else:
                paint(f['geometry'], kind, f, GREEN, style['park_width_pt'], GREEN if style['park_fill'] else None)
    roads, paths, streams = [], [], []
    skipped = Counter()
    for f in ways:
        t = f['tags']
        highway = t.get('highway', '')
        base = highway.removesuffix('_link')
        if t.get('area') == 'yes':
            skipped['area_highways'] += 1
            continue
        if t.get('tunnel') not in (None, 'no') or t.get('location') == 'underground':
            skipped['underground'] += 1
            continue
        if base in style['road_core_widths_pt']:
            f = dict(f, core=style['road_core_widths_pt'][base], kind=base)
            # Topological levels: all casings then all white cores at each level.
            f['level'] = int(t.get('layer', '1' if t.get('bridge') not in (None, 'no') else '0'))
            f['geometry'] = f['geometry'].intersection(viewport.buffer(f['core']))
            if not f['geometry'].is_empty:
                roads.append(f)
        elif highway in ('footway', 'path', 'cycleway', 'steps', 'pedestrian'):
            if style['skip_sidewalks'] and t.get('footway') in ('sidewalk', 'crossing'):
                skipped['sidewalks_crossings'] += 1
                continue
            g = f['geometry'].intersection(viewport)
            if not g.is_empty:
                paths.append(dict(f, geometry=g))
        elif t.get('waterway') in ('stream', 'river', 'canal'):
            g = f['geometry'].intersection(viewport)
            if not g.is_empty:
                streams.append(dict(f, geometry=g))
    for f in streams:
        paint(f['geometry'], 'streams', f, GREEN, style['path_width_pt'])
    for f in paths:
        paint(f['geometry'], 'paths', f, GREEN, style['path_width_pt'], dashes='[3 3] 0')
    for level in sorted({f['level'] for f in roads}):
        same_level = sorted((f for f in roads if f['level']==level), key=lambda f: f['core'])
        for core in [False, True]:
            for f in same_level:
                paint(f['geometry'], 'roads/'+f['kind'], f, WHITE if core else BLACK,
                      f['core'] if core else f['core']+2*style['road_edge_pt'])
    labels, occupied, named_positions = [], [], defaultdict(list)
    def label(text, anchor, angle, size, category, feature, require_inside=None):
        if not text.strip():
            return False
        text_width = font.text_length(text, fontsize=size)
        pad = style['label_padding_pt']
        x, y = anchor
        # Baseline is y=0 in this local frame. The exact font metrics bound ink.
        local = box(-text_width/2-pad, -font.ascender*size-pad,
                    text_width/2+pad, -font.descender*size+pad)
        background = affinity.translate(affinity.rotate(local, angle, origin=(0, 0)), x, y)
        if not viewport.covers(background) or any(background.intersects(g) for g in occupied):
            return False
        if require_inside is not None and not require_inside.covers(background):
            return False
        if any(math.dist(anchor, p) < style['label_repeat_distance_pt'] for p in named_positions[text]):
            return False
        paint(background, category+'/background', feature, None, 0, WHITE)
        # MuPDF morph uses Cartesian rotation, opposite our page-down angle.
        theta = math.radians(angle)
        baseline = (x-math.cos(theta)*text_width/2, y-math.sin(theta)*text_width/2)
        canvas.insert_text(baseline, text, fontsize=size, fontname='MapFont', color=BLACK,
                           morph=(pymupdf.Point(*baseline), pymupdf.Matrix(-angle)), oc=layer(category, feature))
        occupied.append(background.buffer(pad))
        named_positions[text].append(anchor)
        labels.append(dict(text=text, category=category, source=feature['id'], anchor=anchor,
                           angle=angle, size=size, background=list(background.exterior.coords), color=list(BLACK)))
        counts[category] += 1
        return True
    if style['area_labels']:
        for f in sorted(areas, key=lambda f: -f['geometry'].area):
            name = f['tags'].get('name')
            if not name or area_kind(f['tags']) == 'buildings':
                continue
            p = f['geometry'].representative_point()
            label(name, (p.x, p.y), 0, style['area_label_size_pt'], 'labels/areas', f, f['geometry'])
    if style['road_labels']:
        by_name = defaultdict(list)
        for f in roads:
            if f['tags'].get('name'):
                by_name[f['tags']['name']].append(f)
        for name, features in sorted(by_name.items()):
            merged = unary_union([f['geometry'].intersection(viewport) for f in features])
            if merged.geom_type != 'LineString':
                merged = linemerge(merged)
            size = style['label_size_pt']
            needed = font.text_length(name, fontsize=size)+2*style['label_padding_pt']
            for line in sorted(parts(merged, 'LineString'), key=lambda g: -g.length):
                if line.length < needed:
                    continue
                # Search several positions; accept only nearly straight text-sized runs.
                for fraction in [.5, .25, .75, .125, .875]:
                    middle = line.length*fraction
                    if middle < needed/2 or middle+needed/2 > line.length:
                        continue
                    a, b = line.interpolate(middle-needed/2), line.interpolate(middle+needed/2)
                    chord = LineString([a, b])
                    samples = [line.interpolate(middle-needed/2+needed*i/8) for i in range(9)]
                    if a.distance(b) < needed*.97 or max(chord.distance(p) for p in samples) > 2:
                        continue
                    p = line.interpolate(middle)
                    angle = math.degrees(math.atan2(b.y-a.y, b.x-a.x))
                    if angle > 90: angle -= 180
                    if angle < -90: angle += 180
                    label(name, (p.x, p.y), angle, size, 'labels/roads', features[0])
    if style['house_addresses']:
        for f in areas:
            number = f['tags'].get('addr:housenumber')
            if number and area_kind(f['tags']) == 'buildings':
                p = f['geometry'].representative_point()
                label(number, (p.x, p.y), 0, style['label_size_pt'], 'labels/addresses', f, f['geometry'])
    canvas.commit()
    # Clip the whole map, including stroke widths, without clipping title/footer.
    x0, y0, x1, y1 = viewport.bounds
    for xref in page.get_contents():
        stream = doc.xref_stream(xref)
        doc.update_stream(xref, f'q {x0} {height-y1} {x1-x0} {y1-y0} re W n\n'.encode()+stream+b'\nQ')
    decoration = dict(id='map', tags={})
    title_oc = layer('labels/furniture', decoration)
    subtitle = 'OPENSTREETMAP / THREE-PEN STUDY'
    attribution = 'OpenStreetMap contributors | openstreetmap.org/copyright | ODbL 1.0'
    if 'margins_mm' in style:
        x0, y0, x1, y1 = printable
        def furniture(text, size, position):
            size = min(size, (x1-x0)/font.text_length(text, fontsize=1))
            baseline = {'top': y0+font.ascender*size,
                        'subtitle': y0+45,
                        'bottom': y1+font.descender*size}[position]
            page.insert_text((x0, baseline), text, fontsize=size, fontname='MapFont', color=BLACK, oc=title_oc)
        furniture(style['title'], 28, 'top')
        furniture(subtitle, 12, 'subtitle')
        furniture(attribution, 10, 'bottom')
        metadata.update(printable_rect_pt=list(printable), map_frame_pt=list(frame))
    else:
        # Preserve the historical scalar-margin proofs, including their footer placement.
        margin = style['margin_pt']
        page.insert_text((margin, margin+22), style['title'], fontsize=28, fontname='MapFont', color=BLACK, oc=title_oc)
        page.insert_text((margin, margin+45), subtitle, fontsize=12, fontname='MapFont', color=BLACK, oc=title_oc)
        page.insert_text((margin, height-margin+12), attribution, fontsize=10, fontname='MapFont', color=BLACK, oc=title_oc)
    doc.set_metadata(dict(title=style['title'], author='OpenStreetMap contributors; Plotline map style'))
    doc.save(output/'map.pdf', deflate=True, garbage=4)
    (output/'map.svg').write_text(page.get_svg_image(text_as_path=True))
    metadata.update(style=style, feature_counts=dict(counts), skipped_by_style=dict(skipped), labels=labels,
                    fonts=dict(file=str(font_path), sha256=hashlib.sha256(font_path.read_bytes()).hexdigest()),
                    pymupdf=pymupdf.pymupdf_version, shapely=shapely.__version__, geos=shapely.geos_version_string,
                    limitations=['Selective local map, not all OSM feature types.', 'Route/transit/restriction relations are not rendered.',
                                 'Underground features omitted; road stacking follows numeric layer.', 'Straight label runs only; no curved typography.',
                                 'Incomplete multipolygons are listed in issues.'],
                    attribution='OpenStreetMap contributors | https://www.openstreetmap.org/copyright | ODbL 1.0')
    (output/'map.provenance.json').write_text(json.dumps(metadata, indent=2)+'\n')
    doc.close()
    print(json.dumps(dict(output=str(output), feature_counts=dict(counts), issues=metadata['issues'])))
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='Local .osm or .osm.gz snapshot')
    parser.add_argument('output', help='Directory for map.pdf, map.svg and provenance')
    parser.add_argument('--style', default=str(ROOT/'styles/plotter.json'))
    parser.add_argument('--bbox', nargs=4, type=float, metavar=('WEST','SOUTH','EAST','NORTH'))
    args = parser.parse_args()
    build(args.input, args.output, json.loads(Path(args.style).read_text()), args.bbox)


if __name__ == '__main__':
    main()
