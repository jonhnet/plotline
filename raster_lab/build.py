"""Reproducibly build the portfolio matrix; artifacts are keyed by inputs/code."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import cairo
import cv2
import numpy as np
from PIL import Image

from . import engine

HERE = Path(__file__).resolve().parent


def atomic_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(path)


def build(state, output, experiments=None, retain_previous=False, *, source_manifest=None, index='portfolio.json'):
    engine.check_dependencies()
    output.mkdir(parents=True, exist_ok=True)
    recipes = json.loads((experiments or HERE / 'experiments.json').read_text())
    preferences = json.loads((HERE / 'preferences.json').read_text())
    profiles = {r['id']: r for r in json.loads((HERE / 'experiments.json').read_text())}
    current_round = max((r.get('round', 0) for r in recipes), default=0)
    if Path(index).name != index or not index.endswith('.json'):
        raise ValueError('Portfolio index must be a JSON filename')
    sources = json.loads((source_manifest or state / 'sources.json').read_text())
    publish_partial = not (output / index).exists()
    previous = None
    previous_bytes = None
    if retain_previous:
        previous_bytes = (output / index).read_bytes()
        previous = json.loads(previous_bytes)
        if previous['status'] != 'complete':
            raise ValueError('Only a complete portfolio can be retained')
        old_columns = {r['id']: r for r in previous['experiments']}
        for recipe in recipes:
            if recipe['id'] in old_columns and recipe != old_columns[recipe['id']]:
                raise ValueError('Retained experiment changed; give it a new ID: ' + recipe['id'])
    # Include implementation bytes and native versions, not just a hand-edited
    # version label. Cache hits require matching code/dependencies. Explicitly
    # retained controls keep their old signature instead of claiming a rebuild.
    code_sha = hashlib.sha256((HERE / 'engine.py').read_bytes() + (HERE / 'waves.py').read_bytes()
                              + (HERE / 'focus.py').read_bytes()
                              + (HERE / 'study.py').read_bytes()
                              + (HERE / 'lettering.py').read_bytes()
                              + (HERE / 'highlights.py').read_bytes()
                              + (HERE / 'ink_fit.py').read_bytes()
                              + (HERE / 'fill_join.py').read_bytes()
                              + (HERE / 'paper.py').read_bytes()
                              + (HERE / 'combined.py').read_bytes()
                              + (HERE / 'palette.py').read_bytes()
                              + Path(__file__).read_bytes()).hexdigest()
    versions = {name: importlib.metadata.version(name) for name in ('numpy', 'Pillow', 'scikit-image', 'scipy', 'shapely')}
    import shapely
    versions['geos'] = shapely.geos_version_string
    versions['cairo'] = cairo.cairo_version_string()
    versions['pycairo'] = cairo.version
    versions['opencv'] = cv2.__version__
    versions['opencv_build_sha256'] = hashlib.sha256(cv2.getBuildInformation().encode()).hexdigest()
    matrix = dict(schema=1, algorithm=engine.ALGORITHM_VERSION, code_sha256=code_sha,
                  dependencies=versions, created_utc=datetime.now(timezone.utc).isoformat(),
                  study_columns=preferences.get('study_columns', {}),
                  experiments=recipes, images=[], cells={}, status='building')
    if previous is not None:
        matrix['retained_from_sha256'] = hashlib.sha256(previous_bytes).hexdigest()
        matrix['retained_result_keys'] = []
    cv2.setNumThreads(1)
    for source in sources['images']:
        path = state / 'inputs' / source['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError(f"Source checksum mismatch: {source['id']}")
        item = {k: v for k, v in source.items() if k != 'source_url'}
        image = engine.read_image(path)
        item['oriented_pixels'] = list(image.size)
        from .study import source_palette, resolve_recovery, round_preferences
        item['screen_palette'] = source_palette(engine.analysis_image(image))
        current_preferences = round_preferences(preferences, current_round)
        preference = current_preferences['images'].get(source['id'], {})
        item['recovery_reference'] = preference.get('recovery', current_preferences['default_recovery'])
        item['screen_enabled'] = preference.get('screen_enabled', True)
        item['lettering_enabled'] = preference.get('lettering_enabled', False)
        if 'detail_view' in preference:
            item['detail_view'] = preference['detail_view']
        matrix['images'].append(item)
        dest = output / source['id']
        dest.mkdir(exist_ok=True)
        thumbnail = image.copy()
        thumbnail.thumbnail((480, 480))
        thumbnail.save(dest / 'source-thumb.png')
        # This is the normalized display image, not a replacement for input bytes.
        image.save(dest / 'source.png')
        matrix['cells'][source['id']] = {}
        for column in recipes:
            historical = round_preferences(preferences, column.get('round', 0))
            reference = historical['images'].get(source['id'], {}).get('recovery', historical['default_recovery'])
            recipe = resolve_recovery(column, reference, profiles)
            if recipe.get('palette') == 'source':
                recipe['palette'] = item['screen_palette']
                recipe['palette_rule'] = 'rgb-span>12/255 in >1% of analysis pixels'
            retained = previous and previous['cells'].get(source['id'], {}).get(recipe['id'])
            if retained:
                # This is an explicit frozen control, not a cache hit for new code.
                # Keep its actual implementation/dependency provenance and bytes.
                if retained['signature']['source'] != source['sha256'] or retained['signature']['recipe'] != recipe:
                    raise ValueError('Retained source or resolved recipe changed: ' + source['id'] + '/' + recipe['id'])
                files = ['result.json', 'preview.png', 'thumb.png', 'paths.svg', 'paths.json']
                if 'paper' in recipe:
                    files.extend(['paper-preview.png', 'paper.svg'])
                for file in files:
                    if not (output / retained['artifact'] / file).is_file():
                        raise ValueError('Missing retained artifact: ' + retained['artifact'] + '/' + file)
                matrix['cells'][source['id']][recipe['id']] = retained
                matrix['retained_result_keys'].append(retained['key'])
                print(f"{source['id']} / {recipe['id']}: retained {retained['key']}", flush=True)
                atomic_json(output / ('building-'+index), matrix)
                continue
            start = time.perf_counter()
            signature = dict(source=source['sha256'], recipe=recipe, code=code_sha, versions=versions)
            key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
            cell_dir = dest / key
            cell_dir.mkdir(exist_ok=True)
            meta_path = cell_dir / 'result.json'
            if not meta_path.exists():
                diagnostics = {}
                if recipe['method'] == 'combined_study':
                    from .combined import generate
                    drawing, report = generate(image, recipe, cell_dir)
                    diagnostics['combined'] = report
                else:
                    drawing = engine.draw(image, recipe)
                if 'paper' in recipe:
                    from .paper import layout, place
                    fitted = layout(image.size, recipe['paper'])
                    page = place(drawing, fitted)
                    page_preview, _ = engine.render(page, 150)
                    page_preview.save(cell_dir / 'paper-preview.png', dpi=(150, 150))
                    (cell_dir / 'paper.svg').write_text(engine.svg(page, source['title'] + ' — paper layout'))
                    diagnostics['paper_layout'] = dict(fitted, preview_dpi=150)
                if recipe['method'] == 'component_study':
                    from .lettering import diagnostics as lettering_diagnostics
                    diagnostics['lettering'] = lettering_diagnostics(image, drawing, recipe, cell_dir)
                if recipe.get('edge_fade_mm', 0) > 0:
                    if recipe['method'] in ('screen_study', 'combined_study'):
                        from .study import emphasis
                        field = emphasis(engine.analysis_image(image, recipe.get('screen_analysis_long_edge_px', recipe.get('analysis_long_edge_px', engine.ANALYSIS_LONG_EDGE))), drawing, recipe)
                    else:
                        from .focus import ink_focus
                        field = ink_focus(engine.analysis_image(image), drawing, recipe)
                    # White = maximum requested emphasis, black = no color ink.
                    Image.fromarray(np.round(field * 255).astype(np.uint8)).save(cell_dir / 'ink-focus.png')
                dpi = recipe.get('preview_dpi', engine.PREVIEW_DPI)
                preview, coverage = engine.render(drawing, dpi)
                preview.save(cell_dir / 'preview.png')
                thumb = preview.copy()
                thumb.thumbnail((480, 480))
                thumb.save(cell_dir / 'thumb.png')
                (cell_dir / 'paths.svg').write_text(engine.svg(drawing, source['title'] + ' — ' + recipe['name']))
                # Inspectable vectors; no image data, fill tricks or model state.
                vector_data = asdict(drawing)
                if not vector_data['pen_mm_by_ink']:
                    del vector_data['pen_mm_by_ink']
                if not vector_data['ink_colors']:
                    del vector_data['ink_colors']
                (cell_dir / 'paths.json').write_text(json.dumps(vector_data, separators=(',', ':')))
                stats = engine.metrics(drawing)
                stats['coverage'] = coverage
                # Descriptive low-frequency color error, deliberately not a pass/fail
                # threshold and never substituted for the PDF interpretation audit.
                target = np.asarray(image.resize(preview.size)).astype(np.float32) / 255
                actual = np.asarray(preview).astype(np.float32) / 255
                sigma = dpi / 25.4  # 1 mm Gaussian blur
                delta = cv2.GaussianBlur(actual, (0, 0), sigma) - cv2.GaussianBlur(target, (0, 0), sigma)
                stats['blurred_rgb_mae'] = float(np.abs(delta).mean())
                stats['seconds'] = time.perf_counter() - start
                atomic_json(meta_path, dict(key=key, signature=signature, metrics=stats,
                                           analysis_long_edge_px=recipe.get('analysis_long_edge_px', engine.ANALYSIS_LONG_EDGE),
                                           analysis_pixels=list(engine.analysis_image(image, recipe.get('analysis_long_edge_px', engine.ANALYSIS_LONG_EDGE)).shape[1::-1]),
                                           preview_dpi=dpi, **diagnostics))
            cell = json.loads(meta_path.read_text())
            cell['artifact'] = f"{source['id']}/{key}"
            matrix['cells'][source['id']][recipe['id']] = cell
            print(f"{source['id']} / {recipe['id']}: {cell['metrics']['meters']:.1f} m, "
                  f"{cell['metrics']['paths']} paths ({time.perf_counter()-start:.2f}s)", flush=True)
            atomic_json(output / ('building-'+index), matrix)
            if publish_partial:
                atomic_json(output / index, matrix)
    matrix['status'] = 'complete'
    atomic_json(output / index, matrix)
    return matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--experiments', type=Path)
    parser.add_argument('--sources', type=Path, help='Alternative private source manifest')
    parser.add_argument('--index', default='portfolio.json', help='Independent output index for a focused study')
    parser.add_argument('--retain-previous', action='store_true',
                        help='Keep unchanged old columns as frozen controls with their original code provenance')
    args = parser.parse_args()
    build(args.state, args.output, args.experiments, args.retain_previous,
          source_manifest=args.sources, index=args.index)


if __name__ == '__main__':
    main()
