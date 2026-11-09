#!/usr/bin/env python3
"""Plotline Studios's offline PDF worker. Never opens a plotter or a network socket."""
import argparse
import base64
from collections import Counter
from contextlib import contextmanager
from functools import lru_cache
import copy
import gc
import hashlib
import json
import math
import os
import time
from pathlib import Path
import subprocess
import sys

import pymupdf
import orjson
from pymupdf import mupdf as m
import pdf_native as native
from pdf_inks import census, source_style, compact_palette
from print_contract import ROOT, placed_point, validate_intent

PT = 72 / 25.4
VERSION = 'pdf-driver-1'
DEFAULT_POLICIES = dict(fill=dict(mode='crosshatch', spacing_mm=2, angle_deg=45),
                        stroke_width_tolerance=.75, raster='reject', gradients='reject',
                        transparency='reject', blend_modes='reject', soft_masks='reject')
SUPPORTED = dict(raster=['reject', 'omit', 'reserve', 'placeholder', 'dither'], gradients=['reject', 'omit'],
                 transparency=['reject', 'ignore-alpha', 'omit'], blend_modes=['reject', 'normal', 'omit'],
                 soft_masks=['reject'])
IMAGE_ALPHA_MESSAGE = ('This image has per-pixel transparency, even if its object opacity is 100%. '
                       'In Images & effects, set Transparency to "Treat as opaque" to draw it '
                       'without that transparency, or choose "Omit transparent paints".')


def write_json(path, value, *, compact=False):
    if not compact:
        path.write_text(json.dumps(value, allow_nan=False, indent=2) + '\n')
        return
    encoded = orjson.dumps(value, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_APPEND_NEWLINE)
    # orjson encodes NaN/Infinity as null. Preserve allow_nan=False, without a
    # Python walk of millions of coordinates on the normal finite/null-free path.
    # A legitimate null (or "null" in text) can trigger this slower validation;
    # only non-finite numbers fail. Validate before touching the destination.
    if b'null' in encoded:
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('Non-finite number in JSON')
            if isinstance(item, dict):
                pending.extend(item.keys()); pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
    path.write_bytes(encoded)


def progress(directory, context, phase, label):
    """One writer at a time: Python phases surround the Rust child phases."""
    temporary=directory/'progress.tmp'
    try:
        write_json(temporary,dict(version=1,phase=phase,label=label,completed=0,total=0,unit='',
                                  updated_at=time.time(),phase_elapsed_seconds=0,**context))
        temporary.replace(directory/'progress.json')
    except OSError:
        pass  # Progress is optional; source/artifact failures remain fatal.


def rect_path(x, y, w, h):
    return [dict(op='move', p=[x,y]), dict(op='line', p=[x+w,y]),
            dict(op='line', p=[x+w,y+h]), dict(op='line', p=[x,y+h]), dict(op='close')]


def multiply(a, b):
    """Compose column-vector affine matrices: a after b."""
    return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]]


def placement_matrix(page):
    size, placement = page['source_size_mm'], page['placement']
    origin = placed_point((0,0), size, placement)
    x = placed_point((1,0), size, placement); y = placed_point((0,1), size, placement)
    return [x[0]-origin[0], x[1]-origin[1], y[0]-origin[0], y[1]-origin[1], origin[0]*PT, origin[1]*PT]


