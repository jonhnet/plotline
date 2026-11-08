#!/usr/bin/env python3
"""Make the synthetic mixed PDF and a replayable raster print intent."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from pdf_driver import DEFAULT_POLICIES, ROOT, resolve_intent


def make(output):
    subprocess.run(['/usr/bin/python3','-m','raster_lab.pdf_fixture','--output',str((output/'fixture').resolve()),
                    '--pdf-python',sys.executable],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    pdf=output/'fixture/mixed-raster-vector.pdf'
    inks=[dict(id=r.lower(),label=label,color_srgb=c,width_mm=w) for r,label,c,w in
          [('C','Turquoise','#009f7c',.25),('M','Simulated magenta','#e32991',.3),
           ('Y','Simulated yellow','#efcf00',.35),('K','Black','#000000',.25)]]
    intent=dict(schema='plotline.print-intent/1',title='Raster boundary proof',source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                palette=dict(schema='plotline.palette/1',id='raster-fixture',label='Synthetic test pens',inks=inks),
                pages=[dict(page_index=0,source_size_mm=[160,120],placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]))],
                policies={**DEFAULT_POLICIES,'raster':'dither','raster_settings':dict(screening=True,palette='cmy',fullness=1,
                    edge_falloff_mm=6.35,black_treatment='lines',inks={r:r.lower() for r in 'CMYK'})})
    intent=resolve_intent(pdf,intent)
    (output/'intent.json').write_text(json.dumps(intent,indent=2)+'\n')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/pdf-raster-integration')
    args=parser.parse_args();print(make(args.output))
