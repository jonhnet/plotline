"""Synthetic scan tests: registration, effective widths and center-band color."""
import copy
import importlib.util
import json
import math
import re
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pen_calibration',ROOT/'tools/pen_calibration.py');cal=importlib.util.module_from_spec(spec);spec.loader.exec_module(cal)


def target_fixture(directory):
    target=dict(id='fixture',sheet_code=314159,slots=[2,5],carousel=dict(revision=1,slots={
        '2':dict(label='Blue',color='#16375d',width_mm=None,state='loaded',speed_cm_s=5,force=4),
        '5':dict(label='Green',color='#5d9b31',width_mm=.7,state='loaded',speed_cm_s=5,force=4)}),
        registration=dict(sheet_size_mm=[279.4,215.9],sheet_to_device_units=[0,40,40,0,-4798,-5588]))
    directory.mkdir(parents=True,exist_ok=True);cal.write_json(directory/'target.json',target);return cal.generate(directory)


def synthetic_scan(target,path,dpi=600,angle=2,paper=(237,246,227),widths=None,missing=None):
    widths=widths or {2:.3,5:.7};missing=missing or set();ppm=dpi/25.4;theta=math.radians(angle)
    rotation=np.array([[math.cos(theta),math.sin(theta)],[-math.sin(theta),math.cos(theta)]])*ppm
    w,h=target['registration']['sheet_size_mm']
    corners=np.array([[0,0],[w,0],[0,h],[w,h]])@rotation
    offset=-corners.min(axis=0)+12;size=np.ceil(corners.max(axis=0)-corners.min(axis=0)+24).astype(int)
    image=np.empty((size[1],size[0],3),dtype=np.uint8);image[:]=paper;paper_linear=cal.linear(paper)
    for record in target['paths']:
        slot=record['slot']
        if slot in missing and record['kind']=='sample':continue
        color=target['carousel']['slots'][str(slot)]['color'];ink=cal.linear([int(color[i:i+2],16) for i in (1,3,5)])*paper_linear
        radius=widths[slot]*ppm/2
        for a,b in zip(record['points'],record['points'][1:]):
            a=np.array(a)@rotation+offset;b=np.array(b)@rotation+offset
            lo=np.maximum(0,np.floor(np.minimum(a,b)-radius-2)).astype(int);hi=np.minimum(size,np.ceil(np.maximum(a,b)+radius+2)).astype(int)
            yy,xx=np.mgrid[lo[1]:hi[1],lo[0]:hi[0]];v=np.stack([xx,yy],axis=-1)-a
            direction=b-a;t=np.clip(np.sum(v*direction,axis=-1)/sum(direction**2),0,1)
            distance=np.linalg.norm(v-t[...,None]*direction,axis=-1)
            coverage=np.clip(radius+.5-distance,0,1)[...,None]
            patch=image[lo[1]:hi[1],lo[0]:hi[0]]
            patch[:]=cal.encoded(cal.linear(patch)*(1-coverage)+ink*coverage)
    Image.fromarray(image).save(path)
    return np.vstack([rotation,offset])


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.target=target_fixture(self.root)
        self.scan=self.root/'scans'/'test';self.scan.mkdir(parents=True);cal.write_json(self.scan/'options.json',{})
    def tearDown(self):self.temp.cleanup()
    def test_separate_black_registration_pen_is_emitted_and_scan_registers(self):
        target=copy.deepcopy(self.target);target.pop('marker_slot')
        target['carousel']['slots']['1']=dict(target['carousel']['slots']['2'],label='K',color='#000000')
        target['carousel']['slots']['2']['color']='#ed96bb'
        target['carousel']['slots']['5']['color']='#e9d845'
        cal.write_json(self.root/'target.json',target);target=cal.generate(self.root)
        self.assertEqual(target['marker_slot'],1)
        self.assertEqual({p['slot'] for p in target['paths'] if p['kind']!='sample'},{1})
        self.assertEqual({s['slot'] for s in target['samples']},{2,5})
        self.assertEqual(re.findall(r'SP(\d+);',(self.root/'target.hpgl').read_text()),['1','2','5','0'])
        synthetic_scan(target,self.scan/'original.png',dpi=300,angle=3,widths={1:.3,2:.3,5:.7})
        (self.scan/'original.png').rename(self.scan/'original.scan')
        result=cal.analyze(self.scan)
        self.assertEqual([r['slot'] for r in result['rows']],[2,5])
        self.assertTrue(all(r['valid'] for r in result['rows']))

    def test_explicit_registration_pen_overrides_darkest_and_rejects_unloaded(self):
        target=copy.deepcopy(self.target);target['marker_slot']=5
        cal.write_json(self.root/'target.json',target);target=cal.generate(self.root)
        self.assertEqual({p['slot'] for p in target['paths'] if p['kind']!='sample'},{5})
        target['marker_slot']=1
        target['carousel']['slots']['1']=dict(target['carousel']['slots']['2'],state='empty',color='#000000')
        cal.write_json(self.root/'target.json',target)
        with self.assertRaisesRegex(ValueError,'loaded pen for registration'):cal.generate(self.root)

    def test_all_eight_pens_fit_landscape_letter_with_long_edge_back_margin(self):
        target=copy.deepcopy(self.target);target['slots']=list(range(1,9))
        target['carousel']['slots']={str(n):dict(target['carousel']['slots']['5'],width_mm=None) for n in target['slots']}
        cal.write_json(self.root/'target.json',target);target=cal.generate(self.root)
        # Independently decode every commanded point and include the maximum
        # supported pen radius, not only the centerline or the preview bounds.
        hpgl=(self.root/'target.hpgl').read_text()
        points=np.array([(int(x),int(y)) for x,y in re.findall(r'(?:PU|PD)(-?\d+),(-?\d+);',hpgl)])
        radius=2.5*40
        self.assertTrue(np.all(points.min(axis=0)-radius>[-3238,-4988]))
        self.assertTrue(np.all(points.max(axis=0)+radius<[3238,4988]))
        self.assertEqual(len(target['samples']),64)
        self.assertIn('viewBox="0 0 279.4 215.9"',(self.root/'target.svg').read_text())
        for slot in target['slots']:
            directions=[s['direction'] for s in target['samples'] if s['slot']==slot]
            self.assertEqual(sorted(directions),[0,0,45,45,90,90,135,135])

    def test_original_portrait_sheet_identifier_remains_readable(self):
        # The v1 target had no stored code centers. These coordinates describe
        # its printed marks independently of the current target generator.
        image=np.full((800,2200,3),255,dtype=np.uint8)
        bits='1'+f'{314159:020b}'+'1'
        for i,bit in enumerate(bits):
            if bit=='1':image[590:641,318+70*i:323+70*i]=20
        matrix=np.array([[10,0],[0,10],[0,0]],dtype=float)
        cal.verify_code(dict(sheet_code=314159),image,matrix)

    def test_widths_and_center_color_on_tinted_paper_with_rotated_scan(self):
        synthetic_scan(self.target,self.scan/'original.png')
        (self.scan/'original.png').rename(self.scan/'original.scan')
        result=cal.analyze(self.scan)
        self.assertLess(result['registration_error_mm'],.12)
        self.assertEqual(result['color_band_fraction'],.6)
        for row,width in zip(result['rows'],[.3,.7]):
            self.assertTrue(row['valid'],row)
            self.assertGreaterEqual(row['accepted'],100)
            self.assertAlmostEqual(row['mean_width_mm'],width,delta=.04)
            expected=self.target['carousel']['slots'][str(row['slot'])]['color']
            got=row['color_srgb']
            for i in (1,3,5):self.assertAlmostEqual(int(got[i:i+2],16),int(expected[i:i+2],16),delta=8)
            self.assertTrue((self.scan/f'slot-{row["slot"]}.png').exists())
        self.assertTrue((self.scan/'overlay.jpg').exists())
    def test_wrong_sheet_id_is_rejected(self):
        other=copy.deepcopy(self.target);other['sheet_code']=123456
        otherdir=self.root/'other';otherdir.mkdir();cal.write_json(otherdir/'target.json',other);other=cal.generate(otherdir)
        synthetic_scan(other,self.scan/'original.png',dpi=300,angle=90)
        (self.scan/'original.png').rename(self.scan/'original.scan')
        with self.assertRaisesRegex(ValueError,'does not match'):cal.analyze(self.scan)
    def test_dry_pen_is_rejected_without_inventing_width_or_color(self):
        synthetic_scan(self.target,self.scan/'original.png',dpi=300,angle=180,missing={5})
        (self.scan/'original.png').rename(self.scan/'original.scan')
        result=cal.analyze(self.scan)
        self.assertTrue(result['rows'][0]['valid'])
        self.assertFalse(result['rows'][1]['valid']);self.assertNotIn('color_srgb',result['rows'][1])
    def test_manual_registration_and_pale_inks(self):
        self.target['carousel']['slots']['2']['color']='#ed96bb'
        self.target['carousel']['slots']['5']['color']='#e9d845'
        cal.write_json(self.root/'target.json',self.target);self.target=cal.generate(self.root)
        matrix=synthetic_scan(self.target,self.scan/'original.png',dpi=300,angle=90)
        with Image.open(self.scan/'original.png') as image:size=np.array(image.size)
        points=np.array([f['center'] for f in self.target['fiducials']])@matrix[:2]+matrix[2]
        cal.write_json(self.scan/'options.json',dict(marks=(points/size).tolist()))
        (self.scan/'original.png').rename(self.scan/'original.scan')
        result=cal.analyze(self.scan)
        for row in result['rows']:
            self.assertTrue(row['valid'])
            expected=self.target['carousel']['slots'][str(row['slot'])]['color']
            for i in (1,3,5):self.assertAlmostEqual(int(row['color_srgb'][i:i+2],16),int(expected[i:i+2],16),delta=10)
    def test_center_sixty_percent_excludes_stroke_edge_color(self):
        # A 1 mm flat stroke, with red at its outer 20% on each side and blue
        # through the center 60%. Sampling is independent of target generation.
        image=np.full((500,300,3),255,dtype=np.uint8)
        image[230:270]=[220,30,20];image[238:262]=[20,60,180]
        matrix=np.array([[40,0],[0,40],[150,250]],dtype=float)
        sample,reason=cal.stroke_sample(image,matrix,np.array([0.,0.]),np.array([0.,1.]),.025)
        self.assertIsNone(reason);self.assertAlmostEqual(sample['width_mm'],1,delta=.025)
        actual=cal.encoded(sample['color_linear'])
        self.assertLess(actual[0],65);self.assertGreater(actual[2],165)


if __name__=='__main__':unittest.main()
