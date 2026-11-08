#!/usr/bin/env python3
"""Small offline lettering/occlusion proof for a deliberately broad 0.5 mm pen."""
import copy
import hashlib
import json
from pathlib import Path
import pymupdf
from pdf_driver import DEFAULT_POLICIES,PT


def make_fixture(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    pdf=directory/'thin-labels.pdf'
    doc=pymupdf.open();page=doc.new_page(width=360,height=220)
    font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    page.insert_font(fontname='MapSans',fontfile=font)
    for index,(size,label) in enumerate([(8,'Cascades Ridge'),(9,'Tahoma Forest'),(11,'Mountain Loop 1985')]):
        page.insert_text((30,35+index*30),label,fontsize=size,fontname='MapSans',oc=doc.add_ocg('labels/'+str(index)))
    page.draw_line((5,130),(355,130),color=(0,0,0),width=.15*PT,oc=doc.add_ocg('roads/thin'))
    page.draw_rect((180,115,200,145),fill=(1,1,1),color=None)
    # A partly missing glyph-like H tests fragments, not just absent paints.
    shape=page.new_shape()
    shape.draw_polyline([(30,160),(32,160),(32,168),(45,168),(45,160),(47,160),(47,180),(45,180),(45,168.3),(32,168.3),(32,180),(30,180),(30,160)])
    shape.finish(color=None,fill=(0,0,0),oc=doc.add_ocg('symbols/H'));shape.commit()
    doc.save(pdf);doc.close()
    intent=dict(schema='plotline.print-intent/1',title='Thin map labels',source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                palette=dict(schema='plotline.palette/1',id='document',label='Document inks',inks=[dict(id='black',label='Black',color_srgb='#000000',width_mm=.5)]),
                policies=copy.deepcopy(DEFAULT_POLICIES),pages=[dict(page_index=0,source_size_mm=[360/PT,220/PT],placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]),
                target_media=dict(schema='plotline.media-profile/1',id='proof',revision=1,label='Proof',sheet_size_mm=[360/PT,220/PT],printable_rect_mm=[10/PT,10/PT,340/PT,200/PT],content_inset_mm=0))])
    (directory/'thin-labels.print-intent.json').write_text(json.dumps(intent,indent=2)+'\n')
    return intent


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path)
    make_fixture(parser.parse_args().directory)