class PolicyDevice(native.ExtractDevice):
    def __init__(self, policies, bindings, placeholder_width):
        super().__init__()
        self.policies, self.bindings = policies, bindings
        self.placeholder_width = placeholder_width
        self.ink_bindings = {}
        self.warnings = Counter()
        self.groups = []
        self.images = {}

    def skip(self, alpha=1):
        if any(self.groups):
            return True
        if alpha != 1:
            policy = self.policies['transparency']
            if policy != 'reject':
                self.warnings['transparency: '+policy] += 1
            return policy == 'omit'
        return False

    def color(self, cs, values, alpha, params):
        if self.policies['transparency'] != 'reject':
            alpha = 1
        return super().color(cs, values, alpha, params)

    def fill_path(self, ctx, path, even_odd, ctm, cs, color, alpha, params):
        if not self.skip(alpha):
            super().fill_path(ctx, path, even_odd, ctm, cs, color, alpha, params)

    def stroke_path(self, ctx, path, stroke, ctm, cs, color, alpha, params):
        if not self.skip(alpha):
            super().stroke_path(ctx, path, stroke, ctm, cs, color, alpha, params)

    def fill_text(self, ctx, text, ctm, cs, color, alpha, params):
        if not self.skip(alpha):
            super().fill_text(ctx, text, ctm, cs, color, alpha, params)

    def stroke_text(self, ctx, text, stroke, ctm, cs, color, alpha, params):
        if not self.skip(alpha):
            super().stroke_text(ctx, text, stroke, ctm, cs, color, alpha, params)

    def begin_group(self, ctx, bbox, cs, isolated, knockout, blendmode, alpha):
        omit = self.skip(alpha)
        if knockout:
            self.unsupported('knockout compositing group')
        if blendmode != m.FZ_BLEND_NORMAL:
            policy = self.policies['blend_modes']
            if policy == 'reject':
                self.unsupported('blend mode', blendmode)
            else:
                self.warnings['blend mode: '+policy] += 1
                omit |= policy == 'omit'
        if alpha != 1 and self.policies['transparency'] == 'reject':
            self.unsupported('group alpha', alpha)
        self.groups.append(omit)

    def end_group(self, *args):
        self.groups.pop()

    def fill_shade(self, *args):
        if self.skip():
            return
        if self.policies['gradients'] == 'omit':
            self.warnings['gradient omitted; underlying paint revealed'] += 1
        else:
            self.unsupported('gradient')

    def clip_image_mask(self, ctx, image, ctm, scissor):
        # MuPDF brackets an image with its alpha mask and pop_clip. Even when
        # ignoring/omitting alpha we must preserve that stack entry: otherwise
        # pop_clip would remove an enclosing vector or page clip instead.
        opaque = (not self.skip() and self.policies['raster'] not in ('omit', 'reject')
                  and self.policies['transparency'] == 'ignore-alpha')
        self.events.append(dict(kind='clip_fill', path=rect_path(0,0,1 if opaque else 0,1 if opaque else 0),
                                matrix=native.matrix(ctm), even_odd=False))
        if (not self.skip() and self.policies['raster'] not in ('omit', 'reject')
                and self.policies['transparency'] == 'reject'):
            self.unsupported('Image transparency', IMAGE_ALPHA_MESSAGE)

    def fill_image(self, ctx, image, ctm, alpha, params):
        if self.skip(alpha):
            return
        policy = self.policies['raster']
        if policy == 'omit':
            self.warnings['raster omitted; underlying paint revealed'] += 1
            return
        if policy == 'reject':
            self.unsupported('raster image',
                'Raster images is set to "Require a decision". Open Images & effects, '
                'choose "Draw with pens", and assign channel pens. You can also choose '
                '"Omit", "Reserve blank space", or "Box with an X".')
            return
        # Image alpha follows the existing explicit transparency policy. The
        # decoder reads the color image separately from its associated mask.
        # Stencils and color-key masks require different decoding semantics.
        if image.imagemask or image.use_colorkey:
            self.unsupported('Image stencil or color-key transparency',
                             'Export this image flattened onto its intended background, then import it again.')
            return
        if image.mask:
            transparency = self.policies['transparency']
            if transparency == 'reject':
                self.unsupported('Image transparency', IMAGE_ALPHA_MESSAGE)
                return
            self.warnings['image transparency: '+transparency] += 1
            if transparency == 'omit':
                return
        if alpha != 1 and self.policies['transparency'] == 'reject':
            self.unsupported('image alpha', alpha)
            return
        if params.op:
            self.unsupported('image overprint')
            return
        transform = native.matrix(ctm)
        if policy == 'dither':
            from pdf_raster import pixels
            data = pixels(image, params)
            key = hashlib.sha256(data).hexdigest()
            self.images.setdefault(key, data)
            self.events.append(dict(kind='image', matrix=transform, pixels_key=key))
            return
        self.events.append(dict(kind='fill', path=rect_path(0,0,1,1), matrix=transform,
                                even_odd=False, color=[1,1,1]))
        if policy == 'placeholder':
            # Stroke in page coordinates so anisotropic images don't stretch the pen.
            corners = [(0,0),(1,0),(1,1),(0,1),(0,0)]
            def point(p):
                return [transform[0]*p[0]+transform[2]*p[1]+transform[4],
                        transform[1]*p[0]+transform[3]*p[1]+transform[5]]
            path = [dict(op='move' if i == 0 else 'line', p=point(p)) for i,p in enumerate(corners)]
            for a,b in [((0,0),(1,1)), ((1,0),(0,1))]:
                path.extend([dict(op='move',p=point(a)), dict(op='line',p=point(b))])
            self.events.append(dict(kind='stroke',path=path,matrix=[1,0,0,1,0,0],color=[0,0,0],
                                    style=dict(width=self.placeholder_width,cap='Round',join='Round',
                                               miter_limit=10,dash_phase=0,dashes=[])))
        self.warnings['raster: '+policy] += 1

    def end_layer(self, *args):
        name, start = self.layers[-1]
        # Innermost *bound* group wins, even under an unbound group.
        if name in self.bindings:
            for index in range(start, len(self.events)):
                self.ink_bindings.setdefault(index, self.bindings[name])
        super().end_layer(*args)


