#!/usr/bin/env python3
"""Native MuPDF extraction probe, using the bindings shipped with PyMuPDF.

Emits the same owned JSON events as the JS adapter. Glyphs use the PDF engine's
own outlines and placement, not replacement fonts. Unsupported paint fails later
in the Rust compiler; callback exceptions fail extraction itself.
"""
import argparse
from collections import Counter
import functools
import json
import math
from pathlib import Path
import pymupdf
from pymupdf import mupdf as m


def matrix(value):
    return [value.a, value.b, value.c, value.d, value.e, value.f]


class PathWalker(m.FzPathWalker2):
    def __init__(self):
        super().__init__()
        self.commands = []
        for name in ['moveto', 'lineto', 'curveto', 'closepath']:
            getattr(self, 'use_virtual_' + name)()

    def read(self, raw):
        # The callback's path is borrowed; retain it for the native walk. Reuse
        # the director, but transfer a fresh command list to every event.
        owned = m.FzPath(m.ll_fz_keep_path(raw))
        self.commands = []
        try:
            m.fz_walk_path(owned, self, self.m_internal)
            return self.commands
        finally:
            self.commands = []

    def moveto(self, _ctx, x, y):
        self.commands.append(dict(op='move', p=[x, y]))

    def lineto(self, _ctx, x, y):
        self.commands.append(dict(op='line', p=[x, y]))

    def curveto(self, _ctx, x1, y1, x2, y2, x3, y3):
        self.commands.append(dict(op='cubic', p1=[x1, y1], p2=[x2, y2], p3=[x3, y3]))

    def closepath(self, _ctx):
        self.commands.append(dict(op='close'))


def stroke_data(s):
    if not (s.start_cap == s.end_cap == s.dash_cap):
        raise ValueError('Unequal stroke caps need a policy')
    return dict(width=s.linewidth, cap=['Butt', 'Round', 'Square', 'Triangle'][s.start_cap],
                join=['Miter', 'Round', 'Bevel', 'MiterXPS'][s.linejoin],
                miter_limit=s.miterlimit, dash_phase=s.dash_phase,
                dashes=[m.floats_getitem(s.dash_list, i) for i in range(s.dash_len)])


def make_pattern_clips_explicit(doc):
    """Enforce each pattern cell's PDF BBox when bypassing the raster tile cache.

    MuPDF's direct repetition path does not push the tile-cache BBox clip.
    Prefixing a local clip makes that existing PDF constraint explicit. Only
    the in-memory document changes; the input PDF is never saved or rewritten.
    """
    pdf = m.pdf_document_from_fz_document(doc.this)
    patterns = []
    for xref in range(1, doc.xref_length()):
        obj = m.pdf_load_object(pdf, xref)
        if obj.pdf_is_dict() and m.pdf_to_int(m.pdf_dict_gets(obj, "PatternType")) == 1:
            box = m.pdf_to_rect(m.pdf_dict_gets(obj, "BBox"))
            bounds = [box.x0, box.y0, box.x1, box.y1]
            if not all(math.isfinite(v) for v in bounds) or box.x1 <= box.x0 or box.y1 <= box.y0:
                raise ValueError(f"Invalid tiling pattern BBox at object {xref}")
            content = doc.xref_stream(xref)
            if content is None:
                raise ValueError(f"Tiling pattern is not a stream at object {xref}")
            clip = f"q {box.x0:.9f} {box.y0:.9f} {box.x1-box.x0:.9f} {box.y1-box.y0:.9f} re W n\n".encode()
            doc.update_stream(xref, clip + content + b"\nQ\n")
            patterns.append(dict(xref=xref, bbox=bounds))
    return patterns


