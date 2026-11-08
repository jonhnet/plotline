"""Audit generated paths, chosen recovery, preserved earlier samples and notes."""
import argparse
import hashlib
import json
import math
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops


def check_joined_fill(original, joined, diameter):
    """Certify all original runs survive and every connector is already painted."""
    rows = defaultdict(list)
    canonical = lambda a,b: tuple(sorted((tuple(a),tuple(b))))
    expected = Counter(canonical(*p) for p in original)
    seen = Counter()
    for a,b in original:
        rows[a[1]].append((a[0],b[0]))
    rows = {y: sorted(runs) for y,runs in rows.items()}
    starts = {y: [a for a,b in runs] for y,runs in rows.items()}
    def containing(y, lo, hi):
        i = bisect_right(starts[y],lo)-1
        assert i >= 0
        a,b = rows[y][i]
        assert a <= lo <= hi <= b
        return a,b
    joins = 0
    r = diameter/2
    for path in joined:
        for a,b in zip(path,path[1:]):
            key = canonical(a,b)
            if key in expected:
                seen[key] += 1
            if a[1] == b[1]:
                containing(a[1],min(a[0],b[0]),max(a[0],b[0]))
            else:
                assert a[0] == b[0]
                x,dy = a[0],b[1]-a[1]
                assert 0 < dy < diameter
                l0,r0 = containing(a[1],x,x)
                l1,r1 = containing(b[1],x,x)
                # At the midpoint of the row gap, the bridge's outside edges
                # must be within radius r of either row's horizontal segment.
                # The nearest row is closer everywhere else along the bridge.
                for distance in [max(0,max(l0,l1)-(x-r)),max(0,(x+r)-min(r0,r1))]:
                    assert distance*distance+(dy/2)**2 <= r*r+1e-12
                joins += 1
    assert seen == expected
    assert joins == len(original)-len(joined)
    return joins


