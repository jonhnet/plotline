#!/usr/bin/env python3
"""Audit airport pen footprints through real PDF conversion, without hardware."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import shapely
from shapely.geometry import LineString, box
from shapely.ops import substring

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cartographer.compose import compose
from cartographer.geography import framing
from cartographer.model import default_recipe, layout, MM
from check_cartographer_roads import decoded_mm


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',type=Path,required=True)
    driver=parser.parse_args().driver_root.resolve()
    (ROOT/'build').mkdir(exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix='airport-pens-',dir=ROOT/'build'));results=[]
    for diameter in [.3,.5,.7]:
        recipe=default_recipe();recipe['center']=[0,0];recipe['framing']['scale_denominator']=10000
        recipe['furniture']=dict.fromkeys(recipe['furniture'],False)
        recipe['features'].update(parks=False,buildings='all',labels='none',roads='none',shore='none')
        recipe['ink_roles']['airports']='red'
        for ink in recipe['palette']['inks']:ink['width_mm']=diameter
        airports=[dict(kind='airports',id='runway',name='',cls='runway',geometry=LineString([(-10000,-350),(10000,-350)])),
                  dict(kind='airports',id='taxiway',name='',cls='taxiway',geometry=LineString([(-1000,300),(0,450),(1000,300)]))]
        data=dict(source={'attribution':'Fixture'},issues=[],roads=[],airports=airports,areas=[
            dict(kind='airports',id='apron',name='',cls='apron',geometry=box(-1000,650,1000,900)),
            dict(kind='buildings',id='later-paint',name='',geometry=box(-100,-450,100,-250))])
        out=directory/str(diameter);authored=out/'map';converted=out/'converted'
        with patch('cartographer.compose.dataset',return_value=data):
            compose(recipe,authored,quality='proof',cache=directory/'cache',version='airport-pens')
        with (out/'converter.log').open('w') as log:
            subprocess.run([str(driver/'.venv/bin/python'),str(driver/'tools/pdf_driver.py'),'convert',
                            '--pdf',str(authored/'map.pdf'),'--intent',str(authored/'map.print-intent.json'),
                            '--directory',str(converted),'--artifacts','full'],cwd=driver,check=True,stdout=log,stderr=log)
        paths=decoded_mm(converted/'page-0/plot.hpgl',recipe['media']['sheet_size_mm'][1])
        assert paths,'Airport geometry disappeared'
        coverage=shapely.union_all([p.buffer(diameter/2,quad_segs=16) for p in paths])
        report=json.loads((converted/'page-0/report.json').read_text())
        paints=[p for p in report['decoded_hpgl'] if not p['white'] and p['visible_area_pt2']>0]
        assert all(p['outside_allowed_area_pt2']<1e-6 for p in paints),'Pen crossed clipping or later white paint'
        scale,offset=framing(recipe);frame=box(*layout(recipe)['frame_mm'])
        def project(g):return shapely.affinity.affine_transform(g,[scale/MM,0,0,-scale/MM,offset[0]/MM,offset[1]/MM])
        occlusion=project(data['areas'][1]['geometry'])
        for a,core in zip(airports,[1.6,.5]):
            line=project(a['geometry'])
            for side in [-1,1]:
                edge=line.offset_curve(side*(core/2+diameter/2+.03),join_style='round')
                expected=substring(edge,3,edge.length-3).intersection(frame.buffer(-3)).difference(occlusion.buffer(1))
                assert expected.difference(coverage.buffer(.03)).length<.15,(diameter,a['id'],'Missing airport edge')
            white=line.buffer(core/2-.02,cap_style='flat').intersection(frame).difference(occlusion.buffer(1))
            assert coverage.intersection(white).area<.001,(diameter,a['id'],'White center filled')
        apron_edge=project(data['areas'][0]['geometry']).boundary
        assert apron_edge.difference(coverage.buffer(.03)).length<.15,'Missing apron outline'
        assert coverage.intersection(occlusion.buffer(-1)).area<1e-6,'Airport crossed later white building paint'
        assert coverage.difference(frame).area<1e-6,'Airport escaped printable viewport'
        results.append(dict(diameter_mm=diameter,paths=len(paths),passed=True))
    (directory/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(dict(passed=True,directory=str(directory),results=results)))


if __name__=='__main__':main()
