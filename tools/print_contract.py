#!/usr/bin/env python3
"""Offline validation and coordinate examples for the versioned PDF boundary."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from urllib.request import urlopen

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT/'contracts/v1'


def validate_schema(name, value):
    def finite(item):
        if isinstance(item,float) and not math.isfinite(item):
            raise ValueError('JSON numbers must be finite')
        if isinstance(item,dict):
            for v in item.values(): finite(v)
        elif isinstance(item,list):
            for v in item: finite(v)
    finite(value)
    schemas = [json.loads(p.read_text()) for p in DIRECTORY.glob('*.schema.json')]
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    schema = json.loads((DIRECTORY/f'{name}.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=registry).validate(value)


def placed_point(point, source_size, placement):
    """Source page top-left mm -> target sheet top-left mm, never ink centering."""
    x,y = point; w,h = source_size
    rotation = placement['rotation_deg']
    if rotation == 90:
        x,y = h-y,x
    elif rotation == 180:
        x,y = w-x,h-y
    elif rotation == 270:
        x,y = y,w-x
    elif rotation != 0:
        raise ValueError('Expected quarter-turn rotation')
    scale = placement['scale']; dx,dy = placement['offset_mm']
    return x*scale+dx, y*scale+dy


def device_point(point, profile):
    a,b,c,d,e,f = profile['calibration']['sheet_to_device_units']
    x,y = point
    return a*x+c*y+e, b*x+d*y+f


def validate_media(profile):
    validate_schema('media-profile', profile)
    w,h = profile['sheet_size_mm']; x,y,rw,rh = profile['printable_rect_mm']
    inset = profile['content_inset_mm']
    if x < 0 or y < 0 or min(rw,rh) <= 2*inset or x+rw > w+1e-7 or y+rh > h+1e-7:
        raise ValueError('Invalid printable rectangle/content inset')
    if 'calibration' in profile:
        cal = profile['calibration']; a,b,c,d,_,_ = cal['sheet_to_device_units']
        units = cal['units_per_mm']
        if not (math.isclose(math.hypot(a,b),units) and math.isclose(math.hypot(c,d),units)
                and abs(a*c+b*d) < 1e-7):
            raise ValueError('Calibration must preserve physical scale with orthogonal axes')
        if 'orientation_deg' in profile and cal['device']=='HP7585B':
            expected=[(units,0,0,-units),(0,units,units,0),
                      (-units,0,0,units),(0,-units,-units,0)][profile['orientation_deg']//90]
            if any(abs(actual-want)>1e-7 for actual,want in zip((a,b,c,d),expected)):
                raise ValueError('Media orientation disagrees with its calibration transform')
        left,bottom,right,top = cal['hard_clip_units']
        if left >= right or bottom >= top:
            raise ValueError('Invalid reported device limits')
        for point in [(x,y),(x+rw,y),(x,y+rh),(x+rw,y+rh)]:
            px,py = device_point(point,profile)
            if not (left-1e-6 <= px <= right+1e-6 and bottom-1e-6 <= py <= top+1e-6):
                raise ValueError('Printable rectangle exceeds device limits')


def raster_assignment_message(raster, missing):
    labels = dict(C='Cyan channel pen', M='Magenta channel pen', Y='Yellow channel pen',
                  K='Black treatment pen' if raster['black_treatment'] != 'off' else 'Black screening pen')
    optional_black = (missing == {'K'} and raster['black_treatment'] != 'off'
                      and (not raster['screening'] or raster['palette'] in ('selected', 'cmy')))
    return ('In Images & effects, choose ' + ', '.join(labels[role] for role in sorted(missing))
            + (' or set Black treatment to Off.' if optional_black else '.')
            + ' Pen names do not need to match these roles.')


def validate_intent(intent, pdf):
    validate_schema('print-intent',intent)
    if hashlib.sha256(Path(pdf).read_bytes()).hexdigest() != intent['source_sha256']:
        raise ValueError('Print intent is bound to a different PDF')
    inks = intent.get('palette',{}).get('inks',[])
    ids = [ink['id'] for ink in inks]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate document ink IDs')
    if any(ink['color_srgb'].lower() == '#ffffff' for ink in inks):
        raise ValueError('Exact PDF white is paper/occlusion, not a document ink')
    policies = intent.get('policies',{})
    if policies.get('raster') == 'dither':
        raster = policies['raster_settings']
        if not raster['screening'] and raster['black_treatment'] == 'off':
            raise ValueError('Enable screening or a black treatment, or choose Omit / Reserve blank space')
        if any(target not in ids for target in raster['inks'].values()):
            raise ValueError('Raster channel refers to an undefined document ink')
        if any(target not in ids for target in raster.get('screen_inks', [])):
            raise ValueError('Screening pen refers to an undefined document ink')
        required = set()
        if raster['screening'] and raster['palette'] not in ('auto', 'selected'):
            required.update({'k':'K','cmy':'CMY','cmyk':'CMYK'}[raster['palette']])
        if raster['black_treatment'] != 'off': required.add('K')
        if required - raster['inks'].keys():
            raise ValueError(raster_assignment_message(raster, required - raster['inks'].keys()))
    bindings = intent.get('bindings',[])
    mapping = intent.get('ink_mapping',{})
    if any(target not in ids for target in mapping.values()):
        raise ValueError('Ink mapping refers to an undefined output ink')
    names = [binding['ocg_name'] for binding in bindings]
    if len(names) != len(set(names)) or any(mapping.get(b['ink_id'],b['ink_id']) not in ids for b in bindings):
        raise ValueError('Duplicate layer binding or undefined ink')
    pages = intent['pages']; indices = [p['page_index'] for p in pages]
    if len(indices) != len(set(indices)):
        raise ValueError('Duplicate page selection')
    with pymupdf.open(pdf) as doc:
        layer_names = {g['name'] for g in doc.get_ocgs().values()}
        if not set(names) <= layer_names:
            raise ValueError('Binding references an absent PDF layer')
        for page in pages:
            if page['page_index'] >= len(doc):
                raise ValueError('Page selection out of range')
            rect = doc[page['page_index']].rect
            actual = [rect.width*25.4/72,rect.height*25.4/72]
            if any(abs(a-b) > .001 for a,b in zip(actual,page['source_size_mm'])):
                raise ValueError('Source page size does not match normalized PDF CropBox/Rotate')
            if 'target_media' in page:
                validate_media(page['target_media'])
    return intent


def capabilities(carousel, profiles):
    """Adapt today's read-only carousel response; slots are provenance, not ink IDs."""
    available = []
    for slot, pen in carousel['slots'].items():
        if pen['state'] != 'loaded' or pen['color'] is None:
            continue
        available.append(dict(id=f"inventory-{carousel['revision']}-{slot}",label=pen['label'] or pen['color'],
                              color_srgb=pen['color'],width_mm=pen['width_mm'],
                              source=dict(kind='plotline-carousel',revision=carousel['revision'],slot=int(slot))))
    result = dict(schema='plotline.capabilities/1',inventory_revision=carousel['revision'],
                  available_inks=available,media_profiles=profiles,
                  implemented=dict(pdf_upload=False,per_ink_widths=False,
                                   raster_policies=['reject'],fill_modes=['hatch','crosshatch']))
    validate_schema('capabilities',result)
    for profile in profiles:
        validate_media(profile)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    check = commands.add_parser('validate')
    check.add_argument('pdf',type=Path); check.add_argument('intent',type=Path)
    discover = commands.add_parser('capabilities')
    discover.add_argument('--server',default='http://127.0.0.1:9191')
    discover.add_argument('--media',type=Path,default=DIRECTORY/'examples/hp7585-tabloid-landscape.json')
    discover.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.command == 'validate':
        validate_intent(json.loads(args.intent.read_text()),args.pdf)
        print('PASS PDF/print-intent contract')
    else:
        with urlopen(args.server.rstrip('/')+'/api/carousel',timeout=10) as response:
            carousel = json.load(response)
        result = capabilities(carousel,[json.loads(args.media.read_text())])
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(args.output)