class ExtractDevice(m.FzDevice2):
    def __init__(self):
        super().__init__()
        # Ask the PDF interpreter to repeat pattern cells as ordinary callbacks.
        # Placement, inherited color and clipping stay with MuPDF, not our code.
        m.fz_enable_device_hints(self, m.FZ_NO_TILING)
        self.events = []
        self.sources = {}
        self.layers = []
        self.glyphs = []
        self.errors = []
        self.color_spaces = Counter()
        self.path_walker = PathWalker()
        for name in CALLBACKS:
            getattr(self, 'use_virtual_' + name)()

    def unsupported(self, feature, detail=''):
        self.events.append(dict(kind='unsupported', feature=feature, detail=str(detail)))

    def color(self, cs, values, alpha, params):
        if alpha != 1:
            self.unsupported('paint alpha', alpha)
        if params.op:
            self.unsupported('overprint')
        name = m.ll_fz_colorspace_name(cs)
        self.color_spaces[name] += 1
        rgb = m.fz_device_rgb()
        # Preserve the engine's rendering intent and black-point compensation.
        return list(m.ll_fz_convert_color(cs, values, rgb.m_internal, None, params)[:3])

    def fill_path(self, ctx, path, even_odd, ctm, cs, color, alpha, params):
        rgb = self.color(cs, color, alpha, params)
        self.events.append(dict(kind='fill', path=self.path_walker.read(path), matrix=matrix(ctm), even_odd=bool(even_odd), color=rgb))

    def stroke_path(self, ctx, path, stroke, ctm, cs, color, alpha, params):
        rgb = self.color(cs, color, alpha, params)
        self.events.append(dict(kind='stroke', path=self.path_walker.read(path), matrix=matrix(ctm), style=stroke_data(stroke), color=rgb))

    def clip_path(self, ctx, path, even_odd, ctm, scissor):
        self.events.append(dict(kind='clip_fill', path=self.path_walker.read(path), matrix=matrix(ctm), even_odd=bool(even_odd)))

    def clip_stroke_path(self, ctx, path, stroke, ctm, scissor):
        self.events.append(dict(kind='clip_stroke', path=self.path_walker.read(path), matrix=matrix(ctm), style=stroke_data(stroke)))

    def pop_clip(self, ctx):
        self.events.append(dict(kind='pop_clip'))

    def text(self, text, ctm, rgb, style=None):
        raw_span = text.head
        while raw_span:
            span = m.FzTextSpan(raw_span)
            font = span.font()
            if not m.fz_font_ft_face(font):
                self.unsupported('non-FreeType glyph outlines', m.fz_font_name(font))
                raw_span = raw_span.next
                continue
            for i in range(raw_span.len):
                item = span.items(i)
                trm = span.trm()
                trm.e, trm.f = item.x, item.y
                info = dict(font=m.fz_font_name(font), gid=item.gid, unicode=item.ucs,
                            text_matrix=matrix(trm), page_matrix=matrix(ctm), event=None)
                if item.gid >= 0:
                    outline = m.fz_outline_glyph(font, item.gid, trm)
                    if outline.m_internal:
                        commands = self.path_walker.read(outline.m_internal)
                        if commands:
                            info['event'] = len(self.events)
                            event = dict(kind='stroke' if style else 'fill', path=commands,
                                         matrix=matrix(ctm), color=rgb)
                            if style:
                                event['style'] = style
                            else:
                                event['even_odd'] = False
                                event['role'] = 'text'
                            self.events.append(event)
                self.glyphs.append(info)
            raw_span = raw_span.next

    def fill_text(self, ctx, text, ctm, cs, color, alpha, params):
        self.text(text, ctm, self.color(cs, color, alpha, params))

    def stroke_text(self, ctx, text, stroke, ctm, cs, color, alpha, params):
        self.text(text, ctm, self.color(cs, color, alpha, params), stroke_data(stroke))

    def ignore_text(self, *args):
        pass

    def begin_group(self, ctx, bbox, cs, isolated, knockout, blendmode, alpha):
        if knockout or blendmode != m.FZ_BLEND_NORMAL or alpha != 1:
            self.unsupported('compositing group', dict(knockout=knockout, blend=blendmode, alpha=alpha))

    def end_group(self, *args):
        pass

    def begin_tile(self, *args):
        self.unsupported('tiling pattern')
        return 0

    def end_tile(self, *args):
        pass

    def begin_layer(self, ctx, name):
        self.layers.append((str(name), len(self.events)))

    def end_layer(self, *args):
        if not self.layers:
            raise ValueError('PDF layer stack underflow')
        name, start = self.layers.pop()
        for i in range(start, len(self.events)):
            if self.events[i]['kind'] in ('fill', 'stroke'):
                self.sources.setdefault(i, name)

    def set_default_colorspaces(self, ctx, defaults):
        for name, getter in [('DeviceGray', m.ll_fz_default_gray), ('DeviceRGB', m.ll_fz_default_rgb), ('DeviceCMYK', m.ll_fz_default_cmyk)]:
            if m.ll_fz_colorspace_name(getter(defaults)) != name:
                self.unsupported('default color space override', name)


