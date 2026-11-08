"""Independently audit the palette study's paths, provenance and comparisons."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from . import paper


def check(output, state):
    portfolio = json.loads((output/'palette-portfolio.json').read_text())
    recipes = json.loads(Path(__file__).with_name('palette-experiments.json').read_text())
    assert portfolio['status'] == 'complete'
    assert portfolio['experiments'] == recipes
    code = b''.join(Path(__file__).with_name(name).read_bytes() for name in
        ['engine.py','waves.py','focus.py','study.py','lettering.py','highlights.py',
         'ink_fit.py','fill_join.py','paper.py','combined.py','palette.py','build.py'])
    assert portfolio['code_sha256'] == hashlib.sha256(code).hexdigest()
    sources = json.loads((state/'palette-sources.json').read_text())['images']
    assert [s['id'] for s in sources] == [s['id'] for s in portfolio['images']]
    count = 0
    for source in sources:
        assert hashlib.sha256((state/'inputs'/source['file']).read_bytes()).hexdigest() == source['sha256']
        cells = portfolio['cells'][source['id']]
        assert set(cells) == {r['id'] for r in recipes}
        for recipe in recipes:
            cell = cells[recipe['id']]
            signature = cell['signature']
            assert signature['code'] == portfolio['code_sha256'] or cell['key'] in portfolio.get('retained_result_keys', [])
            assert signature['source'] == source['sha256']
            assert signature['recipe'] == recipe
            assert cell['key'] == hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:20]
            folder = output/cell['artifact']
            drawing = json.loads((folder/'paths.json').read_text())
            assert drawing['ink_colors'] == recipe['ink_colors']
            fitted = paper.layout(source['pixels'],recipe['paper'])
            assert math.isclose(drawing['width_mm']/25.4,fitted['art_width_in'])
            assert math.isclose(drawing['height_mm']/25.4,fitted['art_height_in'])
            meters = paths = 0
            for ink, runs in drawing['paths'].items():
                assert ink in recipe['ink_colors']
                radius = drawing.get('pen_mm_by_ink',{}).get(ink,drawing['pen_mm'])/2
                for run in runs:
                    assert len(run) >= 2
                    for x,y in run:
                        assert radius <= x <= drawing['width_mm']-radius
                        assert radius <= y <= drawing['height_mm']-radius
                    meters += sum(math.dist(a,b) for a,b in zip(run,run[1:]))/1000
                paths += len(runs)
            assert abs(meters-cell['metrics']['meters']) < .00005
            assert paths == cell['metrics']['paths']
            assert (folder/'paths.svg').read_text().count('<path ') == paths
            assert cell['combined']['recovered_paths'] == 0
            for file in ('preview.png','thumb.png','paper-preview.png','paper.svg'):
                assert (folder/file).is_file()
            if recipe['palette_mapping'] == 'swatch':
                report = cell['combined']['separation']
                assert report['colors'] == recipe['ink_colors']
                assert report['converged']
                for curve in report['software_tone_curves'].values():
                    assert curve['coverage'][0] == 0
                    assert all(0 <= a <= b <= 1 for a,b in zip(curve['coverage'],curve['coverage'][1:]))
            count += 1
        print(source['id'], {id:dict(meters=round(c['metrics']['meters'],1),
              coarse_rgb_error=round(c['metrics']['blurred_rgb_mae'],4)) for id,c in cells.items()})
    assert portfolio['cells']['img-0456']['palette-extended-oklab']['metrics']['by_ink']['B']['paths'] > 0
    # Independent rendered-pixel check, not the solver's own error metric.
    import numpy as np
    from PIL import Image
    from skimage.color import rgb2hsv
    cell = portfolio['cells']['palette-ramps']['palette-extended-oklab']
    with Image.open(output/cell['artifact']/'preview.png') as image:
        w,h = image.size
        patch = np.asarray(image.crop((int(w*.64),int(h*.15),int(w*.66),int(h*.18))))
    hue = float(rgb2hsv(patch.mean((0,1))[None,None,:]/255)[0,0,0]*360)
    assert 225 < hue < 255, ('Saturated blue patch became purple',hue)
    print(f'PASS {count} palette samples: provenance, physical paths, paper placement, ink identities, mileage, solver convergence.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--state',required=True,type=Path)
    args = parser.parse_args()
    check(args.output,args.state)
