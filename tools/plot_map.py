#!/usr/bin/env python3
"""Build a styled OSM map, convert it, check it and write slicer-style travel tables."""
import argparse
import json
from pathlib import Path
from build_map import build, ROOT
from check import run, check_artifacts
from summarize_plot import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='data/roosevelt-complete-2026-10-29.osm.gz')
    parser.add_argument('--style', default='styles/plotter.json')
    parser.add_argument('--name', default='plotter-roosevelt', help='Output directory name under build/')
    parser.add_argument('--bbox', nargs=4, type=float, metavar=('WEST','SOUTH','EAST','NORTH'),
                        help='Geographic bounds to compose; defaults to the OSM snapshot bounds')
    parser.add_argument('--reuse-map', action='store_true', help='Keep the existing PDF and rerun conversion using the supplied pen/fill settings')
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name in ('.','..'):
        raise ValueError('--name must be a single directory name')
    style = json.loads((ROOT/args.style).read_text())
    source = ROOT/'build'/args.name/'source'
    if args.reuse_map:
        provenance = json.loads((source/'map.provenance.json').read_text())
        old_style = provenance['style']
        if args.bbox is not None and args.bbox != provenance['bounds']:
            raise ValueError('--reuse-map cannot apply different geographic bounds; rebuild the PDF')
        conversion_keys = {'pen_width_pt','fill_style','hatch_spacing_pt','stroke_width_tolerance'}
        if {k:v for k,v in style.items() if k not in conversion_keys} != {k:v for k,v in old_style.items() if k not in conversion_keys}:
            raise ValueError('--reuse-map cannot apply changes to map styling; rebuild the PDF')
    else:
        build(ROOT/args.input, source, style, args.bbox)
    print(run('cargo','build','--release','--locked'))
    pdf, events = source/'map.pdf', source/'map.events.json'
    print(run('.venv/bin/python','tools/pdf_native.py',str(pdf),str(events)))
    print('Converting PDF geometry and verifying decoded pen footprints...', flush=True)
    print(run('target/release/plotline',str(events),'build/'+args.name,
              str(style['pen_width_pt']),'40',style['fill_style'],'red-green-black',str(style['hatch_spacing_pt']),
              'strict' if style['stroke_width_tolerance'] is None else str(style['stroke_width_tolerance'])))
    result = check_artifacts(args.name,str(pdf),dpi='72')
    summary = summarize(ROOT/'build'/args.name)
    (ROOT/'build'/args.name/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(run('mutool','draw','-q','-r','72','-o',f'build/{args.name}/comparison.png',f'build/{args.name}/comparison.svg'))
    print((ROOT/'build'/args.name/'travel.md').read_text().split('### Feature')[0])
    if not args.reuse_map:
        # Snapshot-specific checks belong in check_map.py; this command accepts other maps.
        print('PDF:',pdf)
    print('Ink preview:',ROOT/'build'/args.name/'hpgl.png')
    print('Travel CSV:',ROOT/'build'/args.name/'travel.csv')
    return summary


if __name__ == '__main__':
    main()
