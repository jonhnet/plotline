"""Acceptance at the PDF boundary; independent readback in physical millimeters."""
import copy
import gc
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from collections import defaultdict
from unittest.mock import patch

import pymupdf
from shapely.geometry import LineString, box

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from make_boundary_fixture import make_fixture, image_bytes
from pdf_driver import (ROOT, PT, DEFAULT_POLICIES, PolicyDevice, convert, inspect,
                        prepare_events, resolve_intent, write_json, extract_events, ink_selector)
from pdf_native import extract


def decoded_mm(filename,height):
    """Separate, deliberately small decoder for the converter's PA/SP/PU/PD subset."""
    pen=None;position=None;paths=[]
    for command in filename.read_text().split(';'):
        command=command.strip();op=command[:2];arg=command[2:]
        if op=='SP':pen=int(arg)
        elif op in ('PU','PD') and arg:
            values=[int(n) for n in arg.split(',')]
            points=[(values[i]/40,height-values[i+1]/40) for i in range(0,len(values),2)]
            if op=='PD':paths.append((pen,[position]+points))
            position=points[-1]
    return paths


class PdfDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.intent=make_fixture(cls.root/'fixture');cls.pdf=cls.root/'fixture/boundary.pdf'
        cls.intent=resolve_intent(cls.pdf,cls.intent,dict(revision=0,slots={}))
        cls.output=cls.root/'converted';cls.output.mkdir()
        cls.result=convert(cls.pdf,cls.intent,cls.output,ROOT/'target/release/plotline')

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_palette_cache_keeps_color_ties_transformed_widths_and_palette_order(self):
        def reference(event,inks):
            errors=[sum((v-int(ink['color_srgb'][i*2+1:i*2+3],16)/255)**2 for i,v in enumerate(event['color'])) for ink in inks]
            nearest=[i for i,error in enumerate(errors) if abs(error-min(errors))<1e-12]
            if event['kind']=='stroke':
                a,b,c,d,*_=event['matrix']
                width=event['style']['width']*math.sqrt(abs(a*d-b*c))/PT
                return min(nearest,key=lambda i:abs(inks[i]['width_mm']-width))+1
            return min(nearest,key=lambda i:inks[i]['width_mm'])+1
        inks=[dict(color_srgb=color,width_mm=width) for color,width in [('#000000',.3),('#000000',.7),('#ff0000',.7),('#00ff00',.3),('#0000ff',.3)]]
        colors=[[0,0,0],[.5,0,0],[.5+1e-14,0,0],[.5+1e-8,0,0],[.5,.5,0],[1,1,1]]
        # More colors than the bounded cache, then revisit the initial colors.
        colors += [[i/700,(i*17%700)/700,(i*31%700)/700] for i in range(700)]
        colors += colors[:6]
        for palette in [inks,list(reversed(inks))]:
            choose=ink_selector(palette)
            for color in colors:
                self.assertEqual(choose(dict(kind='fill',color=color)),reference(dict(kind='fill',color=color),palette))
                for matrix in [[1,0,0,1,0,0],[2,0,0,2,0,0],[-1,0,0,1,0,0],[.8,.6,-.6,.8,0,0],[2,.3,.4,1,0,0]]:
                    for width in [.3,.5,.7]:
                        event=dict(kind='stroke',color=color,matrix=matrix,style=dict(width=width*PT))
                        self.assertEqual(choose(event),reference(event,palette))
        # Same color still selects each document ink according to page width.
        choose=ink_selector(inks)
        self.assertEqual(choose(dict(kind='stroke',color=[0,0,0],matrix=[1,0,0,1,0,0],style=dict(width=.3*PT))),1)
        self.assertEqual(choose(dict(kind='stroke',color=[0,0,0],matrix=[1,0,0,1,0,0],style=dict(width=.7*PT))),2)

    def test_extraction_restores_gc_state_after_success_and_failure(self):
        original=gc.isenabled()
        try:
            for enabled in [True,False]:
                for fail in [True,False]:
                    (gc.enable if enabled else gc.disable)()
                    def walk(*args,**kwargs):
                        self.assertFalse(gc.isenabled())
                        if fail:raise RuntimeError('extraction failed')
                        return ('events','probe')
                    with patch('pdf_driver.native.extract',side_effect=walk):
                        if fail:
                            with self.assertRaisesRegex(RuntimeError,'extraction failed'):
                                extract_events(self.pdf,1,object())
                        else:self.assertEqual(extract_events(self.pdf,1,object()),('events','probe'))
                    self.assertEqual(gc.isenabled(),enabled)
        finally:
            (gc.enable if original else gc.disable)()

    def test_page_gc_scope_covers_preparation_and_write_and_restores_before_child(self):
        original=gc.isenabled()
        class StopBeforeChild(Exception):pass
        try:
            for enabled in [True,False]:
                for failure in ['prepare','write',None]:
                    (gc.enable if enabled else gc.disable)()
                    out=self.root/f'gc-{enabled}-{failure}';out.mkdir()
                    def prepare(*args,**kwargs):
                        self.assertFalse(gc.isenabled())
                        if failure=='prepare':raise RuntimeError('deliberate prepare failure')
                        return prepare_events(*args,**kwargs)
                    def write(path,value,**kwargs):
                        if path.name=='events.json':
                            self.assertFalse(gc.isenabled())
                            if failure=='write':raise RuntimeError('deliberate write failure')
                        return write_json(path,value,**kwargs)
                    def child(*args,**kwargs):
                        self.assertEqual(gc.isenabled(),enabled)
                        raise StopBeforeChild()
                    with patch('pdf_driver.prepare_events',side_effect=prepare), patch('pdf_driver.write_json',side_effect=write), patch('pdf_driver.subprocess.run',side_effect=child):
                        with self.assertRaises(RuntimeError if failure else StopBeforeChild):
                            convert(self.pdf,self.intent,out,ROOT/'target/release/plotline')
                    self.assertEqual(gc.isenabled(),enabled)
        finally:
            (gc.enable if original else gc.disable)()

    def test_preparation_does_not_mutate_extraction_and_compact_json_preserves_values(self):
        device=PolicyDevice(self.intent['policies'],{},.3*PT)
        events,_=extract(self.pdf,device_factory=lambda:device)
        before=copy.deepcopy(events)
        prepared=prepare_events(events,device,self.intent['pages'][0],self.intent['palette']['inks'])
        self.assertEqual(events,before)
        self.assertIsNot(prepared['events'][2]['matrix'],events['events'][0]['matrix'])
        normal=self.root/'normal.json';compact=self.root/'compact.json'
        write_json(normal,prepared);write_json(compact,prepared,compact=True)
        self.assertEqual(json.loads(normal.read_text()),json.loads(compact.read_text()))
        self.assertLess(compact.stat().st_size,normal.stat().st_size)
        with self.assertRaises(ValueError):write_json(compact,{'coordinate':float('nan')},compact=True)

    def test_fast_json_keeps_null_unicode_and_rejects_all_nonfinite_numbers(self):
        output=self.root/'fast.json'
        value={'label':'null Ω','optional':None,'coordinates':[-0.0,1.2345678901234567,1e-200],7:True}
        write_json(output,value,compact=True)
        self.assertEqual(json.loads(output.read_bytes()),json.loads(json.dumps(value,allow_nan=False)))
        before=output.read_bytes()
        for number in [float('nan'),float('inf'),float('-inf')]:
            for bad in [{'nested':[None,{'matrix':[1,number]}]}, {number:'bad key'}]:
                with self.assertRaises(ValueError):write_json(output,bad,compact=True)
                self.assertEqual(output.read_bytes(),before)

    def test_preview_and_full_artifacts_match_and_summary_matches_independent_aggregation(self):
        full=self.root/'full';full.mkdir()
        result=convert(self.pdf,self.intent,full,ROOT/'target/release/plotline',artifacts='full')
        self.assertEqual(result['artifact_mode'],'full')
        self.assertEqual(self.result['artifact_mode'],'preview')
        self.assertEqual(result['pages'],self.result['pages'])
        for index in [0,1]:
            a=self.output/f'page-{index}';b=full/f'page-{index}'
            for name in ['events.json','settings.json','plot.hpgl','logical.svg','pen.svg','source.svg']:
                self.assertEqual((a/name).read_bytes(),(b/name).read_bytes(),name)
            for name in ['extraction.json','input.events.json','report.json','coverage.svg','hpgl.svg','visible.svg','planned.svg','comparison.svg']:
                self.assertFalse((a/name).exists(),name)
                self.assertTrue((b/name).is_file(),name)
            report=json.loads((b/'report.json').read_bytes())
            summary=json.loads((a/'summary.json').read_bytes())
            by_ink=defaultdict(lambda:dict(pen_down_mm=0,paths=0,unrepresented_area_pt2=0))
            by_feature=defaultdict(lambda:dict(pen_down_mm=0,paths=0));lost=0
            for paint in report['decoded_hpgl']:
                if paint['white']:continue
                lost+=paint['visible_area_pt2']>0 and paint['paths']==0
                if paint['pen_id']:
                    row=by_ink[self.intent['palette']['inks'][paint['pen_id']-1]['id']]
                    for key in row:row[key]+=paint[key]
                row=by_feature[paint['source']]
                for key in row:row[key]+=paint[key]
            paints={paint['id']:paint for paint in report['decoded_hpgl']}
            for connection in report['continuity']['connections']:
                ink=self.intent['palette']['inks'][connection['pen']-1]['id']
                by_ink[ink]['paths']-=1
                by_ink[ink]['pen_down_mm']+=connection['pen_down_mm']
                for paint_id in [connection['from_paint'],connection['to_paint']]:
                    by_feature[paints[paint_id]['source']]['pen_down_mm']+=connection['pen_down_mm']/2
            self.assertEqual(summary['by_ink'],dict(by_ink))
            self.assertEqual(summary['by_feature'],dict(by_feature))
            # Independently decode physical runs; totals must include the new
            # pen-down connections, not merely re-sum the source paint reports.
            physical=defaultdict(lambda:dict(paths=0,pen_down_mm=0))
            for pen,points in decoded_mm(b/'plot.hpgl',0):
                ink=self.intent['palette']['inks'][pen-1]['id']
                physical[ink]['paths']+=1
                physical[ink]['pen_down_mm']+=sum(math.dist(a,b) for a,b in zip(points,points[1:]))
            for ink,row in physical.items():
                self.assertEqual(summary['by_ink'][ink]['paths'],row['paths'])
                self.assertAlmostEqual(summary['by_ink'][ink]['pen_down_mm'],row['pen_down_mm'],places=6)
            self.assertEqual(summary['visible_paints_without_paths'],lost)
            self.assertEqual(summary['metrics'],report['metrics'])
            for stage in ['planned','decoded_hpgl']:
                checks=summary['verification'][stage]
                self.assertEqual(checks['paints_checked'],len(report[stage]))
                self.assertEqual(checks['white_paints'],sum(p['white'] for p in report[stage]))
                self.assertLessEqual(checks['max_outside_allowed_area_pt2'],1e-7)
                self.assertLessEqual(checks['max_uncovered_required_area_pt2'],1e-7)

    def test_progress_finishes_on_last_page_and_converter_log_is_retained(self):
        progress=json.loads((self.output/'progress.json').read_text())
        self.assertEqual(progress['phase'],'complete')
        self.assertEqual(progress['page_number'],2)
        self.assertEqual(progress['page_count'],2)
        self.assertIn('Verification:',(self.output/'page-1/converter.log').read_text())

    def test_placement_width_identity_overflow_and_white_knockout(self):
        paths=decoded_mm(self.output/'page-0/plot.hpgl',279.4)
        ruler=[line for pen,line in paths if pen==1 and len(line)==2 and abs(line[0][1]-35)<.01 and abs(line[1][1]-35)<.01]
        self.assertTrue(any(abs(min(p[0] for p in line)-60)<.2 and abs(max(p[0] for p in line)-160)<.2 for line in ruler))
        # Later tick paints occlude the ruler ends; the ticks themselves retain 100 mm registration.
        ticks=[line[0][0] for pen,line in paths if pen==1 and len(line)==2 and abs(line[0][0]-line[1][0])<1e-6
               and abs(abs(line[0][1]-line[1][1])-4)<.001]
        self.assertEqual(sorted(ticks),[60,160])
        broad=[LineString(line).length for pen,line in paths if pen==2]
        self.assertEqual(len(broad),1);self.assertAlmostEqual(broad[0],40)
        inks={i+1:ink['width_mm'] for i,ink in enumerate(self.intent['palette']['inks'])}
        printable=box(39.425,11,39.425+376.95,11+253.6).buffer(.00001)
        clear_road=box(175,79.5,185,80.5)
        for pen,line in paths:
            footprint=LineString(line).buffer(inks[pen]/2)
            self.assertTrue(printable.covers(footprint))
            if pen==3:self.assertFalse(footprint.intersects(clear_road))
        self.assertFalse(self.result['pages'][0]['geometry']['source_exceeds_sheet'])
        self.assertTrue(self.result['pages'][1]['geometry']['source_exceeds_sheet'])
        self.assertTrue(all(p['geometry']['placement']['scale']==1 for p in self.result['pages']))
        # The second-page marker at (500,300) remains outside, never shrunk in.
        second=decoded_mm(self.output/'page-1/plot.hpgl',279.4)
        self.assertLess(max(x for _,line in second for x,y in line),250)

    def test_policy_probe_is_explicit_and_conversion_keeps_source_bytes(self):
        pdf=self.root/'fixture/policy-probes.pdf';before=pdf.read_bytes()
        intent=copy.deepcopy(self.intent);intent['source_sha256']=hashlib.sha256(before).hexdigest();intent['pages']=intent['pages'][:1];intent['bindings']=[]
        out=self.root/'policy';out.mkdir();result=convert(pdf,intent,out,ROOT/'target/release/plotline')
        warnings=' '.join(result['warnings'])
        self.assertIn('raster: placeholder',warnings);self.assertIn('gradient omitted',warnings);self.assertIn('ignore-alpha',warnings)
        self.assertEqual(before,pdf.read_bytes())
        device=PolicyDevice(DEFAULT_POLICIES,{},.3*PT)
        events,_=extract(pdf,device_factory=lambda:device)
        self.assertTrue(any(e['kind']=='unsupported' for e in events['events']))

    def test_rotated_cropbox_and_userunit_are_normalized_before_placement(self):
        pdf=self.root/'rotate.pdf';doc=pymupdf.open();page=doc.new_page(width=300,height=400)
        page.draw_line((60,90),(160,90),width=1);page.set_cropbox(pymupdf.Rect(20,30,220,330));page.set_rotation(90)
        doc.xref_set_key(page.xref,'UserUnit','2');doc.save(pdf);doc.close()
        events,_=extract(pdf)
        with pymupdf.open(pdf) as doc:
            self.assertEqual(events['bounds'],list(doc[0].rect))
        stroke=next(e for e in events['events'] if e['kind']=='stroke');a,b,c,d,e,f=stroke['matrix']
        actual=[(a*p['p'][0]+c*p['p'][1]+e,b*p['p'][0]+d*p['p'][1]+f) for p in stroke['path'] if 'p' in p]
        self.assertEqual(actual,[(480,80),(480,280)])
        selection=dict(page_index=0,source_size_mm=[events['bounds'][2]/PT,events['bounds'][3]/PT],
                       placement=dict(scale=.5,rotation_deg=90,offset_mm=[7,9]),
                       target_media=self.intent['pages'][0]['target_media'])
        device=PolicyDevice(DEFAULT_POLICIES,{},.3*PT)
        prepared=prepare_events(events,device,selection,self.intent['palette']['inks'])
        self.assertEqual(prepared['events'][1]['kind'],'clip_fill')
        self.assertEqual(prepared['events'][-2:],[dict(kind='pop_clip')]*2)
        stroke=next(e for e in prepared['events'] if e['kind']=='stroke')
        a,b,c,d,e,f=stroke['matrix'];x,y=stroke['path'][0]['p']
        self.assertAlmostEqual(a*x+c*y+e,160+7*PT)
        self.assertAlmostEqual(b*x+d*y+f,240+9*PT)

    def test_raster_reserve_omit_and_transformed_placeholder(self):
        pdf=self.root/'image.pdf';doc=pymupdf.open();page=doc.new_page(width=200,height=200)
        page.draw_line((10,100),(190,100),color=(1,0,0),width=2)
        page.insert_image(pymupdf.Rect(50,50,150,150),stream=image_bytes(),keep_proportion=False,rotate=90)
        doc.save(pdf);doc.close()
        for policy in ('omit','reserve','placeholder'):
            device=PolicyDevice({**DEFAULT_POLICIES,'raster':policy},{},.3*PT)
            events,_=extract(pdf,device_factory=lambda:device)
            paints=[e for e in events['events'] if e['kind'] in ('fill','stroke')]
            self.assertEqual(len(paints),{'omit':1,'reserve':2,'placeholder':3}[policy])
            self.assertFalse(any(e['kind']=='unsupported' for e in events['events']))
            if policy!='omit':self.assertEqual(paints[1]['color'],[1,1,1])
            if policy=='placeholder':
                self.assertEqual(paints[-1]['matrix'],[1,0,0,1,0,0])
                points=[p['p'] for p in paints[-1]['path']]
                self.assertEqual({tuple(round(v,3) for v in p) for p in points},{(50,50),(150,50),(50,150),(150,150)})

    def test_masked_image_is_not_reserved_as_an_opaque_rectangle(self):
        import io
        from PIL import Image
        image=Image.new('RGBA',(12,12),(255,0,0,80));stream=io.BytesIO();image.save(stream,format='PNG')
        pdf=self.root/'masked.pdf';doc=pymupdf.open();page=doc.new_page(width=100,height=100)
        page.insert_image((10,10,90,90),stream=stream.getvalue());doc.save(pdf);doc.close()
        device=PolicyDevice({**DEFAULT_POLICIES,'raster':'placeholder'},{},.3*PT)
        events,_=extract(pdf,device_factory=lambda:device)
        self.assertTrue(any(e['kind']=='unsupported' for e in events['events']))
        self.assertFalse(any(e['kind']=='fill' and e['color']==[1,1,1] for e in events['events']))
        messages='; '.join(e['feature']+': '+e['detail'] for e in events['events'] if e['kind']=='unsupported')
        self.assertIn('per-pixel transparency',messages)
        self.assertIn('Treat as opaque',messages)
        self.assertNotIn('clip_image_mask',messages)
        self.assertNotIn('reservation/placeholder',messages)

    def test_image_alpha_obeys_transparency_policy_without_popping_outer_clips(self):
        import io
        from PIL import Image
        stream=io.BytesIO();Image.new('RGBA',(12,12),(255,0,0,80)).save(stream,format='PNG')
        pdf=self.root/'alpha-policies.pdf';doc=pymupdf.open();page=doc.new_page(width=60*PT,height=60*PT)
        # Earlier horizontal ink, an alpha image, then a line inside the same
        # outer clip. A missing image-mask push would expose that line's ends.
        page.draw_line((2*PT,30*PT),(58*PT,30*PT),width=.3*PT)
        page.insert_image((20*PT,20*PT,40*PT,40*PT),stream=stream.getvalue())
        page.draw_line((2*PT,45*PT),(58*PT,45*PT),width=.3*PT)
        content=b'\n'.join(doc.xref_stream(x) for x in page.get_contents())
        xref=doc.get_new_xref();doc.update_object(xref,'<<>>');doc.update_stream(xref,b'q '+f'{10*PT} {10*PT} {40*PT} {40*PT} re W n\n'.encode()+content+b'\nQ')
        page.set_contents(xref);doc.save(pdf);doc.close();original=pdf.read_bytes()
        for raster,transparency in [('dither','ignore-alpha'),('dither','omit'),('omit','reject'),('placeholder','ignore-alpha'),('reserve','ignore-alpha')]:
            with self.subTest(raster=raster,transparency=transparency):
                policies={**DEFAULT_POLICIES,'raster':raster,'transparency':transparency}
                device=PolicyDevice(policies,{},.3*PT);events,_=extract(pdf,device_factory=lambda:device)
                self.assertFalse([e for e in events['events'] if e['kind']=='unsupported'])
                depth=0
                for e in events['events']:
                    if e['kind'].startswith('clip_'):depth+=1
                    if e['kind']=='pop_clip':depth-=1
                    self.assertGreaterEqual(depth,0)
                self.assertEqual(depth,0)
                if raster=='dither' and transparency=='ignore-alpha':
                    self.assertEqual(len(device.images),1)
                    with Image.open(io.BytesIO(next(iter(device.images.values())))) as decoded:
                        self.assertEqual(decoded.getpixel((6,6)),(255,0,0),'Opaque means source color, not alpha-darkened RGB')
                else:self.assertFalse(device.images)
                if transparency=='omit' or raster=='omit':
                    self.assertFalse([e for e in events['events'] if e['kind']=='fill'])
        # Real Rust/HPGL checks for mask-stack handling and both paint policies.
        for transparency in ['ignore-alpha','omit']:
            intent=copy.deepcopy(self.intent);intent['bindings']=[];intent['source_sha256']=hashlib.sha256(original).hexdigest()
            intent['palette']['inks']=[dict(id='black',label='Black',color_srgb='#000000',width_mm=.3)]
            intent['pages']=[dict(page_index=0,source_size_mm=[60,60],placement=dict(scale=1,rotation_deg=0,offset_mm=[0,0]),
                                 target_media=dict(schema='plotline.media-profile/1',id='test-alpha',revision=1,label='Test',sheet_size_mm=[60,60],printable_rect_mm=[0,0,60,60],content_inset_mm=0))]
            intent['policies'].update(raster='dither',transparency=transparency,raster_settings=dict(screening=True,palette='k',fullness=1,edge_falloff_mm=6.35,black_treatment='off',inks={'K':'black'}))
            output=self.root/('alpha-'+transparency);output.mkdir()
            result=convert(pdf,resolve_intent(pdf,intent),output,ROOT/'target/release/plotline')
            paths=decoded_mm(output/'page-0/plot.hpgl',60);self.assertTrue(paths)
            for _,points in paths:self.assertLess(LineString(points).buffer(.15).difference(box(10,10,50,50)).area,1e-7)
            self.assertEqual(len(result['pages'][0].get('raster_images',[])),1 if transparency=='ignore-alpha' else 0)
        self.assertEqual(pdf.read_bytes(),original,'Source PDF and its alpha remain immutable')

    def test_larger_target_cannot_reveal_art_outside_source_page(self):
        pdf=self.root/'source-clip.pdf';doc=pymupdf.open();page=doc.new_page(width=50*PT,height=50*PT)
        page.draw_line((-20*PT,25*PT),(70*PT,25*PT),width=.4*PT);doc.save(pdf);doc.close()
        intent=copy.deepcopy(self.intent);intent['source_sha256']=hashlib.sha256(pdf.read_bytes()).hexdigest();intent['bindings']=[]
        intent['pages']=[dict(page_index=0,source_size_mm=[50,50],placement=dict(scale=1,rotation_deg=0,offset_mm=[60,60]),
                             target_media=self.intent['pages'][0]['target_media'])]
        directory=self.root/'source-clip';directory.mkdir();convert(pdf,intent,directory,ROOT/'target/release/plotline')
        paths=decoded_mm(directory/'page-0/plot.hpgl',279.4)
        self.assertTrue(paths)
        for pen,line in paths:
            radius=intent['palette']['inks'][pen-1]['width_mm']/2
            self.assertTrue(box(60,60,110,110).covers(LineString(line).buffer(radius)))

    def test_inventory_provenance_cannot_change_document_inks_or_drawing(self):
        intent=copy.deepcopy(self.intent)
        for slot,ink in enumerate(intent['palette']['inks'],1):
            ink['source']=dict(kind='plotline-carousel',revision=9,slot=slot)
        before=copy.deepcopy(intent)
        slots={str(n):dict(state='loaded',width_mm=ink['width_mm'],color=ink['color_srgb'])
               for n,ink in enumerate(intent['palette']['inks'],1)}
        variants=dict(unchanged=dict(revision=9,slots=slots),
                      revision_only=dict(revision=10,slots=slots),
                      reordered=dict(revision=10,slots=dict(zip(reversed(list(slots)),slots.values()))),
                      unrelated_edit=dict(revision=10,slots={**slots,'8':dict(state='loaded',width_mm=.9,color='#123456')}),
                      original_unavailable=dict(revision=10,slots={**slots,'1':dict(state='unavailable',width_mm=None,color=None)}),
                      no_inventory=None)
        for name,inventory in variants.items():
            with self.subTest(name=name):
                resolved=resolve_intent(self.pdf,intent,inventory)
                self.assertEqual(resolved,intent)
                directory=self.root/name;directory.mkdir()
                result=convert(self.pdf,resolved,directory,ROOT/'target/release/plotline')
                for page in result['pages']:
                    folder=f"page-{page['page_index']}"
                    # Includes two black inks of different widths, OCG bindings,
                    # paper placement, white occlusion and decoded footprints.
                    for artifact in ['events.json','plot.hpgl','logical.svg','pen.svg']:
                        self.assertEqual((directory/folder/artifact).read_bytes(),(self.output/folder/artifact).read_bytes())
                self.assertEqual(intent,before,'Resolution must not rewrite the authored snapshot')

    def test_wrong_source_and_unknown_width_still_fail(self):
        intent=copy.deepcopy(self.intent);ink=intent['palette']['inks'][0]
        ink['source']=dict(kind='plotline-carousel',revision=9,slot=2)
        inventory=dict(revision=10,slots={'2':dict(state='loaded',width_mm=.15,color='#000000')})
        ink['width_mm']=None
        with self.assertRaises(Exception):resolve_intent(self.pdf,intent,inventory)
        intent=copy.deepcopy(self.intent);intent['source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'different PDF'):resolve_intent(self.pdf,intent,inventory)

    def test_nested_binding_uses_innermost_bound_layer(self):
        device=PolicyDevice(DEFAULT_POLICIES,{'outer':dict(ink_id='fine-black'),'inner':dict(ink_id='broad-black')},.3*PT)
        device.begin_layer(None,'outer');device.begin_layer(None,'unbound')
        device.events.append(dict(kind='stroke'));device.end_layer();device.begin_layer(None,'inner')
        device.events.append(dict(kind='stroke'));device.end_layer();device.end_layer()
        self.assertEqual(device.ink_bindings[0]['ink_id'],'fine-black');self.assertEqual(device.ink_bindings[1]['ink_id'],'broad-black')

    def test_invalid_optional_intent_keeps_inspectable_pdf(self):
        directory=self.root/'inspect';directory.mkdir();(directory/'intent.json').write_text('{}')
        result=inspect(self.pdf,directory)
        self.assertIn('intent_error',result);self.assertEqual(len(result['pages']),2)
        with pymupdf.open(self.pdf) as pdf:
            self.assertEqual(result['layers'],sorted({g['name'] for g in pdf.get_ocgs().values()}))
        self.assertTrue((directory/'page-0-v2.png').exists())

if __name__=='__main__':unittest.main()