# Keep MuPDF's recovery from hiding a Python callback failure, including overrides.
for _name in native.CALLBACKS:
    if _name in PolicyDevice.__dict__:
        setattr(PolicyDevice, _name, native.guarded(getattr(PolicyDevice, _name)))


def inspect(pdf, directory):
    from pdf_preview import render_page, filename
    with pymupdf.open(pdf) as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError('Expected an unencrypted PDF')
        if len(doc) > 200:
            raise ValueError('Review currently supports up to 200 pages; original retained')
        pages = []
        for index, page in enumerate(doc):
            rect = page.rect
            render_page(page).save(directory/filename(index))
            pages.append(dict(page_index=index,source_size_mm=[rect.width/PT,rect.height/PT],
                              media_box_pt=list(page.mediabox),crop_box_pt=list(page.cropbox),rotation_deg=page.rotation,
                              image_paints=sum(kind == 'fill-image' for kind,*_ in page.get_bboxlog()),
                              path_paints=sum(kind in ('fill-path','stroke-path') for kind,*_ in page.get_bboxlog()),
                              text_spans=len(page.get_texttrace()),source_styles=census(page)))
        result = dict(pages=pages,engine=f'MuPDF {pymupdf.mupdf_version}', metadata=doc.metadata,
                      layers=sorted({group['name'] for group in doc.get_ocgs().values()}))
        if (directory/'intent.json').exists():
            try:
                result['intent']=validate_intent(json.loads((directory/'intent.json').read_text()),pdf)
            except Exception as error:
                result['intent_error']=str(error)
        write_json(directory/'inspection.json',result)
        return result


