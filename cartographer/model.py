"""Orthogonal, portable map recipe. Version one is migrated without rewriting saves."""
import copy
import hashlib
import json
import math
import re

from . import ROOT
from print_contract import validate_media, validate_schema

MM = 72 / 25.4
ROLES = ['labels', 'roads', 'bridges', 'buildings', 'parks', 'water', 'terrain', 'silhouettes', 'hillshade']
VECTOR_SOURCE = 'https://tiles.openfreemap.org/planet/20261025_080012_pt/{z}/{x}/{y}.pbf'


def furniture_options(recipe=None):
    defaults=dict.fromkeys(['title','compass','parameters','legend','credits','signature'],False)
    return (recipe or {}).get('furniture',defaults)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def media_profiles():
    calibrated = json.loads((ROOT / 'contracts/v1/examples/hp7585-tabloid-landscape.json').read_text())
    def profile(identity, label, size, rect):
        return dict(schema='plotline.media-profile/1', id=identity, revision=1,
                    label=label, sheet_size_mm=size, printable_rect_mm=rect, content_inset_mm=1)
    return [calibrated,
            profile('hp7585-letter-planned', 'Letter portrait · planned HP7585 margins',
                    [215.9, 279.4], [15, 39, 185.9, 225.4]),
            profile('tabloid-portrait-authoring', '11 × 17 portrait · authoring margins',
                    [279.4, 431.8], [15, 15, 249.4, 401.8]),
            profile('ansi-c-authoring', '22 × 17 landscape · authoring margins',
                    [558.8, 431.8], [15, 15, 528.8, 401.8])]


def style(emphasis='balanced'):
    features = dict(buildings='auto', building_min_mm2=.3, roads='all', bridges=True,
                    parks=True, park_treatment='hatch', hatch_spacing_mm=2.5,
                    shore='line', water_treatment='none', water_spacing_mm=2.5, labels='normal', contours=False,
                    contour_interval_m=100, park_labels=True, peak_labels=False, terrain_edges=True,
                    hillshade=False, hillshade_spacing_mm=2.5)
    roles = dict(labels='black', roads='black', bridges='red', buildings='red',
                 parks='green', water='black', terrain='black',silhouettes='black',hillshade='black')
    if emphasis == 'waterways':
        features.update(buildings='none', roads='major', shore='echo')
    elif emphasis == 'context':
        features.update(buildings='none', roads='major', park_treatment='outline', labels='sparse')
        roles = dict.fromkeys(ROLES, 'black')
    return dict(features=features, ink_roles=roles)


def default_recipe(emphasis='balanced'):
    inks = [dict(id=i, label=i.capitalize(), color_srgb=c, width_mm=.4/MM)
            for i,c in [('black','#000000'),('red','#ff0000'),('green','#008000')]]
    return dict(schema='plotline.map-recipe/2', title='Seattle', center=[-122.335,47.61],
                extent='viewport', bounds=[-122.43,47.56,-122.24,47.69],
                media=media_profiles()[0], framing=dict(scale_denominator=60000,offset_mm=[0,0]),
                camera=dict(relief=False,azimuth_deg=0,elevation_deg=35,exaggeration=3),
                furniture=dict(furniture_options(),title=True,compass=True,credits=True),
                lighting=dict(azimuth_deg=180,elevation_deg=45),
                **style(emphasis), outside=style('context'), boundary=None,
                palette=dict(schema='plotline.palette/1',id='map-palette',label='Document inks · modeled widths',inks=inks),
                data=dict(vector_template=VECTOR_SOURCE))


