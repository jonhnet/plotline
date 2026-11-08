"""Source identities, unlimited authoring palettes and single-pass conversion."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf
from shapely.geometry import LineString,box

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from pdf_driver import DEFAULT_POLICIES,ROOT,PT,PolicyDevice,convert,inspect,prepare_events
from pdf_inks import compact_palette,source_style
from pdf_native import extract
from print_contract import validate_intent
from test_pdf_driver import decoded_mm


class PenWorkflowTests(unittest.TestCase):
    def test_compaction_retains_all_used_assignments_and_refuses_nine_materials(self):
        inks=[dict(id=f'ink-{i}',color=[0,0,0],width=1) for i in range(20)]
        event=dict(inks=inks,paint_inks={i:i+1 for i in range(20)})
        result=compact_palette(copy.deepcopy(event))
        self.assertEqual(len(result['inks']),1)
        self.assertEqual(set(result['paint_inks'].values()),{1})
        self.assertEqual(len(result['paint_inks']),20)
        for i,ink in enumerate(inks):ink['width']=i+1
        with self.assertRaisesRegex(ValueError,'20 distinct output pens'):compact_palette(event)

    def test_census_mapping_is_stable_before_placement_and_keeps_close_widths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf=root/'art.pdf';doc=pymupdf.open();page=doc.new_page(width=300,height=200)
            for i in range(20):page.draw_line((30,20+i*5),(200,20+i*5),color=(0,.5,0),width=.8+i*.001)
            doc.save(pdf);doc.close()
            rows=inspect(pdf,root)['pages'][0]['source_styles']
            self.assertEqual(len(rows),20);self.assertEqual(len({s['width_mm'] for s in rows}),20)
            inks=[dict(id='green',label='Green',color_srgb='#008000',width_mm=.5)]
            device=PolicyDevice(DEFAULT_POLICIES,{},1);events,_=extract(pdf,1,device_factory=lambda:device)
            self.assertEqual({source_style(e)['id'] for e in events['events'] if e['kind']=='stroke'},{r['id'] for r in rows})
            selection=dict(source_size_mm=[300/PT,200/PT],placement=dict(scale=2,rotation_deg=90,offset_mm=[3,4]),
                           target_media=dict(sheet_size_mm=[200,200],printable_rect_mm=[0,0,200,200]))
            prepared=prepare_events(events,device,selection,inks,{r['id']:'green' for r in rows})
            self.assertEqual(len(prepared['paint_inks']),20);self.assertEqual(set(prepared['paint_inks'].values()),{1})

    def test_pen_width_single_pass_preserves_white_knockout_and_finite_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf=root/'strokes.pdf';doc=pymupdf.open();page=doc.new_page(width=300,height=200)
            page.draw_line((0,60),(300,60),color=(0,0,0),width=15)
            page.draw_rect((140,40,160,80),color=None,fill=(1,1,1))
            doc.save(pdf);doc.close()
            ink=dict(id='black',label='Black',color_srgb='#000000',width_mm=.3)
            intent=dict(schema='plotline.print-intent/1',source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),title='Single pass',
                palette=dict(schema='plotline.palette/1',id='document',label='Document',inks=[ink]),
                pages=[dict(page_index=0,source_size_mm=[300/PT,200/PT],placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]),
                            target_media=dict(schema='plotline.media-profile/1',id='paper',revision=1,label='Paper',sheet_size_mm=[300/PT,200/PT],printable_rect_mm=[10,10,80,50],content_inset_mm=0))],
                policies=dict(copy.deepcopy(DEFAULT_POLICIES),stroke_width_mode='pen'))
            # A large document palette with unused inks must still be valid and printable.
            intent['palette']['inks'] += [dict(ink,id=f'unused-{i}',width_mm=.4+i*.01) for i in range(15)]
            intent['ink_mapping']={r['id']:'black' for r in inspect(pdf,root)['pages'][0]['source_styles']}
            validate_intent(intent,pdf)
            output=root/'pen';output.mkdir();convert(pdf,intent,output,ROOT/'target/release/plotline')
            paths=decoded_mm(output/'page-0/plot.hpgl',200/PT)
            self.assertEqual(len(paths),2)
            self.assertLess(sum(LineString(line).length for _,line in paths),80)
            clear=box(140/PT,40/PT,160/PT,80/PT);printable=box(10,10,90,60).buffer(1e-5)
            for _,line in paths:
                footprint=LineString(line).buffer(.15)
                self.assertFalse(footprint.intersects(clear));self.assertTrue(printable.covers(footprint))
            intent['policies'].pop('stroke_width_mode');wide=root/'wide';wide.mkdir()
            convert(pdf,intent,wide,ROOT/'target/release/plotline')
            self.assertGreater(sum(LineString(line).length for _,line in decoded_mm(wide/'page-0/plot.hpgl',200/PT)),300)


if __name__=='__main__':unittest.main()
