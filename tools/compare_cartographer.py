#!/usr/bin/env python3
"""Compare map semantics and full-page PDF rasters after a composer optimization.

Arguments are completed composer output directories. Rasterize in strips to
bound memory on large sheets; compare identical paper coordinates without
alignment or rescaling. This does not convert HPGL or contact a plotter.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pymupdf


def compare(before,after,dpi=300):
    before,after=Path(before),Path(after)
    def read(root,name):return json.loads((root/name).read_text())
    assert read(before,'recipe.json')==read(after,'recipe.json'),'Recipe changed'
    a,b=[read(root,'provenance.json') for root in [before,after]]
    for key in ['quality','inputs','layout','fetched_bounds','feature_counts','labels','omitted_labels','warnings','depth_audit']:
        assert a[key]==b[key],f'Map semantics changed: {key}'
    a,b=[read(root,'map.print-intent.json') for root in [before,after]]
    for intent in [a,b]:intent.pop('source_sha256')
    assert a==b,'Print intent changed beyond the PDF hash'
    report=dict(dpi=dpi,semantics_equal=True,pages=[],byte_equal={})
    for name in ['map.pdf','preview.png','scene.json']:
        report['byte_equal'][name]=hashlib.sha256((before/name).read_bytes()).digest()==hashlib.sha256((after/name).read_bytes()).digest()
    with pymupdf.open(before/'map.pdf') as old,pymupdf.open(after/'map.pdf') as new:
        assert len(old)==len(new),'Page count changed'
        for page_no,(a,b) in enumerate(zip(old,new)):
            assert a.rect==b.rect,'Paper dimensions changed'
            assert a.get_text('words')==b.get_text('words'),'Printed labels changed'
            displays=[page.get_displaylist() for page in [a,b]]
            zoom=dpi/72;matrix=pymupdf.Matrix(zoom,zoom)
            # Use the renderer's integer rectangle rounding: floating-point
            # ceil can invent an extra column at an exact inch/DPI boundary.
            pixel_rect=(a.rect*matrix).irect
            width,height=pixel_rect.width,pixel_rect.height
            counts=dict(pixels=width*height,changed=0,substantial=0,ink=0,maximum_channel_delta=0)
            for start in range(0,height,512):
                stop=min(height,start+512);clip=pymupdf.Rect(0,start/zoom,a.rect.width,stop/zoom)
                arrays=[]
                for display in displays:
                    pix=display.get_pixmap(matrix=matrix,clip=clip,colorspace=pymupdf.csRGB,alpha=False)
                    rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
                    rgb=rgb[start-pix.y:stop-pix.y,-pix.x:width-pix.x]
                    assert rgb.shape==(stop-start,width,3),'Raster strip is misaligned'
                    arrays.append(rgb)
                left,right=arrays
                delta=np.max(np.abs(left.astype(np.int16)-right.astype(np.int16)),axis=2)
                counts['changed']+=int(np.count_nonzero(delta))
                counts['substantial']+=int(np.count_nonzero(delta>64))
                counts['ink']+=int(np.count_nonzero(np.min(np.minimum(left,right),axis=2)<224))
                counts['maximum_channel_delta']=max(counts['maximum_channel_delta'],int(delta.max()))
            counts['substantial_ink_ratio']=counts['substantial']/max(1,counts['ink'])
            report['pages'].append(dict(page=page_no,width=width,height=height,**counts))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path);parser.add_argument('after',type=Path)
    parser.add_argument('output',type=Path);parser.add_argument('--dpi',type=int,default=300)
    args=parser.parse_args()
    if args.dpi<=0:parser.error('DPI must be positive')
    result=compare(args.before,args.after,args.dpi)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