def resolve_intent(pdf, intent, inventory=None):
    """Resolve authored inks, independent of the current physical inventory.

    ``source`` is historical provenance, not a slot assignment. The optional
    inventory argument remains for older callers; physical matching happens
    when Plotline Studios prepares a plot, using the current carousel.
    """
    validate_intent(intent, pdf)
    result = copy.deepcopy(intent)
    if not result.get('palette'):
        raise ValueError('Choose a document palette with known pen diameters')
    result.setdefault('policies',copy.deepcopy(DEFAULT_POLICIES))
    policies = result['policies']
    for key, values in SUPPORTED.items():
        if policies[key] not in values:
            raise ValueError(f'{key} policy {policies[key]!r} is not implemented')
    for page in result['pages']:
        if 'target_media' not in page:
            w,h = page['source_size_mm']
            page['target_media'] = dict(schema='plotline.media-profile/1',id='pdf-page',revision=1,
                                       label='PDF page (uncalibrated)',sheet_size_mm=[w,h],
                                       printable_rect_mm=[0,0,w,h],content_inset_mm=0)
        if max(page['source_size_mm'])*page['placement']['scale'] > 10000:
            raise ValueError('Placed page exceeds the 10 m review limit')
        if max(abs(v) for v in page['placement']['offset_mm']) > 10000:
            raise ValueError('Page offset exceeds the 10 m review limit')
    return result


def ink_selector(inks):
    """Page-local color matching; widths only break ties between nearest colors."""
    colors = [tuple(int(ink['color_srgb'][i:i+2],16)/255 for i in (1,3,5)) for ink in inks]

    @lru_cache(maxsize=256)
    def candidates(color):
        errors = [sum((v-target[i])**2 for i,v in enumerate(color)) for target in colors]
        best = min(errors)
        return tuple(i for i,error in enumerate(errors) if abs(error-best) < 1e-12)

    def choose(event):
        nearest = candidates(tuple(event['color']))
        if len(nearest) == 1:
            return nearest[0] + 1
        if event['kind'] == 'stroke':
            a,b,c,d,*_ = event['matrix']
            width = event['style']['width']*math.sqrt(abs(a*d-b*c))/PT
            return min(nearest,key=lambda i:abs(inks[i]['width_mm']-width)) + 1
        return min(nearest,key=lambda i:inks[i]['width_mm']) + 1

    return choose


def prepare_events(events, device, selection, inks, mapping=None, raster_outputs=None):
    # Paths/styles are immutable here. Own the records we edit (matrix, sources,
    # assignments), rather than recursively cloning millions of path coordinates.
    events = {**events, 'events':[dict(event) for event in events['events']],
              'sources':dict(events.get('sources',{}))}
    affine = placement_matrix(selection)
    sw,sh = selection['source_size_mm']; media = selection['target_media']
    width,height = media['sheet_size_mm']; x,y,w,h = media['printable_rect_mm']
    prefix = [dict(kind='clip_fill',path=rect_path(x*PT,y*PT,w*PT,h*PT),matrix=[1,0,0,1,0,0],even_odd=False),
              dict(kind='clip_fill',path=rect_path(0,0,sw*PT,sh*PT),matrix=affine,even_odd=False)]
    assignments = {}; ids = {ink['id']:i+1 for i,ink in enumerate(inks)}
    choose_ink = ink_selector(inks)
    mapping = mapping or {}
    output = list(prefix)
    sources = {}
    for index,event in enumerate(events['events']):
        if event['kind'] == 'image':
            transform = multiply(affine,event['matrix'])
            output.append(dict(kind='fill',path=rect_path(0,0,1,1),matrix=transform,
                               even_odd=False,color=[1,1,1]))
            if raster_outputs is None or index not in raster_outputs:
                raise ValueError('Missing raster paths for PDF image')
            for channel in raster_outputs[index]:
                channel = dict(channel)
                assignments[len(output)] = ids[channel.pop('ink_id')]
                sources[len(output)] = channel.pop('source')
                output.append(channel)
            continue
        source_id = source_style(event)['id'] if mapping and event['kind'] in ('fill','stroke') else None
        if 'matrix' in event:
            event['matrix'] = multiply(affine,event['matrix'])
        feature = events.get('sources',{}).get(index)
        if event['kind'] in ('fill','stroke') and event['color'] != [1,1,1]:
            bound = device.ink_bindings.get(index)
            source_id = bound['ink_id'] if bound else source_id
            assignments[len(output)] = ids[mapping[source_id]] if source_id in mapping else ids[bound['ink_id']] if bound else choose_ink(event)
            if bound and 'feature_group' in bound:
                feature = bound['feature_group']
        if feature is not None: sources[len(output)] = feature
        output.append(event)
    events['sources'] = sources
    events['events'] = output + [dict(kind='pop_clip'),dict(kind='pop_clip')]
    events['bounds'] = [0,0,width*PT,height*PT]
    events['inks'] = [dict(id=ink['id'],color=[int(ink['color_srgb'][i:i+2],16)/255 for i in (1,3,5)],
                          width=ink['width_mm']*PT) for ink in inks]
    events['paint_inks'] = assignments
    return compact_palette(events)


