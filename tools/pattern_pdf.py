#!/usr/bin/env python3
"""Pattern centerlines -> layered PDF + complete authoring intent. No plotting."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import pymupdf as fitz

from print_contract import validate_schema

LIMITS = json.loads((Path(__file__).resolve().parents[1]/'patterns/web/limits.json').read_text())
PRINT_POLICIES = json.loads((Path(__file__).resolve().parents[1]/'patterns/print-policies.json').read_text())


def overlap_strokes(recipe):
    return (recipe['family'] in PRINT_POLICIES['overlap_strokes'] or
            (recipe['family'] == 'tetrahedron' and recipe['params'].get('wireframe') == 1))


def check_geometry_size(paths, points):
    require(paths > 0, 'Drawing has no paths')
    size = paths*LIMITS['bytes_per_path'] + points*LIMITS['bytes_per_point']
    require(size <= LIMITS['working_memory_bytes'],
            f"Drawing needs about {math.ceil(size/1048576)} MiB of working memory "
            f"({paths} paths, {points} points); allowance is {LIMITS['working_memory_bytes']//1048576} MiB")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def export(scene, directory, kind="patterns"):
    require(kind in ("patterns", "mazes"), "Unknown authoring studio")
    label = "Maze" if kind == "mazes" else "Pattern"
    prefix = "maze" if kind == "mazes" else "pattern"
    recipe = scene['recipe']
    media = recipe['media']
    width, height = media['sheet_size_mm']
    require(all(isinstance(v, (int, float)) and math.isfinite(v) and 20 <= v <= 5080 for v in (width, height)), 'Invalid sheet size')
    x, y, w, h = media['printable_rect_mm']
    require(all(math.isfinite(v) for v in (x, y, w, h)) and x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= width+.001 and y+h <= height+.001, 'Invalid printable rectangle')
    inks = {ink['id']: ink for ink in recipe['inks']}
    require(len(inks) == len(recipe['inks']) and 1 <= len(inks) <= 64, 'Invalid document inks')
    roles = recipe['roles']
    require(1 <= len(roles) <= 64 and all(role['ink_id'] in inks for role in roles), 'Every role needs a document ink')
    check_geometry_size(len(scene['paths']), sum(len(p['points']) for p in scene['paths']))
    for p in scene['paths']:
        require(isinstance(p['role'], int) and 0 <= p['role'] < len(roles), 'Every stroke needs a role')
        ink = inks[roles[p['role']]['ink_id']]
        pen = ink['width_mm']
        require(isinstance(pen, (int, float)) and math.isfinite(pen) and .01 <= pen <= 5, 'Set an ink diameter before preparing')
        require(2 <= len(p['points']), 'A stroke needs at least two points')
        inset = max(1, media.get('content_inset_mm', 0)) + pen/2
        for point in p['points']:
            require(len(point) == 2 and all(math.isfinite(v) for v in point), 'Invalid point')
            px, py = point
            require(x+inset-1e-4 <= px <= x+w-inset+1e-4 and y+inset-1e-4 <= py <= y+h-inset+1e-4, 'Stroke footprint exceeds printable area')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    pt = 72/25.4
    page = doc.new_page(width=width*pt, height=height*pt)
    bindings = []
    # Separate marks keep native centerlines and explicit role attribution.
    used = sorted({p['role'] for p in scene['paths']})
    for role in used:
        ink = inks[roles[role]['ink_id']]
        name = f'{prefix}-role-{role}'
        ocg = doc.add_ocg(name)
        bindings.append(dict(ocg_name=name, ink_id=ink['id'], feature_group=roles[role]['label']))
        rgb = tuple(int(ink['color_srgb'][i:i+2], 16)/255 for i in (1, 3, 5))
        # Bound each PDF content stream. A single giant Shape repeatedly grows
        # strings and becomes quadratic for hundreds of thousands of paths.
        # Small spatial runs also bound each downstream GEOS paint operation;
        # one compound paint containing thousands of disjoint marks is costly.
        shape = page.new_shape();count = 0;subpaths = 0;paints = 0
        def finish():
            shape.finish(color=rgb, fill=None, width=ink['width_mm']*pt, lineCap=1, lineJoin=1, closePath=False, oc=ocg)
        for p in scene['paths']:
            if p['role'] == role:
                points = p['points']
                # Split long polylines only at a shared endpoint, preserving
                # continuity with the same round cap/join and physical ink.
                for start in range(0, len(points)-1, 2048):
                    part = points[start:start+2049]
                    shape.draw_polyline([(a*pt, b*pt) for a, b in part]);count += len(part);subpaths += 1
                    if count >= 2048 or subpaths >= 16:
                        finish();count = 0;subpaths = 0;paints += 1
                        # Many small paint operations per bounded content stream.
                        # Committing each paint separately grows /Contents
                        # quadratically in MuPDF's page wrapper.
                        if paints >= 32:
                            shape.commit();paints = 0
        if count:finish();paints += 1
        if paints:shape.commit()
    doc.set_metadata(dict(title=recipe['name'], creator=f'Plotline {label} Studio'))
    pdf = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    source = hashlib.sha256(pdf).hexdigest()
    used_inks = {roles[r]['ink_id'] for r in used}
    intent = dict(schema='plotline.print-intent/1', title=recipe['name'], source_sha256=source,
                  pages=[dict(page_index=0, source_size_mm=[width, height], placement=dict(scale=1, rotation_deg=0, offset_mm=[0, 0]), target_media=media)],
                  palette=dict(schema='plotline.palette/1', id=f'{prefix}-inks', label=f'{label} inks', inks=[ink for ink in recipe['inks'] if ink['id'] in used_inks]),
                  bindings=bindings,
                  policies=dict(fill=dict(mode='none'), stroke_width_tolerance=.75, stroke_width_mode='pen', thin_detail_thickening=.75,
                                raster='reject', gradients='omit', transparency='ignore-alpha', blend_modes='normal', soft_masks='reject'),
                  authoring=dict(application=f'{prefix}-studio', url=f'/{kind}?source_recipe='+scene['hash'], recipe_hash=scene['hash']))
    if scene.get('document_id'):
        intent['authoring']['document_id'] = scene['document_id']
    if kind == 'patterns' and overlap_strokes(recipe):
        intent['policies']['overlap_strokes'] = True
    validate_schema('print-intent', intent)
    (directory/'drawing.pdf').write_bytes(pdf)
    (directory/'intent.json').write_text(json.dumps(intent, indent=2)+'\n')
    doc.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--kind', choices=['patterns', 'mazes'], default='patterns')
    args = parser.parse_args()
    export(json.loads(args.scene.read_text()), args.directory, args.kind)
