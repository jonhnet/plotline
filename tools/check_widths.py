#!/usr/bin/env python3
"""Width-tolerance PDF proof, including a stroke wider than its logical source."""
import json
from pathlib import Path
import pymupdf
from check import ROOT, run, check_artifacts
from summarize_plot import summarize


def main():
    directory=ROOT/'build/fixtures'
    directory.mkdir(parents=True,exist_ok=True)
    pdf=directory/'width-tolerance.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=360,height=300)
        page.insert_text((20,23),'0.4 pt pen / 75% width tolerance',fontsize=13)
        for i,width in enumerate([.3,.4,.65,.7,.9,1.1,2.0]):
            y=55+i*28
            page.insert_text((20,y+3),f'{width:g} pt',fontsize=11)
            page.draw_line((95,y),(330,y),width=width,color=(0,0,0),lineCap=1)
        page.draw_line((95,265),(330,265),width=.3,color=(1,0,0),lineCap=1)
        page.draw_rect((195,255,220,275),fill=(1,1,1),color=None)
        page.insert_text((20,268),'Occlusion',fontsize=11)
        doc.save(pdf)
    print(run('cargo','build','--release','--locked'))
    events=directory/'width-tolerance.json'
    print(run('.venv/bin/python','tools/pdf_native.py',str(pdf),str(events)))
    print(run('target/release/plotline',str(events),'build/width-tolerance','0.4','40','crosshatch','red-green-black','6','0.75'))
    result=check_artifacts('width-tolerance',str(pdf),dpi='216')
    report=json.loads((ROOT/'build/width-tolerance/report.json').read_text())
    widths=[p for p in report['decoded_hpgl'] if p['width_layout']]
    if len(widths)!=8 or not any(p['outside_visible_area_pt2']>0 for p in widths):
        raise AssertionError('Expected eight approximated strokes, including deliberate thickening')
    if any(p['outside_allowed_area_pt2']>1e-7 or p['uncovered_required_area_pt2']>1e-7 for p in widths):
        raise AssertionError('Width/occlusion contract failed')
    summary=summarize(ROOT/'build/width-tolerance')
    (ROOT/'build/width-tolerance/validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(run('mutool','draw','-q','-r','120','-o','build/width-tolerance/comparison.png','build/width-tolerance/comparison.svg'))
    for p in widths:
        w=p['width_layout']
        print(f"width={w['requested_width_pt']:.3f}, passes={w['passes']}, planned span={w['planned_span_pt']:.3f}, excess area={p['outside_visible_area_pt2']:.3f} pt2")
    print(f"PASS width PDF: interpretation discrepancy {result['interpretation_large_pixel_discrepancy_ratio']:.3%}; containment and required interior verified")
    return summary


if __name__=='__main__':main()