def review_svgs(directory, pdf, selection, *, keep_intermediates=True):
    sw,sh = selection['source_size_mm']; media = selection['target_media']
    w,h = media['sheet_size_mm']; x,y,rw,rh = media['printable_rect_mm']
    corners = [placed_point(p,[sw,sh],selection['placement']) for p in [(0,0),(sw,0),(sw,sh),(0,sh)]]
    xs=[0,w]+[p[0] for p in corners]; ys=[0,h]+[p[1] for p in corners]
    bounds = [min(xs)-5,min(ys)-5,max(xs)+5,max(ys)+5]
    vx,vy,vw,vh=bounds[0]*PT,bounds[1]*PT,(bounds[2]-bounds[0])*PT,(bounds[3]-bounds[1])*PT
    header=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}"><rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="#e2e8ed"/>'
    sheet=f'<rect width="{w*PT}" height="{h*PT}" fill="white" stroke="#56616c" stroke-width="1"/>'
    outline=f'<rect width="{w*PT}" height="{h*PT}" fill="none" stroke="#56616c" stroke-width="1"/>'
    outline+=f'<rect x="{x*PT}" y="{y*PT}" width="{rw*PT}" height="{rh*PT}" fill="none" stroke="#5b6fd8" stroke-width="1.2" stroke-dasharray="5 4"/>'
    polygon=' '.join(f'{a*PT},{b*PT}' for a,b in corners)
    outline+=f'<polygon points="{polygon}" fill="none" stroke="#56616c" stroke-width="1" stroke-dasharray="2 3"/>'
    with pymupdf.open(pdf) as doc:
        from pdf_preview import render_page, PLACED_EDGE
        page=doc[selection['page_index']]
        pix=render_page(page,edge=PLACED_EDGE,alpha=True)
        data=base64.b64encode(pix.tobytes('png')).decode()
    affine=' '.join(str(v) for v in placement_matrix(selection))
    source=f'<image width="{sw*PT}" height="{sh*PT}" transform="matrix({affine})" href="data:image/png;base64,{data}"/>'
    (directory/'source.svg').write_text(header+sheet+source+outline+'</svg>')
    for original,name in [('coverage.svg','logical.svg'),('hpgl.svg','pen.svg')]:
        svg=(directory/original).read_text()
        # Nested SVG retains its original full-sheet coordinate system.
        (directory/name).write_text(header+sheet+svg.split('>',1)[1].rsplit('</svg>',1)[0]+outline+'</svg>')
    if (directory/'detail-loss.svg').exists():
        loss=(directory/'detail-loss.svg').read_text()
        # Transparent overlay shares the exact source/logical/pen review viewBox.
        (directory/'loss.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}">'+loss.split('>',1)[1].rsplit('</svg>',1)[0]+'</svg>')
    if not keep_intermediates:
        for original in ['coverage.svg','hpgl.svg']:
            (directory/original).unlink()
    return dict(source_exceeds_sheet=any(a<-.001 or b<-.001 or a>w+.001 or b>h+.001 for a,b in corners),
                source_exceeds_printable=any(a<x-.001 or b<y-.001 or a>x+rw+.001 or b>y+rh+.001 for a,b in corners),
                placement=selection['placement'],media=media)


