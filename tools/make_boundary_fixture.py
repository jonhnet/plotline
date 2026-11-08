#!/usr/bin/env python3
"""Build ordinary PDFs and optional print intent for both generalization tracks."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path

from PIL import Image
import pymupdf

from print_contract import DIRECTORY, ROOT, validate_intent

MM = 72/25.4


def image_bytes():
    image = Image.new('RGB',(32,24))
    image.putdata([(int(x*255/31),int(y*255/23),0 if (x//4+y//4)%2 else 255)
                   for y in range(24) for x in range(32)])
    output = io.BytesIO(); image.save(output,format='PNG')
    return output.getvalue()


def make_fixture(directory):
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
    doc = pymupdf.open()
    groups = {name:doc.add_ocg('fixture/'+name) for name in
              ['fine-black','broad-black','red','green','knockout','labels']}
    font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    def point(x,y): return pymupdf.Point(x*MM,y*MM)
    def rect(x,y,w,h): return pymupdf.Rect(x*MM,y*MM,(x+w)*MM,(y+h)*MM)
    def text(page, label,x,y,size=10):
        page.insert_text(point(x,y),label,fontsize=size,fontname='FixtureSans',
                         oc=groups['labels'])
    def line(page,points,color,width,group):
        page.draw_polyline([point(*p) for p in points],color=color,width=width*MM,
                           lineCap=1,lineJoin=1,oc=groups[group])
    for index,size in enumerate([(431.8,279.4),(558.8,431.8)]):
        page = doc.new_page(width=size[0]*MM,height=size[1]*MM)
        page.insert_font(fontname='FixtureSans',fontfile=font)
        text(page,f'PDF BOUNDARY / PAGE {index+1}',60,24,17)
        text(page,'100 mm ruler; its left endpoint is at page (60, 35) mm.',60,31,9)
        line(page,[(60,35),(160,35)],(0,0,0),.15,'fine-black')
        for x in [60,160]:
            line(page,[(x,33),(x,37)],(0,0,0),.15,'fine-black')
        text(page,'Same black color, two intended pens:',60,47)
        line(page,[(60,52),(100,52)],(0,0,0),.15,'fine-black')
        line(page,[(120,52),(160,52)],(0,0,0),.6,'broad-black')
        text(page,'White road core and cleared label background',60,67)
        line(page,[(60,80),(200,80)],(1,0,0),2.4,'red')
        line(page,[(60,80),(200,80)],(1,1,1),1.4,'knockout')
        page.draw_rect(rect(103,74,28,12),color=None,fill=(1,1,1),oc=groups['knockout'])
        text(page,'CLEAR',105,82,12)
        text(page,'Hollow outline / explicit hatch / generic fill',60,97)
        page.draw_rect(rect(60,105,25,30),width=.15*MM,color=(0,0,0),oc=groups['fine-black'])
        # A real PDF clipping path around precomputed diagonal hatch strokes.
        old = set(page.get_contents())
        shape = page.new_shape()
        for x in range(80,150,3):
            shape.draw_line(point(x,150),point(x+45,100))
        shape.finish(color=(0,.5,0),width=.4*MM,oc=groups['green']); shape.commit()
        clip = rect(100,105,35,40)
        prefix = f'q {clip.x0} {page.rect.height-clip.y1} {clip.width} {clip.height} re W n\n'.encode()
        for xref in set(page.get_contents())-old:
            doc.update_stream(xref,prefix+doc.xref_stream(xref)+b'\nQ')
        page.draw_rect(rect(150,105,45,40),color=None,fill=(0,.5,0),oc=groups['green'])
        page.draw_circle(point(215,60),8*MM,width=.3*MM,color=(1,0,0),oc=groups['red'])
        text(page,'Most of the page is intentionally blank. Preserve this placement.',60,163,11)
        if index == 1:
            # Out of the smaller target sheet: a driver must report/preview it,
            # not rescale or center all of the artwork to make it fit.
            line(page,[(496,300),(504,300)],(0,0,0),.15,'fine-black')
            line(page,[(500,296),(500,304)],(0,0,0),.15,'fine-black')
            text(page,'C-page marker outside B sheet',450,315,11)
    doc.set_metadata(dict(title='Plotline shared PDF boundary fixture',author='Plotline'))
    pdf = directory/'boundary.pdf'
    doc.save(pdf,deflate=True,no_new_id=True)
    palette = json.loads((DIRECTORY/'examples/palette.json').read_text())
    profile = json.loads((DIRECTORY/'examples/hp7585-tabloid-landscape.json').read_text())
    policies = dict(fill=dict(mode='crosshatch',spacing_mm=2,angle_deg=45),
                    stroke_width_tolerance=.75,raster='placeholder',gradients='omit',
                    transparency='ignore-alpha',blend_modes='normal',soft_masks='reject')
    intent = dict(schema='plotline.print-intent/1',title='Shared boundary fixture',
                  source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                  pages=[dict(page_index=i,source_size_mm=size,target_media=profile,
                              placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]))
                         for i,size in enumerate([[431.8,279.4],[558.8,431.8]])],
                  palette=palette,bindings=[dict(ocg_name='fixture/'+name,ink_id=name)
                                            for name in ['fine-black','broad-black','red','green']]
                         +[dict(ocg_name='fixture/labels',ink_id='fine-black')], policies=policies)
    (directory/'boundary.print-intent.json').write_text(json.dumps(intent,indent=2)+'\n')
    validate_intent(intent,pdf)
    doc[0].get_pixmap(dpi=96).save(directory/'boundary-preview.png')
    # Policy probe is separate: the strict vector fixture remains useful to
    # today's converter while images/alpha/shading intentionally need policy work.
    probe = pymupdf.open(); probe.insert_pdf(doc,from_page=0,to_page=0)
    page = probe[0]
    page.insert_image(rect(240,110,48,36),stream=image_bytes())
    page.draw_rect(rect(240,50,35,35),color=None,fill=(1,0,0),fill_opacity=.35)
    page.draw_rect(rect(258,65,35,35),color=None,fill=(0,0,1),fill_opacity=.35)
    function = probe.get_new_xref()
    probe.update_object(function,'<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0] /C1 [1 0 0] /N 1 >>')
    shading = probe.get_new_xref()
    probe.update_object(shading,f'<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 1 0] /Function {function} 0 R /Extend [true true] >>')
    resources = int(probe.xref_get_key(page.xref,'Resources')[1].split()[0])
    probe.xref_set_key(resources,'Shading',f'<< /FixtureGradient {shading} 0 R >>')
    stream = probe.get_new_xref(); probe.update_object(stream,'<< >>')
    area = rect(240,170,60,20)
    probe.update_stream(stream,f'q {area.width} 0 0 {area.height} {area.x0} {page.rect.height-area.y1} cm 0 0 1 1 re W n /FixtureGradient sh Q'.encode())
    contents = page.get_contents()+[stream]
    probe.xref_set_key(page.xref,'Contents','['+' '.join(f'{x} 0 R' for x in contents)+']')
    probe.save(directory/'policy-probes.pdf',deflate=True,no_new_id=True)
    probe[0].get_pixmap(dpi=96).save(directory/'policy-probes-preview.png')
    probe.close(); doc.close()
    expectations = dict(schema='plotline.fixture-expectations/1',
                        ruler=dict(page_index=0,start_mm=[60,35],end_mm=[160,35],length_mm=100,
                                   start_device_units=[-6716,4112],end_device_units=[-2716,4112]),
                        same_color_distinct_inks=['fine-black','broad-black'],
                        page_1_overflow_marker_mm=[500,300],
                        page_1_overflow_marker_device_units=[10884,-6488],
                        source_pages_mm=[[431.8,279.4],[558.8,431.8]],
                        policy_probe_features=['embedded RGB image','alpha paints','axial gradient'],
                        note='Geometry expectations are source/page-placement invariants. Per-ink conversion and policy probes are acceptance targets, not implemented claims.')
    (directory/'expectations.json').write_text(json.dumps(expectations,indent=2)+'\n')
    embedded = base64.b64encode(image_bytes()).decode()
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="215.9mm" height="279.4mm" viewBox="0 0 215.9 279.4">
<title>Plotline Inkscape capture test</title>
<text x="25" y="50" font-family="sans-serif" font-size="6">INKSCAPE → PDF CAPTURE</text>
<path d="M 25,65 L 125,65 M 25,62 V 68 M 125,62 V 68" fill="none" stroke="black" stroke-width="0.3"/>
<text x="25" y="76" font-family="sans-serif" font-size="4">100 mm ruler; start = (25, 65) mm</text>
<path d="M 25,100 C 70,75 90,125 125,100" fill="none" stroke="red" stroke-width="0.4"/>
<rect x="25" y="120" width="45" height="30" fill="none" stroke="green" stroke-width="0.4"/>
<text x="25" y="166" font-family="sans-serif" font-size="4">This text and these paths should stay vector.</text>
<image x="135" y="90" width="48" height="36" xlink:href="data:image/png;base64,{embedded}"/>
<text x="125" y="140" font-family="sans-serif" font-size="3.5">Only this patch is a bitmap.</text>
</svg>'''
    (directory/'inkscape-capture.svg').write_text(svg)
    return intent


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/pdf-boundary-v1')
    args = parser.parse_args(); make_fixture(args.output)
    print(args.output)
