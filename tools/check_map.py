#!/usr/bin/env python3
"""Regression checks for the pinned OSM snapshot and default plotter style."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from check import ROOT, run, check_artifacts
from summarize_plot import summarize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse',action='store_true')
    args=parser.parse_args()
    print(run('.venv/bin/python','-m','unittest','discover','-s','tests','-p','test_*.py'))
    if not args.reuse:
        print(run('.venv/bin/python','tools/plot_map.py'))
    folder=ROOT/'build/plotter-roosevelt'
    provenance=json.loads((folder/'source/map.provenance.json').read_text())
    source=json.loads((ROOT/'data/roosevelt-complete-2026-10-29.source.json').read_text())
    data=gzip.decompress((ROOT/'data/roosevelt-complete-2026-10-29.osm.gz').read_bytes())
    if provenance['input_sha256'] != source['sha256'] or hashlib.sha256(data).hexdigest() != source['sha256']:
        raise AssertionError('OSM snapshot/provenance mismatch')
    style=json.loads((ROOT/'styles/plotter.json').read_text())
    conversion={'pen_width_pt','fill_style','hatch_spacing_pt','stroke_width_tolerance'}
    if {k:v for k,v in provenance['style'].items() if k not in conversion} != {k:v for k,v in style.items() if k not in conversion} or provenance['issues']:
        raise AssertionError('Default map style changed or OSM geometry was lost')
    labels=provenance['labels']
    if sum(l['category']=='labels/roads' for l in labels) < 60 or not {'Cowen Park','Ravenna Park','Froula Park'} <= {l['text'] for l in labels}:
        raise AssertionError('Map label census regressed')
    events=json.loads((folder/'input.events.json').read_text())
    if not all(e['color']==[0,0,0] for e in events['events'] if e.get('role')=='text'):
        raise AssertionError('Map labels must use black ink')
    if any(s.startswith('labels/addresses') for s in events['sources'].values()):
        raise AssertionError('Default style must omit house addresses')
    # Both paint operations survive extraction for each road feature.
    roads={}
    for key,source_name in events['sources'].items():
        if source_name.startswith('roads/'):
            roads.setdefault(source_name,[]).append(events['events'][int(key)])
    if len(roads)<350 or any([e['color'] for e in paints] != [[0,0,0],[1,1,1]] for paints in roads.values()):
        raise AssertionError('Road casing/white-core pair missing')
    result=check_artifacts('plotter-roosevelt',str(folder/'source/map.pdf'),dpi='72')
    summary=summarize(folder)  # Also reconciles every table with decoded travel.
    for setting,key in [('pen_width','pen_width_pt'),('fill_style','fill_style'),
                        ('hatch_spacing','hatch_spacing_pt'),('stroke_width_tolerance','stroke_width_tolerance')]:
        if summary['settings'][setting] != style[key]:
            raise AssertionError(f'Conversion setting mismatch: {key}')
    representation=summary['representation']
    if not .12 < representation['crosshatch']['ink_fraction'] < .20:
        raise AssertionError('Crosshatching became too sparse or too dense')
    if representation['glyph_contours']['ink_fraction'] < .78 or representation['glyph_contours']['paints_without_paths']:
        raise AssertionError('Lettering representation regressed')
    if representation.get('stroke_width_tolerance',{}).get('ink_fraction',0) < .58 or representation['stroke_contours']['ink_fraction'] < .76:
        raise AssertionError('Thin outline representation regressed')
    if summary['metrics']['pen_down_mm']>250000 or summary['metrics']['paths']>25000:
        raise AssertionError('Default map travel/fragmentation regressed')
    if any(p['source']=='unclassified' for p in json.loads((folder/'report.json').read_text())['decoded_hpgl'] if p['paths']):
        raise AssertionError('Travel has unattributed map ink')
    (folder/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"PASS styled OSM map: {summary['metrics']['pen_down_mm']/1000:.2f} m; PDF interpretation discrepancy {result['interpretation_large_pixel_discrepancy_ratio']:.3%}; all travel reconciled")


if __name__=='__main__':main()
