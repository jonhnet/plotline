#!/usr/bin/env python3
"""Compose roads and independently audit the real converter's decoded HPGL.

Uses synthetic vector data, no network, inventory writes or physical plotting.
"""
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

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from cartographer.compose import compose
from cartographer.geography import framing
from cartographer.model import default_recipe, layout, MM


def decoded_mm(filename,height):
    """Independent decoder for the converter's absolute PA/SP/PU/PD subset."""
    position=None;paths=[]
    for command in filename.read_text().split(';'):
        command=command.strip();op=command[:2];arg=command[2:]
        if op in ('PU','PD') and arg:
            values=[int(n) for n in arg.split(',')]
            points=[(values[i]/40,height-values[i+1]/40) for i in range(0,len(values),2)]
            if op=='PD':paths.append(LineString([position]+points))
            position=points[-1]
    return paths


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',type=Path,required=True)
    args=parser.parse_args();driver=args.driver_root.resolve()
    directory=Path(tempfile.mkdtemp(prefix='cartographer-roads-',dir=ROOT/'build'))
    results=[]
    for diameter in [.3,.5,.7]:
        recipe=default_recipe();recipe['center']=[0,0]
        recipe['framing']['scale_denominator']=10000
        recipe['furniture']={key:False for key in recipe['furniture']}
        recipe['features'].update(parks=False,buildings='none',labels='none',roads='all')
        for ink in recipe['palette']['inks']:ink['width_mm']=diameter
        roads=[dict(id='straight',cls='residential',bridge=False,geometry=LineString([(-1000,-450),(1000,-450)])),
               dict(id='diagonal',cls='primary',bridge=False,geometry=LineString([(-1000,-100),(1000,100)])),
               dict(id='bridge',cls='motorway',bridge=True,geometry=LineString([(-1000,450),(0,550),(1000,450)]))]
        for road in roads:road.update(name='',level=int(road['bridge']))
        data=dict(source={'attribution':'Fixture'},issues=[],areas=[],roads=roads)
        authored=directory/str(diameter)/'map';converted=directory/str(diameter)/'converted'
        with patch('cartographer.compose.dataset',return_value=data):
            compose(recipe,authored,quality='proof',cache=directory/'cache',version='roads-regression')
        with (authored.parent/'converter.log').open('w') as log:
            subprocess.run([str(driver/'.venv/bin/python'),str(driver/'tools/pdf_driver.py'),'convert',
                            '--pdf',str(authored/'map.pdf'),'--intent',str(authored/'map.print-intent.json'),
                            '--directory',str(converted),'--artifacts','full'],cwd=driver,check=True,stdout=log,stderr=log)
        paths=decoded_mm(converted/'page-0/plot.hpgl',recipe['media']['sheet_size_mm'][1])
        assert paths,'All roads disappeared'
        footprint=shapely.union_all([path.buffer(diameter/2,quad_segs=16) for path in paths])
        report=json.loads((converted/'page-0/report.json').read_text())
        paints=[p for p in report['decoded_hpgl'] if not p['white'] and p['visible_area_pt2']>0]
        assert len(paints)==6 and all(p['paths'] for p in paints),paints
        assert all(p['outside_allowed_area_pt2']<1e-6 for p in paints),'Pen crossed a clip or later paint'
        scale,offset=framing(recipe);length=0
        for road,core in zip(roads,[.7,1.7,2.8]):
            line=LineString([((x*scale+offset[0])/MM,(-y*scale+offset[1])/MM) for x,y in road['geometry'].coords])
            length+=line.length
            # Sample both edge centerlines, independently of converter reports.
            # Exclude end caps; retain the bridge's change of direction.
            for side in [-1,1]:
                edge=line.offset_curve(side*(core/MM/2+diameter/2+.03),join_style='round')
                sample=substring(edge,3,edge.length-3)
                uncovered=sample.difference(footprint.buffer(.03)).length
                assert uncovered<.1,(diameter,road['id'],side,uncovered)
            white=line.buffer(core/MM/2-.01,cap_style='flat')
            assert footprint.intersection(white).area<.001,(diameter,road['id'],'White center filled')
        frame=box(*layout(recipe)['frame_mm'])
        assert footprint.difference(frame).area<1e-6,'Pen escaped the printable viewport'
        mileage=sum(path.length for path in paths)
        assert 1.95*length<mileage<2.15*length,(diameter,mileage,length,'Missing or retraced edge')
        results.append(dict(diameter_mm=diameter,edge_length_mm=mileage,passed=True))
    (directory/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(dict(passed=True,directory=str(directory),results=results)))


if __name__=='__main__':main()
