"""Independent checks of the matched, physical-size combined comparisons."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from . import engine, lettering
from .check_portfolio import check_joined_fill


def check(output):
    portfolio=json.loads((output/'portfolio.json').read_text())
    result={}
    for row in portfolio['images']:
        cells=portfolio['cells'][row['id']]
        masks={};drawings={};reports={};joins=0
        for policy in ['off','ridge','luminance','neutral']:
            cell=cells['combined-'+policy];folder=output/cell['artifact']
            drawing=engine.Drawing(**json.loads((folder/'paths.json').read_text()))
            report=cell['combined'];recipe=cell['signature']['recipe']
            assert drawing.pen_mm==.25 and not drawing.pen_mm_by_ink
            assert drawing.ink_colors=={'C':'#009f7c','M':'#e32991','Y':'#efcf00','K':'#000000'}
            assert recipe['density']==.7 and recipe['interior_ink']==1
            assert report['black_policy']==policy
            assert cell['paper_layout']['margin_in']==.75
            assert sorted([cell['paper_layout']['width_in'],cell['paper_layout']['height_in']])==[11,17]
            assert abs(drawing.width_mm/drawing.height_mm-row['oriented_pixels'][0]/row['oriented_pixels'][1])<1e-9
            assert abs(min(cell['paper_layout'][key] for key in ['offset_x_in','offset_y_in'])-.75)<1e-9
            assert report['recovered_paths']+sum(report['screen_paths_by_ink'].values())==cell['metrics']['paths']
            svg=ET.parse(folder/'paths.svg').getroot()
            for ink in drawing.paths:
                assert svg.find('.//{*}g[@id="ink-'+ink+'"]').attrib['stroke']==drawing.ink_colors[ink]
            with Image.open(folder/'preview.png') as im:
                assert im.size==tuple(round(mm/25.4*300) for mm in [drawing.width_mm,drawing.height_mm])
            if policy in ['luminance','neutral']:
                with Image.open(folder/'ink-mask.png') as im:masks[policy]=np.asarray(im)<128
                raw=lettering.ink_fill_paths(masks[policy],drawing)
                recovered=drawing.paths['K'][:report['recovered_paths']]
                joins+=check_joined_fill(raw,recovered,.25)
                assert len(raw)==report['fill_joining']['original_runs']
            drawings[policy]=drawing;reports[policy]=report
        assert reports['off']['recovered_paths']==0
        assert not np.any(masks['neutral'] & ~masks['luminance'])
        dimensions={(d.width_mm,d.height_mm) for d in drawings.values()};assert len(dimensions)==1
        if row['screen_palette']=='black':
            for d in drawings.values():assert set(d.paths)=={'K'}
            assert np.array_equal(masks['neutral'],masks['luminance'])
            assert drawings['neutral']==drawings['luminance']
        discarded=int(np.sum(masks['luminance'] & ~masks['neutral']))
        result[row['id']]=dict(dark_mask_pixels=int(masks['luminance'].sum()),
                              colored_dark_pixels_excluded=discarded,
                              recovered_meters={p:round(v['recovered_meters'],3) for p,v in reports.items()},
                              certified_joins=joins)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(check(args.output),indent=2))