def migrate(recipe):
    result = copy.deepcopy(recipe)
    if result.get('schema') in ('plotline.map-recipe/1','plotline.map-recipe/2'):
        result.setdefault('furniture',furniture_options())
        result.setdefault('lighting',dict(azimuth_deg=180,elevation_deg=45))
        inks=result.get('palette',{}).get('inks',[])
        black=next((ink['id'] for ink in inks if ink.get('color_srgb','').lower()=='#000000'),None)
        for target in [result,result.get('outside',{})]:
            if not isinstance(target,dict):continue
            if isinstance(target.get('ink_roles'),dict):
                for role in ['silhouettes','hillshade']:
                    target['ink_roles'].setdefault(role,black or target['ink_roles'].get('labels'))
            if isinstance(target.get('features'),dict):
                features=target['features']
                if 'water_fill' in features:
                    old_fill=features.pop('water_fill')
                    if type(old_fill) is not bool:raise ValueError('water_fill must be true or false')
                    features.setdefault('water_treatment','fill' if old_fill else 'none')
                for k in ['park_labels','peak_labels','terrain_edges','hillshade','hillshade_spacing_mm','water_treatment','water_spacing_mm']:
                    target['features'].setdefault(k,style()['features'][k])
    if result.get('schema') != 'plotline.map-recipe/1':
        return result
    preset = result.pop('preset')
    result['schema'] = 'plotline.map-recipe/2'
    w,s,e,n = result['bounds']
    result.update(center=[(w+e)/2,(s+n)/2], extent='viewport', boundary=None,
                  outside=style('context'),data=dict(vector_template=VECTOR_SOURCE))
    result['camera']['relief'] = preset == 'relief'
    result['camera']['elevation_deg'] = max(10,result['camera']['elevation_deg'])
    result['camera']['azimuth_deg'] = (result['camera']['azimuth_deg']-180)%360 if preset == 'relief' else 0
    result['features']['buildings'] = 'auto' if result['features']['buildings'] else 'none'
    if preset != 'relief':
        result['features']['contours'] = False
    # Assign the legacy monochrome context to an existing ink, even if renamed.
    result['outside']['ink_roles'] = dict.fromkeys(ROLES,result['ink_roles']['labels'])
    return result


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be between {low:g} and {high:g}')


def keys(value, required, label):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError(f'Invalid {label} fields; expected {", ".join(required)}')


def validate_style(value, palette_ids):
    keys(value, ['features', 'ink_roles'], 'style')
    f=value['features']; keys(f, style()['features'], 'features')
    for k in ['bridges','parks','contours','park_labels','peak_labels','terrain_edges','hillshade']:
        if type(f[k]) is not bool:
            raise ValueError(f'{k} must be true or false')
    for k,values in [('buildings',['auto','all','none']),('roads',['none','major','all']),
                     ('park_treatment',['outline','hatch','concentric','fill']),('shore',['none','line','echo']),
                     ('water_treatment',['none','hatch','concentric','fill']),
                     ('labels',['none','sparse','normal'])]:
        if f[k] not in values:
            raise ValueError(f'Unknown {k} choice')
    for k,lo,hi in [('building_min_mm2',0,100),('hatch_spacing_mm',.05,15),('water_spacing_mm',.05,15),('contour_interval_m',10,2000),('hillshade_spacing_mm',.05,15)]:
        label={'hatch_spacing_mm':'Park spacing (mm)','water_spacing_mm':'Water spacing (mm)',
               'hillshade_spacing_mm':'Hillshade spacing (mm)'}.get(k,k)
        number(f[k],lo,hi,label)
    keys(value['ink_roles'],ROLES,'ink roles')
    if any(i not in palette_ids for i in value['ink_roles'].values()):
        raise ValueError('Every feature role must reference a document ink')


