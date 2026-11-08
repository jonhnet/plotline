"""Synthetic mixed vector/image PDF for the forthcoming PDF Studio boundary.

This creates input and verifies its source semantics. It does not pretend the
existing PDF worker already implements the new raster-to-pen image event.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess

import cairo
import numpy as np
from PIL import Image


def make(output, pdf_python):
    output.mkdir(parents=True,exist_ok=True)
    pixels=np.full((128,128,3),255,np.uint8)
    pixels[20:50,20:50]=[0,80,35]  # Dark saturated color is not black ink.
    pixels[70:105,20:50]=[180,20,100]
    pixels[20:50,80:110]=[245,220,20]
    pixels[10:115,60:68]=0
    Image.fromarray(pixels).save(output/'source.png')
    data=np.zeros((128,128,4),np.uint8)
    data[:,:,:3]=pixels[:,:,::-1]
    source=cairo.ImageSurface.create_for_data(data,cairo.FORMAT_RGB24,128,128)
    pt=72/25.4
    surface=cairo.PDFSurface(str(output/'mixed-raster-vector.pdf'),160*pt,120*pt)
    surface.set_metadata(cairo.PDF_METADATA_TITLE,'Raster/PDF boundary fixture')
    surface.set_metadata(cairo.PDF_METADATA_CREATE_DATE,'2026-11-04T00:00:00Z')
    c=cairo.Context(surface);c.scale(pt,pt)
    c.set_source_rgb(1,1,1);c.paint()
    c.set_source_rgb(0,0,1);c.set_line_width(1)
    c.move_to(5,35);c.line_to(80,35);c.stroke()
    c.set_source_rgb(1,0,0);c.move_to(115,5);c.line_to(115,95);c.stroke()
    def image(x,y,size,rotate=False,circle=False):
        c.save()
        if circle:
            c.arc(x+size/2,y+size/2,size/2,0,2*math.pi);c.clip()
        c.translate(x+size/2,y+size/2)
        if rotate:c.rotate(math.pi/2)
        c.translate(-size/2,-size/2);c.scale(size/128,size/128)
        c.set_source_surface(source);c.get_source().set_filter(cairo.FILTER_NEAREST)
        c.paint();c.restore()
    image(20,15,50,circle=True)
    image(95,25,40,rotate=True)
    c.set_source_rgb(1,1,1);c.rectangle(40,30,10,10);c.fill()
    c.set_source_rgb(0,0,0);c.set_line_width(.25)
    c.move_to(20,80);c.line_to(140,80);c.stroke()
    c.select_font_face('sans');c.set_font_size(4)
    c.move_to(20,90);c.show_text('Later vector text remains vector text')
    c.show_page();surface.finish()
    probes=[dict(name='earlier blue stroke outside image',mm=[10,35],rgb=[0,0,255]),
            dict(name='circular clip reveals earlier blue',mm=[20,35],rgb=[0,0,255]),
            dict(name='inside circular clip image hides blue',mm=[22,35],rgb=[255,255,255]),
            dict(name='opaque image white hides earlier blue',mm=[30,35],rgb=[255,255,255]),
            dict(name='black image mark before white overpaint',mm=[45,28],rgb=[0,0,0]),
            dict(name='later white hides black image mark',mm=[45,35],rgb=[255,255,255]),
            dict(name='dark green image area keeps source hue',mm=[33,28],rgb=[0,80,35])]
    script='''import json,sys,pymupdf
from pathlib import Path
p=Path(sys.argv[1]);probes=json.loads(sys.argv[2]);doc=pymupdf.open(p/'mixed-raster-vector.pdf');page=doc[0]
pix=page.get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False)
pix.save(p/'source-page.png')
for probe in probes:
 x,y=[round(v*72/25.4*3) for v in probe['mm']]
 actual=pix.pixel(x,y)[:3]
 assert max(abs(a-b) for a,b in zip(actual,probe['rgb']))<=2,(probe,actual)
images=page.get_image_info(hashes=True)
assert len(images)==2 and images[0]['digest']==images[1]['digest']
assert images[0]['transform']!=images[1]['transform']
assert page.get_drawings() and page.get_text().strip()
print(json.dumps(dict(image_occurrences=len(images),same_source_reused=True,distinct_transforms=True,source_paint_probes=probes,
 paper_mm=[160,120],status='source fixture verified; raster-to-pen PDF integration pending'),indent=2))
'''
    report=subprocess.run([str(pdf_python),'-c',script,str(output.resolve()),json.dumps(probes)],check=True,capture_output=True,text=True)
    (output/'fixture.json').write_text(report.stdout)
    return json.loads(report.stdout)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pdf-python',type=Path,default=Path('/root/plotline/.venv/bin/python'))
    args=parser.parse_args();print(json.dumps(make(args.output,args.pdf_python),indent=2))
