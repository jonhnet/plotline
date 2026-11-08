#!/usr/bin/env python3
"""Compose an oblique Seattle map with true vector terrain occlusion."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image
import pymupdf
import shapely
from shapely import affinity
from shapely.geometry import LineString, box, shape, mapping
from shapely.ops import unary_union

from build_map import ROOT, parts, BLACK, RED, GREEN, WHITE
from regional_map import Drawing, read_layers, merged_lines
from terrain_geometry import Terrain, stroke_clip
from check import run, check_artifacts
from summarize_plot import summarize


def page_layout(style):
    """Landscape PDF coordinates; machine X follows the long paper dimension.

    For rotation 0, PDF left/right are machine back/front, and PDF top/bottom
    are machine right/left. Intersect measured edge offsets with the reported
    window size, then inset the content without rescaling strokes or lettering.
    """
    width, height = style['width_in']*72, style['height_in']*72
    if 'margins_mm' not in style:
        content = (36, 36, width-36, height-22)
        inset = 0
        printable = None
    else:
        margins = style['margins_mm']
        if set(margins) != {'left','top','right','bottom'} or any(
                not math.isfinite(v) or v < 0 for v in margins.values()):
            raise ValueError('Expected finite nonnegative left/top/right/bottom margins')
        mm = 72/25.4
        x0,y0,x1,y1 = (margins['left']*mm, margins['top']*mm,
                       width-margins['right']*mm, height-margins['bottom']*mm)
        hard_clip = style['hard_clip_size_mm']
        if len(hard_clip) != 2 or any(not math.isfinite(v) or v <= 0 for v in hard_clip):
            raise ValueError('Expected a positive finite hard-clip width and height')
        dx,dy = max(0, x1-x0-hard_clip[0]*mm)/2, max(0, y1-y0-hard_clip[1]*mm)/2
        printable = (x0+dx,y0+dy,x1-dx,y1-dy)
        guard = style['content_inset_mm']
        if not math.isfinite(guard) or guard < 0:
            raise ValueError('Content inset must be finite and nonnegative')
        content = (printable[0]+guard*mm, printable[1]+guard*mm,
                   printable[2]-guard*mm, printable[3]-guard*mm)
        inset = 1  # Full edge stroke and coordinate rounding stay inside content.
    x0,y0,x1,y1 = content
    frame = (x0+inset,y0+89,x1-inset,y1-88)
    if frame[2] <= frame[0] or frame[3] <= frame[1]:
        raise ValueError('Margins and furniture leave no terrain area')
    return dict(page_pt=[width,height], printable_rect_pt=printable,
                content_rect_pt=content, terrain_frame_pt=frame,
                label_frame_pt=(x0+inset,y0+74,x1-inset,y1-68))


def load_terrain(path, style):
    path = Path(path)
    source = json.loads(path.with_suffix('.source.json').read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('Elevation snapshot checksum mismatch')
    with Image.open(path) as image:
        data = np.asarray(image, dtype=float)
        dx, dy, _ = image.tag_v2[33550]
        _, _, _, west, north, _ = image.tag_v2[33922]
    if data.ndim != 2 or not np.isfinite(data).all() or data.min() < -100 or data.max() > 1000:
        raise ValueError('Expected valid raw Seattle elevations in metres')
    stride = style['mesh_stride']
    if not isinstance(stride, int) or not 1 <= stride < min(data.shape):
        raise ValueError('Mesh stride must leave at least two vertices per axis')
    lons = west + (np.arange(data.shape[1])[::stride]+.5)*dx
    lats = (north - (np.arange(data.shape[0])[::stride]+.5)*dy)[::-1]
    z = np.maximum(data[::stride, ::stride][::-1], 0)
    latitude = (lats[0]+lats[-1])/2
    metres_lat = 111195.0802
    metres_lon = metres_lat*math.cos(math.radians(latitude))
    # Local metric frame shared by the DEM and all agency vector layers.
    matrix = [metres_lon, 0, 0, metres_lat, -lons[0]*metres_lon, -lats[0]*metres_lat]
    xs, ys = (lons-lons[0])*metres_lon, (lats-lats[0])*metres_lat
    terrain = Terrain(xs, ys, z, style['vertical_exaggeration'], style['camera_azimuth_deg'],
                      style['camera_elevation_deg'], page_layout(style)['terrain_frame_pt'])
    return terrain, matrix, source


def compose(style, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    terrain, geographic, dem_source = load_terrain(ROOT/style['elevation_file'], style)
    print('Terrain visibility', terrain.stats, flush=True)
    audit = terrain.audit_visibility(2000)
    print('Independent depth audit', audit, flush=True)
    if audit['maximum_frontmost_depth_error_m'] > 1e-3:
        raise AssertionError('Surface visibility disagrees with independent depth planes')
    layers, manifest = read_layers(ROOT/style['vector_directory'])
    frame = box(terrain.xs[0], terrain.ys[0], terrain.xs[-1], terrain.ys[-1])
    issues = []
    def world(feature):
        g = shape(feature['geometry'])
        if not g.is_valid:
            issues.append(dict(id=feature['properties'].get('OBJECTID'), repair='GEOS make_valid'))
            g = shapely.make_valid(g)
        return affinity.affine_transform(g, geographic).intersection(frame)
    land = unary_union([world(f) for f in layers['landwater'] if f['properties']['POLYGON']=='land'])
    water = unary_union([world(f) for f in layers['landwater'] if f['properties']['POLYGON']=='water'])
    shore = land.boundary.intersection(water.buffer(.5)).difference(frame.boundary.buffer(1)).simplify(3)
    width, height = style['width_in']*72, style['height_in']*72
    layout = page_layout(style)
    x0,y0,x1,y1 = layout['content_rect_pt']
    drawing = Drawing(width, height)
    def line(geometry, category, identity, color=BLACK, stroke=.65, name=''):
        if geometry.is_empty:
            return
        projected, mask = terrain.drape_line(geometry, stroke, category+'|'+str(identity)+'|'+name)
        if not projected.is_empty and not mask.is_empty:
            # Independent visible runs stay independent paint operations. One
            # difficult clipped end must not force every contour at this height
            # into the converter's multi-contour fallback policy.
            for run_line in parts(projected, 'LineString'):
                drawing.paint(run_line, category, identity, color=color, width=stroke, name=name)
                drawing.commit(stroke_clip(run_line, stroke))
    for level, contours in terrain.contours(style['contour_interval_m']).items():
        line(contours.intersection(land), 'terrain/contours', level, stroke=.65, name=f'{level:g} m')
    print('Contours composed', flush=True)
    parks = []
    for f in layers['parks']:
        g = world(f).intersection(land).simplify(5, preserve_topology=True)
        if g.area >= style['minimum_park_area_m2']:
            parks.append((f['properties'], g))
    for p, g in parks:
        area = terrain.drape_area(g)
        drawing.paint(area, 'parks/background', p['OBJECTID'], color=None, width=0, fill=WHITE, name=p['NAME'])
    drawing.commit()
    for p, g in parks:
        line(g.boundary, 'parks/boundary', p['OBJECTID'], color=GREEN, name=p['NAME'])
        # Hatch on the ground BEFORE projection; spacing and shape follow hills.
        gx0, gy0, gx1, gy1 = g.bounds
        spacing = style['park_hatch_spacing_m']
        reach = math.hypot(gx1-gx0, gy1-gy0)*2+spacing
        center = ((gx0+gx1)/2, (gy0+gy1)/2)
        strokes = []
        for angle in [-45, 45]:
            for offset in np.arange(-reach, reach+spacing, spacing):
                hatch = LineString([(center[0]-reach, center[1]+offset), (center[0]+reach, center[1]+offset)])
                strokes.append(affinity.rotate(hatch, angle, origin=center).intersection(g))
        line(unary_union(strokes), 'parks/draped_hatch', p['OBJECTID'], color=GREEN, name=p['NAME'])
    print('Parks composed', len(parks), flush=True)
    roads = defaultdict(list)
    for f in layers['streets']:
        p = f['properties']; name = p['STNAME_ORD'] or ''
        if p['ARTCLASS'] not in style['road_classes'] or any(s in name for s in [' RP','RAMP','TUNNEL']):
            continue
        g = world(f).simplify(4)
        if not g.is_empty:
            roads[name].append(g)
    for bridge in [False, True]:
        selected = [(name, merged_lines(gs)) for name,gs in sorted(roads.items()) if bool(re.search(r'\bBR\b', name)) == bridge]
        projected_roads = []
        for name, geometry in selected:
            projected, _ = terrain.drape_line(geometry, style['road_outer_width_pt'], 'roads|'+name)
            projected_roads.append((name, parts(projected, 'LineString')))
        for core in [False, True]:
            for name, runs in projected_roads:
                # Like contours, road lines retain their page width at every
                # viewing angle. White cores use the same visible centerlines.
                road_width = style['road_core_width_pt'] if core else style['road_outer_width_pt']
                for run_line in runs:
                    drawing.paint(run_line, 'bridges/core' if bridge and core else 'bridges/casing' if bridge else
                                  'roads/core' if core else 'roads/casing', name,
                                  color=WHITE if core else RED, width=road_width, name=name)
                    drawing.commit(stroke_clip(run_line, style['road_outer_width_pt']))
    print('Roads composed', len(roads), flush=True)
    line(shore, 'water/shorelines', 'shore', stroke=.8)
    drawing.paint(terrain.outline.boundary, 'terrain/edge', 'extent', width=.65)
    drawing.commit()
    viewport = box(*layout['label_frame_pt'])
    hidden_labels = []
    for label in style['labels']:
        lon, lat = label['location']
        p = affinity.affine_transform(shapely.Point(lon, lat), geographic)
        located = terrain.locate(p.x, p.y)
        if located is None or not located[1]:
            hidden_labels.append(label['text'])
            continue
        drawing.label(label['text'], located[0], viewport, size=label.get('size',11),
                      font=label.get('font','sans'), offset=label.get('offset_pt',[0,-12]),
                      leader=True, category='labels/terrain')
    drawing.commit()
    title_y = y0+drawing.fonts['sans'].ascender*30 if 'margins_mm' in style else y0+23
    drawing.text('SEATTLE / RELIEF', (x0,title_y),30)
    drawing.text('MAGNOLIA / QUEEN ANNE / CAPITOL HILL', (x0,y0+45),10)
    drawing.text(f"{style['vertical_exaggeration']:g}x HEIGHT  /  {style['camera_elevation_deg']:g} DEGREE VIEW  /  {style['contour_interval_m']:g} m CONTOURS",
                 (x0,y0+63),8.5)
    # North follows the ground projection, not the top edge of the sheet.
    north = terrain.projection[:,1]
    north = north/np.linalg.norm(north)*32
    base = np.array([x1-34, y0+49]); tip = base+north
    perpendicular = np.array([-north[1], north[0]])/32
    drawing.paint(LineString([base,tip,tip-north*.2+perpendicular*3,tip,
                              tip-north*.2-perpendicular*3]),'legend','north',width=.65)
    drawing.text('N',tuple(tip+[-3,-8]),9)
    bottom = y1+drawing.fonts['sans'].descender*8 if 'margins_mm' in style else y1
    y = bottom-43
    for x,color,label in [(x0,BLACK,'Terrain / shore'),(x0+199,RED,'Major roads'),(x0+369,GREEN,'Parks')]:
        start = x+1 if 'margins_mm' in style else x
        drawing.paint(LineString([(start,y-3),(x+22,y-3)]),'legend',label,color=color,width=.65)
        drawing.text(label,(x+29,y),9)
    drawing.text('View from the southwest. Labels stay upright; geographic features follow the ground.', (x0,bottom-21),8.5)
    drawing.text('Terrain: USGS 3DEP / Map: City of Seattle, SDOT, Seattle Parks and Recreation', (x0,bottom),8)
    drawing.text('Plotline',(x1-44,bottom),8)
    drawing.commit()
    drawing.doc.set_metadata(dict(title='Seattle / Relief', author='Plotline; USGS; City of Seattle'))
    drawing.doc.save(directory/'map.pdf', deflate=True, garbage=4)
    (directory/'map.svg').write_text(drawing.page.get_svg_image(text_as_path=True))
    drawing.doc.close()
    diagnostic = Drawing(width,height)
    for raw, visible in terrain.debug_contours:
        hidden = raw.difference(visible.buffer(.02))
        diagnostic.paint(hidden, 'diagnostic/hidden', 'contours', color=RED, width=.65)
        diagnostic.paint(visible, 'diagnostic/visible', 'contours', width=.65)
    diagnostic.paint(terrain.outline.boundary,'diagnostic','extent',width=.65)
    diagnostic.text('TERRAIN OCCLUSION / DIAGNOSTIC',(x0,title_y),26)
    diagnostic.text('Black: visible contours. Red: contours removed because nearer terrain hides them.',(x0,y0+49),11)
    diagnostic.text('The same elevation mesh and camera as the pen proof. Hidden contours never enter the plotted map.',(x0,bottom-18),9)
    diagnostic.commit()
    diagnostic.doc.save(directory/'occlusion.pdf',deflate=True,garbage=4)
    diagnostic.page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).save(directory/'occlusion.png')
    diagnostic.doc.close()
    before = sum(row['projected_length_pt'] for row in terrain.audit_lines)
    after = sum(row['visible_length_pt'] for row in terrain.audit_lines)
    metadata = dict(style=style, layout=layout, inputs=dict(elevation=dem_source, vectors=manifest),
                    terrain=terrain.stats, depth_audit=audit,
                    mesh_cell_m=[float(np.diff(terrain.xs).mean()),float(np.diff(terrain.ys).mean())],
                    elevation_range_m=[float(terrain.z.min()),float(terrain.z.max())],
                    visible_line_fraction=after/before, line_visibility=terrain.audit_lines,
                    labels=drawing.labels, hidden_labels=hidden_labels, omitted_labels=drawing.omitted,
                    issues=issues, geographic_transform=geographic,
                    limitations=['Orthographic, piecewise planar bare-earth terrain; not a building model.',
                                 'Bridges drape to bare earth/water; deck elevations and grade separation are not modeled.',
                                 'Linear map marks have constant page width; only their paths are projected and culled.',
                                 'Visible-run end cuts approximate tube occlusion; this is not a full 3D cylinder-surface intersection model.',
                                 'Labels are upright annotations anchored only to visible terrain.',
                                 'USGS samples are resampled to the recorded mesh; exaggeration is intentional.'])
    (directory/'map.provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    # Keep the visibility mesh for independent checks and inspection, not as PDF
    # paint. Only clipped vector map marks enter the converter.
    np.savez_compressed(directory/'terrain.npz', vertices=terrain.vertices, triangles=terrain.indices,
                        projection=terrain.projection, scale=terrain.scale, offset=terrain.offset,
                        camera=terrain.camera)
    (directory/'visible-faces.geojson').write_text(json.dumps(dict(type='FeatureCollection', features=[
        dict(type='Feature', properties=dict(triangle=i),geometry=mapping(g))
        for i,g in enumerate(terrain.visible) if not g.is_empty]),separators=(',',':')))
    print('Visibility', dict(retained_fraction=after/before, hidden_labels=hidden_labels, omitted_labels=drawing.omitted), flush=True)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--style', default='styles/seattle-relief-11x17.json')
    parser.add_argument('--name', default='seattle-relief-11x17')
    parser.add_argument('--compose-only', action='store_true')
    parser.add_argument('--reuse-map', action='store_true')
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name in ['.','..']:
        raise ValueError('Expected a single output name')
    output = ROOT/'build'/args.name
    style = json.loads((ROOT/args.style).read_text())
    if args.reuse_map:
        saved = json.loads((output/'source/map.provenance.json').read_text())
        if saved['style'] != style:
            raise ValueError('Saved source style differs; rebuild the map')
    else:
        compose(style, output/'source')
    if args.compose_only:
        return
    print(run('cargo','build','--release','--locked'))
    pdf, events = output/'source/map.pdf', output/'source/map.events.json'
    print(run('.venv/bin/python','tools/pdf_native.py',str(pdf),str(events)))
    print(run('target/release/plotline',str(events),str(output),str(style['pen_width_pt']),'40',
              'crosshatch','red-green-black','10',str(style['stroke_width_tolerance'])))
    validation = check_artifacts(args.name,str(pdf),dpi='216')
    (output/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    summary = summarize(output)
    print(run('mutool','draw','-q','-r','300','-o',str(output/'hpgl-300dpi.png'),str(output/'hpgl.svg')))
    print(run('mutool','draw','-q','-r','120','-o',str(output/'comparison.png'),str(output/'comparison.svg')))
    with pymupdf.open(output/'hpgl.svg') as drawing:
        (output/'hpgl.pdf').write_bytes(drawing.convert_to_pdf())
    print(f"PASS {args.name}: {summary['metrics']['pen_down_mm']/1000:.2f} m; interpretation {validation['interpretation_large_pixel_discrepancy_ratio']:.3%}")


if __name__ == '__main__':
    main()