def check(output, state, before=None, notes_before=None):
    portfolio = json.loads((output / 'portfolio.json').read_text())
    recipes = json.loads(Path(__file__).with_name('experiments.json').read_text())
    assert portfolio['status'] == 'complete'
    assert portfolio['experiments'] == recipes
    code = b''.join(Path(__file__).with_name(name).read_bytes() for name in
                    ['engine.py', 'waves.py', 'focus.py', 'study.py', 'lettering.py', 'highlights.py', 'ink_fit.py', 'fill_join.py', 'paper.py', 'combined.py', 'palette.py', 'build.py'])
    assert portfolio['code_sha256'] == hashlib.sha256(code).hexdigest(), 'Build is not current'
    count = 0
    retained = set(portfolio.get('retained_result_keys', []))
    for cells in portfolio['cells'].values():
        assert len(cells) == len(recipes)
        for cell in cells.values():
            folder = output / cell['artifact']
            drawing = json.loads((folder / 'paths.json').read_text())
            meters = area = runs = 0
            for ink, paths in drawing['paths'].items():
                pen = drawing.get('pen_mm_by_ink', {}).get(ink, drawing['pen_mm'])
                distance = 0
                for path in paths:
                    assert len(path) >= 2
                    for x, y in path:
                        assert pen/2 <= x <= drawing['width_mm'] - pen/2
                        assert pen/2 <= y <= drawing['height_mm'] - pen/2
                    distance += sum(math.dist(a, b) for a, b in zip(path, path[1:]))
                meters += distance / 1000
                area += distance * pen
                runs += len(paths)
            assert abs(meters - cell['metrics']['meters']) < 3e-5
            expected_area = cell['metrics']['ink_area_ratio'] * drawing['width_mm'] * drawing['height_mm']
            assert abs(area - expected_area) < .02  # metrics round channel distances to 0.01 mm
            assert runs == cell['metrics']['paths'] == (folder / 'paths.svg').read_text().count('<path ')
            assert cell['signature']['code'] == portfolio['code_sha256'] or cell['key'] in retained
            assert hashlib.sha256(json.dumps(cell['signature'], sort_keys=True).encode()).hexdigest()[:20] == cell['key']
            if cell['signature']['recipe'].get('edge_fade_mm', 0) > 0:
                assert (folder / 'ink-focus.png').is_file()
            if cell['signature']['recipe']['method'] == 'component_study':
                assert 'lettering' in cell
                assert (folder / 'threshold-mask.png').is_file()
                assert (folder / 'accepted-skeleton.png').is_file() == cell['lettering'].get('skeleton_used', True)
            count += 1
    preserved = 0
    if before:
        old = json.loads(before.read_text())
        if retained:
            assert portfolio['retained_from_sha256'] == hashlib.sha256(before.read_bytes()).hexdigest()
            assert retained <= {c['key'] for cells in old['cells'].values() for c in cells.values()}
        for row, cells in old['cells'].items():
            for key, cell in cells.items():
                for file in ['preview.png', 'paths.svg', 'paths.json']:
                    assert (output / cell['artifact'] / file).read_bytes() == (output / portfolio['cells'][row][key]['artifact'] / file).read_bytes(), (row, key, file)
                preserved += 1
    sources = json.loads((state / 'sources.json').read_text())
    for source in sources['images']:
        assert hashlib.sha256((state / 'inputs' / source['file']).read_bytes()).hexdigest() == source['sha256']
    notes = list((state / 'comments').glob('*.json'))
    for path in notes:
        note = json.loads(path.read_text())
        assert (state / 'comments/samples' / note['sample']['result_key'] / 'preview.png').is_file()
    saved = {}
    if notes_before:
        saved = json.loads(notes_before.read_text())
        for relative, sha in saved.items():
            assert hashlib.sha256((state / 'comments' / relative).read_bytes()).hexdigest() == sha, relative
    screens = fitted_count = weighted_count = joined_count = identical_fills = paper_count = 0
    paper_raster_differences = {}
    for row in portfolio['images']:
        cells = portfolio['cells'][row['id']]
        def vectors(id):
            return json.loads((output / cells[id]['artifact'] / 'paths.json').read_text())
        chosen = vectors(row['recovery_reference'])
        assert vectors('choice-ink') == chosen, row['id']
        assert vectors('review-ink') == chosen, row['id']
        assert vectors('highlight-reference') == vectors('choice-full'), row['id']
        assert vectors('letter-native') == vectors('letter-dark'), row['id']
        assert vectors('thin-reference') == vectors('letter-cubic-narrow'), row['id']
        smooth = vectors('fit-smooth')['paths']['K']
        weighted = vectors('fit-weight')['paths']['K']
        assert weighted[:len(smooth)] == smooth, row['id']
        baseline = vectors('thin-reference')['paths']['K']
        assert len(smooth) == len(baseline), row['id']
        assert all(a[0] == b[0] and a[-1] == b[-1] for a, b in zip(baseline, smooth)), row['id']
        # Independently check high-resolution circular pen polygons, rather
        # than relying on the fitter's coarse conservative footprint test.
        import numpy as np
        from shapely.geometry import LineString
        from . import engine, ink_fit
        drawing = engine.Drawing(**vectors('fit-smooth'))
        with Image.open(output / cells['fit-smooth']['artifact'] / 'threshold-mask.png') as image:
            ink = ink_fit.pixel_shape(np.asarray(image) < 128, drawing)
        for before_path, path in zip(baseline, smooth):
            if before_path == path:
                continue
            painted = LineString(path).buffer(drawing.pen_mm/2, quad_segs=64)
            assert ink.covers(painted) or painted.difference(ink).area < 1e-10, (row['id'], 'fit outside ink')
            fitted_count += 1
        for path in weighted[len(smooth):]:
            painted = LineString(path).buffer(drawing.pen_mm/2, quad_segs=64)
            assert ink.covers(painted) or painted.difference(ink).area < 1e-10, (row['id'], 'weight outside ink')
            weighted_count += 1
        for id in ['fit-smooth', 'fit-weight']:
            assert (output / cells[id]['artifact'] / 'accepted-skeleton.png').read_bytes() == (output / cells['thin-reference']['artifact'] / 'accepted-skeleton.png').read_bytes(), (row['id'], id)
        fill = vectors('thin-fill')
        joined = vectors('fill-joined')
        joined_count += check_joined_fill(fill['paths']['K'],joined['paths']['K'],fill['pen_mm'])
        with Image.open(output / cells['thin-fill']['artifact'] / 'preview.png') as old, Image.open(output / cells['fill-joined']['artifact'] / 'preview.png') as new:
            assert ImageChops.difference(old,new).getbbox() is None, (row['id'], 'joined fill changed rendered paint')
        identical_fills += 1
        if 'fill-paper-quarter' in cells:
            cell = cells['fill-paper-quarter']
            folder = output / cell['artifact']
            drawing = engine.Drawing(**vectors('fill-paper-quarter'))
            p = cell['paper_layout']
            assert sorted([p['width_in'], p['height_in']]) == [11, 17]
            assert p['margin_in'] == .75 and drawing.pen_mm == .25
            w, h = drawing.width_mm/25.4, drawing.height_mm/25.4
            assert math.isclose(w/h, row['oriented_pixels'][0]/row['oriented_pixels'][1])
            assert math.isclose(p['offset_x_in'], (p['width_in']-w)/2)
            assert math.isclose(p['offset_y_in'], (p['height_in']-h)/2)
            assert min(p['offset_x_in'], p['offset_y_in']) >= .75-1e-10
            assert math.isclose(min(p['offset_x_in'], p['offset_y_in']), .75)
            for key in ['left', 'right']:
                assert p['actual_margins_in'][key] == p['offset_x_in']
            for key in ['top', 'bottom']:
                assert p['actual_margins_in'][key] == p['offset_y_in']
            assert (folder/'threshold-mask.png').read_bytes() == (output/cells['thin-fill']['artifact']/'threshold-mask.png').read_bytes()
            with Image.open(folder/'threshold-mask.png') as image:
                mask = np.asarray(image) < 128
            from .lettering import ink_fill_paths
            raw = ink_fill_paths(mask, drawing)
            joined_count += check_joined_fill(raw, drawing.paths['K'], drawing.pen_mm)
            # Independently verify each original hatch's full bounding square
            # is in source ink using a summed-area table, including round caps.
            table = np.pad(mask.astype(np.int64), ((1,0),(1,0))).cumsum(0).cumsum(1)
            for (x0,y), (x1,_) in raw:
                r = drawing.pen_mm/2
                left, right = [math.floor(x/drawing.width_mm*mask.shape[1]) for x in (x0-r,x1+r)]
                top, bottom = [math.floor(v/drawing.height_mm*mask.shape[0]) for v in (y-r,y+r)]
                covered = table[bottom+1,right+1]-table[top,right+1]-table[bottom+1,left]+table[top,left]
                assert covered == (bottom-top+1)*(right-left+1)
            # Compare painted pixels at the actual 450 dpi proof resolution.
            original = engine.Drawing(drawing.width_mm,drawing.height_mm,drawing.pen_mm,{'K':raw})
            pw, ph = round(w*450), round(h*450)
            # Cairo approximates round caps/joins differently at this diameter.
            # The analytic certificate above allows NO new painted area; these
            # small A8 differences diagnose rasterization, not geometry error.
            original_pixels = engine.stroke_mask(original,'K',pw,ph,3)
            joined_pixels = engine.stroke_mask(drawing,'K',pw,ph,3)
            delta = np.abs(original_pixels.astype(np.int16)-joined_pixels.astype(np.int16))
            raster_difference = dict(max_alpha_difference=int(delta.max()),
                                     different_pixels=int(np.count_nonzero(delta)),
                                     fraction_different=float(np.count_nonzero(delta)/delta.size))
            assert raster_difference['max_alpha_difference'] <= 4, (row['id'], raster_difference)
            assert raster_difference['fraction_different'] < .001, (row['id'], raster_difference)
            paper_raster_differences[row['id']] = raster_difference
            with Image.open(folder/'preview.png') as image:
                assert image.size == (pw,ph)
            with Image.open(folder/'paper-preview.png') as image:
                assert image.size == (round(p['width_in']*150),round(p['height_in']*150))
            svg = ET.parse(folder/'paper.svg').getroot()
            assert math.isclose(float(svg.attrib['width'][:-2]),p['width_in']*25.4)
            assert math.isclose(float(svg.attrib['height'][:-2]),p['height_in']*25.4)
            assert float(svg.find('{*}g').attrib['stroke-width']) == .25
            paths = svg.findall('.//{*}path')
            assert len(paths) == len(drawing.paths['K'])
            for path, art in zip(paths,drawing.paths['K']):
                points = [tuple(map(float,xy.split(','))) for xy in path.attrib['d'][1:].split(' L')]
                assert len(points) == len(art)
                for (x,y),(ax,ay) in zip(points,art):
                    assert abs(x-ax-p['offset_x_in']*25.4) <= .000501
                    assert abs(y-ay-p['offset_y_in']*25.4) <= .000501
                    assert .75*25.4+.125 <= x <= (p['width_in']-.75)*25.4-.125
                    assert .75*25.4+.125 <= y <= (p['height_in']-.75)*25.4-.125
            paper_count += 1
        for id in ['thin-reference', 'thin-guo', 'thin-medial', 'thin-fill', 'fit-smooth', 'fit-weight', 'fill-joined']:
            cell = cells[id]
            assert (output / cell['artifact'] / 'threshold-mask.png').read_bytes() == (output / cells['letter-cubic-narrow']['artifact'] / 'threshold-mask.png').read_bytes(), (row['id'], id)
            assert cell['signature']['recipe']['pen_mm'] == .075
            assert cell['preview_dpi'] == 450
            with Image.open(output / cell['artifact'] / 'preview.png') as image:
                assert image.size == tuple(round(cell['metrics'][key] * 450) for key in ['width_in', 'height_in'])
        for normal, fine in [('letter-native', 'letter-narrow'), ('letter-cubic', 'letter-cubic-narrow')]:
            for file in ['threshold-mask.png', 'accepted-skeleton.png']:
                assert (output / cells[normal]['artifact'] / file).read_bytes() == (output / cells[fine]['artifact'] / file).read_bytes()
            for id in [normal, fine]:
                cell = cells[id]
                assert cell['preview_dpi'] == 450
                with Image.open(output / cell['artifact'] / 'preview.png') as image:
                    assert image.size == tuple(round(cell['metrics'][key] * 450) for key in ['width_in', 'height_in'])
        for recipe in recipes:
            if recipe.get('round', 0) < 4 or recipe.get('study') != 'screens':
                continue
            cell = cells[recipe['id']]
            drawing = vectors(recipe['id'])
            paths = drawing['paths']
            assert paths['K'][:len(chosen['paths']['K'])] == chosen['paths']['K']
            assert drawing.get('pen_mm_by_ink', {}).get('K', drawing['pen_mm']) == chosen['pen_mm']
            if row['screen_palette'] == 'black':
                assert set(paths) == {'K'}
            else:
                assert paths['K'] == chosen['paths']['K']
                assert drawing['pen_mm'] == .3
            assert cell['signature']['recipe']['density'] == .7
            assert cell['signature']['recipe']['screen_analysis_long_edge_px'] == 1000
            screens += 1
    return dict(cells_checked=count, old_cells_byte_identical=preserved, preserved_notes=len(notes),
                saved_feedback_files_unchanged=len(saved), selected_references_match=len(portfolio['images']),
                fitted_paths_inside_source_ink=fitted_count, added_weight_paths_inside_source_ink=weighted_count,
                certified_fill_joins=joined_count, pixel_identical_joined_previews=identical_fills,
                paper_fits_and_joined_coverage_verified=paper_count,
                paper_join_rasterization=paper_raster_differences,
                matched_screen_peaks_and_black_paths=screens)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--notes-before', type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.output, args.state, args.before, args.notes_before), indent=2))


if __name__ == '__main__':
    main()