@contextmanager
def deferred_cyclic_gc():
    # Synchronous, single-threaded worker: the event tree contains owned
    # lists/dicts, not cycles. Keep refcount/native releases active, but postpone
    # cyclic scans until the entire temporary page graph has been released.
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


def extract_events(pdf, page_number, device):
    with deferred_cyclic_gc():
        return native.extract(pdf, page_number, device_factory=lambda:device)


def convert(pdf, intent, directory, converter, *, artifacts='preview'):
    if artifacts not in ['preview','full']:
        raise ValueError('Unknown artifact mode')
    inks=intent['palette']['inks']; policies=intent['policies']; fill=policies['fill']
    bindings={item['ocg_name']:item for item in intent.get('bindings',[])}
    settings=dict(pen_width=min(ink['width_mm'] for ink in inks)*PT,
                  flatten_tolerance=min(.01,min(ink['width_mm'] for ink in inks)*PT/10),
                  units_per_mm=40,fill_style=fill['mode'],hatch_spacing=fill.get('spacing_mm',2)*PT,
                  hatch_angle=fill.get('angle_deg',45),feather_distance=fill.get('feather_distance_mm',12.7)*PT,
                  stroke_width_tolerance=policies['stroke_width_tolerance'])
    if policies.get('thin_detail_thickening',0):settings['thin_detail_thickening']=policies['thin_detail_thickening']
    if policies.get('stroke_width_mode')=='pen':settings['stroke_width_mode']='pen'
    if policies.get('overlap_strokes'):settings['overlap_strokes']=True
    pages=[]; warnings=[]
    for ordinal,selection in enumerate(intent['pages'],1):
        index=selection['page_index']; output=directory/f'page-{index}'; output.mkdir()
        context=dict(page_index=index,page_number=ordinal,page_count=len(intent['pages']))
        progress(directory,context,'extracting','Reading PDF vectors')
        with deferred_cyclic_gc():
            device=PolicyDevice(policies,bindings,settings['pen_width'])
            events,probe=extract_events(pdf,index+1,device)
            unsupported=[e for e in events['events'] if e['kind']=='unsupported']
            if artifacts=='full':
                write_json(output/'extraction.json',events,compact=True)
            if unsupported:
                messages = [f"{e['feature']}: {e['detail']}" if e['detail'] else e['feature']
                            for e in unsupported]
                raise NotImplementedError('; '.join(dict.fromkeys(messages)))
            if device.groups:
                raise ValueError('Unbalanced PDF compositing groups')
            progress(directory,context,'preparing','Applying page placement and ink policies')
            raster_outputs = None
            raster_reports = []
            if device.images:
                from pdf_raster import generate
                raster_outputs,raster_reports = generate(events,device,placement_matrix(selection),inks,output,directory/'progress.json',context,keep_intermediates=artifacts=='full')
            prepared=prepare_events(events,device,selection,inks,intent.get('ink_mapping'),raster_outputs)
            write_json(output/'events.json',prepared,compact=True); write_json(output/'settings.json',settings)
            engine=events['engine']
            warning=[f'{count} × {message}' for message,count in device.warnings.items()]
            # Only serialized inputs are needed by the child. Release the large
            # coordinate graph before GEOS starts allocating its own geometry.
            del events,prepared,probe,device
        environment={**os.environ,'PLOTLINE_PROGRESS_FILE':str((directory/'progress.json').resolve()),
                     'PLOTLINE_PROGRESS_CONTEXT':json.dumps(context)}
        with (output/'converter.log').open('w') as log:
            result=subprocess.run([str(converter),str(output/'events.json'),str(output),'--settings',str(output/'settings.json'),'--artifacts',artifacts],
                                  stdout=log,stderr=subprocess.STDOUT,text=True,timeout=600,env=environment)
        if result.returncode:
            with (output/'converter.log').open('rb') as log:
                log.seek(max(0,log.seek(0,2)-5000))
                raise RuntimeError(log.read().decode(errors='replace'))
        progress(directory,context,'previews','Rendering page previews and mileage')
        summary=json.loads((output/'summary.json').read_text())
        if summary.get('schema')!='plotline.conversion-summary/1':
            raise ValueError('Unsupported converter summary')
        warnings.extend(warning)
        pages.append(dict(page_index=index,geometry=review_svgs(output,pdf,selection,keep_intermediates=artifacts=='full'),
                          metrics=summary['metrics'],by_ink=summary['by_ink'],by_feature=summary['by_feature'],
                          visible_paints_without_paths=summary['visible_paints_without_paths'],warnings=warning))
        if 'detail_loss' in summary:pages[-1]['detail_loss']=summary['detail_loss']
        if raster_reports:pages[-1]['raster_images']=raster_reports
    result=dict(worker_version=VERSION,artifact_mode=artifacts,engines=dict(pdf=engine,geometry=summary['geos']),source_sha256=intent['source_sha256'],pages=pages,warnings=warnings,
                physical_plotting=False,notes=['Artifact HPGL uses sheet coordinates; Plotline Studios registers it to the device when updating a plot draft.'])
    write_json(directory/'result.json',result)
    progress(directory,context,'complete','Preview complete')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['inspect','import-image','import-svg','styles','validate','convert'])
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--intent',type=Path)
    parser.add_argument('--inventory',type=Path,help='Legacy argument; document conversion does not read the live inventory')
    parser.add_argument('--artifacts',choices=['preview','full'],default='preview',help='Preview files or complete diagnostic/replay bundle; verification is always enabled')
    parser.add_argument('--converter',type=Path,default=ROOT/'target/release/plotline')
    args=parser.parse_args(); args.directory.mkdir(exist_ok=True,parents=True)
    if args.command in ('import-image','import-svg'):
        (args.directory/'failure.json').unlink(missing_ok=True)
        try:
            from pdf_input import image_pdf
            pdf=args.directory/'source.pdf'
            if args.command=='import-svg':
                from svg_input import svg_pdf
                metadata=svg_pdf(args.pdf,pdf)
                key='svg_input'
            else:
                metadata=image_pdf(args.pdf,pdf)
                key='image_input'
            result=inspect(pdf,args.directory)
            result[key]=metadata
            write_json(args.directory/'inspection.json',result)
        except ValueError as error:
            write_json(args.directory/'failure.json',dict(status='invalid_request',error=str(error)))
            raise
    elif args.command=='inspect':
        inspect(args.pdf,args.directory)
    elif args.command=='styles':
        with pymupdf.open(args.pdf) as doc:
            result=[dict(page_index=i,source_styles=census(page)) for i,page in enumerate(doc)]
        write_json(args.directory/'source-styles.json',result)
    elif args.command=='validate':
        (args.directory/'failure.json').unlink(missing_ok=True)
        try:
            resolved=resolve_intent(args.pdf,json.loads(args.intent.read_text()))
        except ValueError as error:
            # Expected recipe errors are operator guidance, not a Python
            # traceback in the UI. Keep the traceback in the worker log.
            write_json(args.directory/'failure.json',dict(status='invalid_request',error=str(error)))
            raise
        write_json(args.directory/'effective-intent.json',resolved)
    else:
        try:
            convert(args.pdf,json.loads(args.intent.read_text()),args.directory,args.converter,artifacts=args.artifacts)
        except Exception as error:
            write_json(args.directory/'failure.json',dict(status='unsupported' if isinstance(error,NotImplementedError) else 'error',error=str(error)))
            raise

if __name__=='__main__':
    main()
