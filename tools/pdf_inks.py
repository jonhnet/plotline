"""Immutable source-style census and stable mapping identities.

No path geometry is retained during the census. Source values are not quantized;
grouping is a UI operation over these IDs, never a mutation of the PDF.
"""
import hashlib
import json
import math
import pdf_native as native
from pymupdf import mupdf as m

PT = 72 / 25.4


def source_style(event):
    width = None
    if event['kind'] == 'stroke':
        a,b,c,d,*_ = event['matrix']
        width = event['style']['width'] * math.sqrt(abs(a*d-b*c)) / PT
    rgb = event['color']
    key = json.dumps([event['kind'],rgb,width],separators=(',',':'),allow_nan=False)
    return dict(id='style-'+hashlib.sha256(key.encode()).hexdigest()[:24],kind=event['kind'],
                color_srgb='#'+''.join(f'{max(0,min(255,round(v*255))):02x}' for v in rgb),
                source_rgb=rgb,width_mm=width)


class StyleDevice(native.ExtractDevice):
    def __init__(self):
        super().__init__()
        self.styles = {}

    def unsupported(self,*args):
        pass  # Census is not a claim that every PDF effect is convertible.

    def record(self,kind,ctm,cs,color,params,stroke=None):
        rgb = self.color(cs,color,1,params)
        if rgb == [1,1,1]:
            return
        event=dict(kind=kind,color=rgb,matrix=native.matrix(ctm))
        if stroke is not None:event['style']=dict(width=stroke.linewidth)
        style=source_style(event)
        row=self.styles.setdefault(style['id'],dict(style,count=0))
        row['count']+=1

    def fill_path(self,ctx,path,even_odd,ctm,cs,color,alpha,params):
        self.record('fill',ctm,cs,color,params)

    def stroke_path(self,ctx,path,stroke,ctm,cs,color,alpha,params):
        self.record('stroke',ctm,cs,color,params,stroke)

    def fill_text(self,ctx,text,ctm,cs,color,alpha,params):
        self.record('fill',ctm,cs,color,params)

    def stroke_text(self,ctx,text,stroke,ctm,cs,color,alpha,params):
        self.record('stroke',ctm,cs,color,params,stroke)


for _name in ['clip_path','clip_stroke_path','pop_clip','begin_layer','end_layer']:
    setattr(StyleDevice,_name,lambda self,*args:None)
for _name in native.CALLBACKS:
    if _name in StyleDevice.__dict__:
        setattr(StyleDevice,_name,native.guarded(getattr(StyleDevice,_name)))


def census(page):
    device=StyleDevice()
    m.fz_run_page(page.this,device,m.FzMatrix(),m.FzCookie())
    m.fz_close_device(device)
    if device.errors:raise RuntimeError('; '.join(device.errors))
    return list(device.styles.values())


def compact_palette(events):
    """Retain logical identities in intent, emit at most eight distinct used pens."""
    inks=events['inks'];used=set(events['paint_inks'].values())
    if len(inks)<=8:return events  # Keep existing conversion IDs and bytes stable.
    effective=[];keys={};mapping={}
    for n,ink in enumerate(inks,1):
        if n not in used:continue
        key=(tuple(ink['color']),ink['width'])
        if key not in keys:
            keys[key]=len(effective)+1;effective.append(ink)
        mapping[n]=keys[key]
    if len(effective)>8:
        raise ValueError(f'This page uses {len(effective)} distinct output pens. Map inks together in the paired pen display to use at most eight pens in one plot.')
    # Empty pages still need an explicit palette for the converter's semantics.
    events['inks']=effective or inks[:1]
    events['paint_inks']={index:mapping[n] for index,n in events['paint_inks'].items()}
    return events
