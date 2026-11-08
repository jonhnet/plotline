#!/usr/bin/env python3
"""Reproduce the original Roosevelt export and its focused PDF regressions."""
import argparse
import json
from check import ROOT, check_artifacts, check_case, run
from summarize_plot import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse', action='store_true', help='Validate existing artifacts instead of rebuilding them')
    args = parser.parse_args()
    results = []
    if not args.reuse:
        print(run('cargo', 'build', '--release', '--locked'))
        print(run('.venv/bin/python', 'tools/make_pattern_fixtures.py'))
    for name, mode in [('patterns', 'expanded_patterns'), ('pattern-bbox', 'default'), ('miter-limits', 'default')]:
        pdf = f'build/fixtures/{name}.pdf'
        if args.reuse:
            result = check_artifacts(name, pdf, reference_mode=mode)
        else:
            result = check_case(name, pdf, '0.4', adapter='native', binary='target/release/plotline', reference_mode=mode)
        results.append(result)
    name = 'roosevelt'
    pdf = f'build/fixtures/{name}.pdf'
    if args.reuse:
        result = check_artifacts(name, pdf, dpi='72')
    else:
        print(run('.venv/bin/python', 'tools/make_roosevelt_fixture.py'))
        result = check_case(name, pdf, '0.4', adapter='native', binary='target/release/plotline',
                            palette='red-green-black', dpi='72')
    results.append(result)
    summary = summarize(ROOT / 'build' / name)
    probe = json.loads((ROOT / f'build/fixtures/{name}.json.probe.json').read_text())
    if len(probe['patterns']) != 49 or len(probe['glyphs']) < 6800:
        raise AssertionError('Roosevelt pattern/font extraction census changed')
    if summary['source_nonwhite_colors'] != 25 or summary['metrics']['pen_selections'] != 3:
        raise AssertionError('Source palette or three-pen output changed')
    # These bound total loss independently of area fractions: tiny details must
    # not disappear unnoticed merely because a large road dominates the area.
    if summary['representation']['glyph_contours']['paints_without_paths'] > 3:
        raise AssertionError('More glyph fragments disappeared')
    if summary['representation']['stroke_contours']['paints_without_paths'] > 498:
        raise AssertionError('More stroke fragments disappeared')
    if summary['metrics']['paths'] > 105000 or summary['metrics']['pen_down_mm'] > 1900000:
        raise AssertionError('Roosevelt path count/ink travel regressed')
    run('mutool', 'draw', '-q', '-r', '90', '-o', f'build/{name}/comparison.png', f'build/{name}/comparison.svg')
    (ROOT / 'build/validation-roosevelt.json').write_text(json.dumps(results, indent=2)+'\n')
    for result in results:
        print(f"PASS {result['case']}: interpretation {result['interpretation_large_pixel_discrepancy_ratio']:.3%} "
              f"({result['reference_mode']}, {result['dpi']:g} dpi); "
              f"default PDF raster {result['default_pdf_raster_discrepancy_ratio']:.3%}")
    print('PASS Roosevelt representation and three-pen gates; summary.json and unrepresented-paints.json contain the remaining gaps')


if __name__ == '__main__':
    main()