def validate_recipe(recipe):
    canonical(recipe)
    # Add optional artwork controls without rewriting an older saved revision.
    recipe=migrate(recipe)
    required=set(default_recipe())
    keys(recipe,required,'recipe')
    if recipe['schema'] != 'plotline.map-recipe/2':
        raise ValueError('Unknown recipe version; migrate version one before editing')
    if not isinstance(recipe['title'],str) or not 1 <= len(recipe['title'].strip()) <= 100 or any(ord(c)<32 for c in recipe['title']):
        raise ValueError('Title must contain 1–100 printable characters')
    furniture=furniture_options(recipe);keys(furniture,furniture_options(),'map furniture')
    if any(type(value) is not bool for value in furniture.values()):
        raise ValueError('Map furniture choices must be true or false')
    if not isinstance(recipe['center'],list) or len(recipe['center']) != 2:
        raise ValueError('Center must be [longitude, latitude]')
    number(recipe['center'][0],-180,180,'Center longitude'); number(recipe['center'][1],-84,84,'Center latitude')
    bounds=recipe['bounds']
    if not isinstance(bounds,list) or len(bounds)!=4:
        raise ValueError('Bounds must be [west, south, east, north]')
    for v in bounds: number(v,-180,180,'Bound')
    w,s,e,n=bounds
    if not (-85 <= s < n <= 85 and 0 < e-w <= 360):
        raise ValueError('Bounds need west < east and -85 ≤ south < north ≤ 85')
    if recipe['extent'] not in ('viewport','bounds'):
        raise ValueError('Extent must follow viewport or use explicit bounds')
    validate_media(recipe['media'])
    for v in recipe['media']['sheet_size_mm']: number(v,150,1250,'Sheet dimension in mm')
    layout(recipe)
    keys(recipe['framing'],['scale_denominator','offset_mm'],'framing')
    number(recipe['framing']['scale_denominator'],500,100000000,'Map scale denominator')
    offset=recipe['framing']['offset_mm']
    if not isinstance(offset,list) or len(offset)!=2: raise ValueError('Page offset needs x and y millimeters')
    for v in offset: number(v,-1000,1000,'Page offset')
    keys(recipe['camera'],['relief','azimuth_deg','elevation_deg','exaggeration'],'camera')
    if type(recipe['camera']['relief']) is not bool: raise ValueError('Relief must be true or false')
    for k,lo,hi in [('azimuth_deg',0,360),('elevation_deg',10,85),('exaggeration',.25,15)]:
        number(recipe['camera'][k],lo,hi,k)
    keys(recipe['lighting'],['azimuth_deg','elevation_deg'],'sun position')
    number(recipe['lighting']['azimuth_deg'],0,360,'Sun direction')
    number(recipe['lighting']['elevation_deg'],5,85,'Sun elevation')
    keys(recipe['data'],['vector_template'],'data source')
    if not re.fullmatch(r'https://tiles\.openfreemap\.org/planet/[a-zA-Z0-9_]+/\{z\}/\{x\}/\{y\}\.pbf',recipe['data']['vector_template']):
        raise ValueError('Unrecognized vector tile source')
    if recipe['boundary'] is not None:
        b=recipe['boundary']; keys(b,['name','geometry','source','sha256'],'boundary')
        if not isinstance(b['name'],str) or not 1<=len(b['name'])<=200: raise ValueError('Boundary needs a name')
        if not isinstance(b['source'],str) or len(b['source'])>1000: raise ValueError('Invalid boundary source')
        if digest(b['geometry'])!=b['sha256']: raise ValueError('Boundary snapshot checksum mismatch')
        from shapely.geometry import shape
        g=shape(b['geometry'])
        if g.geom_type not in ('Polygon','MultiPolygon') or g.is_empty or not g.is_valid:
            raise ValueError('Boundary must be a valid GeoJSON Polygon or MultiPolygon')
        if not (-180 <= g.bounds[0] < g.bounds[2] <= 180 and -85<=g.bounds[1]<g.bounds[3]<=85):
            raise ValueError('Boundary must use longitude/latitude coordinates within Mercator coverage')
    # Authoring can retain an unmeasured ink. Only export of a conversion palette
    # requires a known diameter. Never turn an inventory null into a guess.
    palette = copy.deepcopy(recipe['palette'])
    for ink in palette.get('inks', []):
        if ink.get('width_mm') is None:
            ink['width_mm'] = 1  # Schema shape check only; never stored or exported.
        else:
            number(ink.get('width_mm'), .01, 5, 'Measured ink diameter')
    validate_schema('palette', palette)
    ids = [i['id'] for i in palette['inks']]
    if len(set(ids)) != len(ids) or any(i['color_srgb'].lower() == '#ffffff' for i in palette['inks']):
        raise ValueError('Ink IDs must be unique; white is reserved for paper')
    validate_style(dict(features=recipe['features'],ink_roles=recipe['ink_roles']),ids)
    validate_style(recipe['outside'],ids)
    return recipe


def layout(recipe):
    x, y, w, h = recipe['media']['printable_rect_mm']
    inset = recipe['media']['content_inset_mm']
    content = [x+inset, y+inset, x+w-inset, y+h-inset]
    frame = list(content)
    if frame[2]-frame[0] < 50 or frame[3]-frame[1] < 50:
        raise ValueError('The printable area leaves too little room for the map')
    return dict(content_mm=content, frame_mm=frame, sheet_mm=recipe['media']['sheet_size_mm'])


def catalog():
    from .furniture import compass_artwork
    from .paper import paper_sizes, plotter_profiles
    return dict(recipe=default_recipe(), compass=compass_artwork(), styles=[dict(id=k,label=v,**style(k)) for k,v in
                [('balanced','Neighborhood'),('waterways','Waterways'),('context','Sparse context')]],
                media=media_profiles(),paper_sizes=paper_sizes(),plotter_media=plotter_profiles(),roles=ROLES,
                limitations=['OSM tiles are generalized by zoom; detail limits are reported per composition.',
                             'Boundary search: US cities, counties and states. Other polygons can be imported as GeoJSON.',
                             'Relief uses bare-earth Terrain Tiles; bridge decks and building heights are not modeled.'])
