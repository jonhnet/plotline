#!/usr/bin/env python3
"""Compare a visibility optimization with retained converter artifacts.

Logical paint must be byte-identical. Tiny GEOS operation-order changes can
change HPGL grid choices on sub-pen remnants, so audit those independently of
path order/direction. The ink-difference bound cancels identical segments first;
ignoring common ink can only overestimate the final physical difference.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from shapely.geometry import LineString
from shapely import union_all


def segments(path, units_per_mm):
    result=defaultdict(set);pen=None;position=None
    for raw in path.read_text().split(';'):
        command=raw.strip();op=command[:2];arg=command[2:]
        if op=='SP': pen=int(arg)
        elif op in ('PU','PD') and arg:
            numbers=[int(n) for n in arg.split(',')]
            points=list(zip(numbers[::2],numbers[1::2]))
            if op=='PD':
                assert position is not None and pen
                for a,b in zip([position]+points,points):
                    if a!=b: result[pen].add(tuple(sorted((a,b))))
            position=points[-1]
    return {pen:{tuple((x/units_per_mm,y/units_per_mm) for x,y in line) for line in lines}
            for pen,lines in result.items()}


def check(before, after, max_ink_difference_mm2):
    a=json.loads((before/'report.json').read_text());b=json.loads((after/'report.json').read_text())
    for key in ('settings','palette','pen_widths_pt','document_inks'):
        assert a.get(key)==b.get(key),f'{key} changed'
    def digest(path):
        with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()
    assert digest(before/'coverage.svg')==digest(after/'coverage.svg'),'Logical paint changed'
    assert len(a['decoded_hpgl'])==len(b['decoded_hpgl'])
    largest_visible_delta=0.;largest_mileage_delta=0.
    for x,y in zip(a['decoded_hpgl'],b['decoded_hpgl']):
        for key in ('id','source','white','pen_id','width_status','width_layout'):
            assert x[key]==y[key],(x['id'],key)
        assert bool(x['paths'])==bool(y['paths']),(x['id'],'lost/gained representation')
        largest_visible_delta=max(largest_visible_delta,abs(x['visible_area_pt2']-y['visible_area_pt2']))
        largest_mileage_delta=max(largest_mileage_delta,abs(x['pen_down_mm']-y['pen_down_mm']))
        assert y['outside_allowed_area_pt2']<=1e-7
        assert (y['uncovered_required_area_pt2'] or 0)<=1e-7
        assert not y['white'] or not y['paths']
    assert largest_visible_delta<=1e-7,largest_visible_delta
    old=segments(before/'plot.hpgl',a['settings']['units_per_mm'])
    new=segments(after/'plot.hpgl',b['settings']['units_per_mm'])
    changed={};area=0.
    for pen in old.keys()|new.keys():
        removed=old.get(pen,set())-new.get(pen,set());added=new.get(pen,set())-old.get(pen,set())
        radius=b['pen_widths_pt'][pen-1]*25.4/72/2
        def ink(lines):return union_all([LineString(line).buffer(radius,quad_segs=32) for line in lines])
        bound=ink(removed).symmetric_difference(ink(added)).area
        area+=bound
        changed[pen]=dict(removed_segments=len(removed),added_segments=len(added),ink_difference_upper_bound_mm2=bound)
    assert area<=max_ink_difference_mm2,(area,max_ink_difference_mm2)
    return dict(logical_paint_sha256=digest(after/'coverage.svg'),
                largest_visible_area_delta_pt2=largest_visible_delta,
                largest_per_paint_mileage_delta_mm=largest_mileage_delta,
                ink_difference_upper_bound_mm2=area,changed_segments_by_pen=changed,
                before_metrics=a['metrics'],after_metrics=b['metrics'],before_timing=a['timing'],after_timing=b['timing'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path);parser.add_argument('after',type=Path)
    parser.add_argument('--max-ink-difference-mm2',type=float,default=1.)
    args=parser.parse_args()
    print(json.dumps(check(args.before,args.after,args.max_ink_difference_mm2),indent=2))