UNSUPPORTED = ['clip_text', 'clip_stroke_text', 'fill_image', 'fill_image_mask',
               'clip_image_mask', 'fill_shade', 'begin_mask', 'end_mask']
for _name in UNSUPPORTED:
    def reject(self, *args, name=_name):
        self.unsupported(name)
    setattr(ExtractDevice, _name, reject)

CALLBACKS = ['fill_path', 'stroke_path', 'clip_path', 'clip_stroke_path', 'pop_clip',
             'fill_text', 'stroke_text', 'ignore_text', 'begin_group', 'end_group',
             'begin_tile', 'end_tile', 'begin_layer', 'end_layer', 'set_default_colorspaces'] + UNSUPPORTED


def guarded(callback):
    @functools.wraps(callback)
    def call(self, *args):
        try:
            return callback(self, *args)
        except Exception as error:
            # MuPDF can recover from device exceptions. Remember errors so the
            # caller cannot mistake a partial extraction for a complete page.
            self.errors.append(f'{callback.__name__}: {error}')
            return 0
    return call


for _name in CALLBACKS:
    setattr(ExtractDevice, _name, guarded(getattr(ExtractDevice, _name)))


# Transfer functions have no device callback. Conservatively flag any
# resource dictionary containing one, including unused resources.
# A module-level recursion avoids a self-capturing closure retaining the device
# and its entire event tree until a later cyclic collection.
def scan_transfer_functions(obj, device, depth=0):
    if depth > 64:
        raise ValueError('PDF object nesting exceeds probe limit')
    if obj.pdf_is_indirect():
        return  # Each indirect object is visited separately below.
    if obj.pdf_is_dict():
        for i in range(obj.pdf_dict_len()):
            key = obj.pdf_dict_get_key(i).pdf_to_name()
            value = obj.pdf_dict_get_val(i)
            if key in ('TR', 'TR2') and not value.pdf_is_null() and value.pdf_to_name() not in ('Identity', 'Default'):
                device.unsupported('transfer function', key)
            scan_transfer_functions(value, device, depth+1)
    elif obj.pdf_is_array():
        for i in range(obj.pdf_array_len()):
            scan_transfer_functions(obj.pdf_array_get(i), device, depth+1)


def extract(filename, page_number=1, *, device_factory=ExtractDevice):
    with pymupdf.open(filename) as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError('Expected unencrypted PDF')
        if not 1 <= page_number <= len(doc):
            raise ValueError('Page number out of range')
        patterns = make_pattern_clips_explicit(doc)
        page = doc[page_number-1]
        device = device_factory()
        pdf = m.pdf_document_from_fz_document(doc.this)
        for xref in range(1, doc.xref_length()):
            scan_transfer_functions(m.pdf_load_object(pdf, xref), device)
        m.fz_run_page(page.this, device, m.FzMatrix(), m.FzCookie())
        m.fz_close_device(device)
        if device.layers:
            raise ValueError('Unbalanced PDF layer stack')
        if device.errors:
            raise RuntimeError('; '.join(device.errors))
        result = dict(version=1, engine=f'MuPDF {pymupdf.mupdf_version} / PyMuPDF {pymupdf.pymupdf_version}',
                      units='pt', y_axis='down', bounds=list(page.rect), events=device.events)
        if device.sources:
            result['sources'] = device.sources
        probe = dict(glyphs=device.glyphs, color_spaces=device.color_spaces,
                     patterns=patterns, pattern_expansion='MuPDF FZ_NO_TILING with explicit cell BBox clips')
        return result, probe


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--page', type=int, default=1)
    args = parser.parse_args()
    result, probe = extract(args.input, args.page)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    Path(args.output+'.probe.json').write_text(json.dumps(probe, indent=2, allow_nan=False)+'\n')
    counts = Counter(e['kind'] if e['kind'] != 'unsupported' else 'unsupported:'+e['feature'] for e in result['events'])
    print(json.dumps(dict(engine=result['engine'], counts=counts, glyphs=len(probe['glyphs']))))
