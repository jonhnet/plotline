#!/usr/bin/env python3
"""Aggregate a verified plot report without conflating interpretation and ink."""
import argparse
import csv
import json
import math
from pathlib import Path


def travel_report(directory, report):
    """Lengths of decoded, quantized commands; moves are charged to destination."""
    rows = report['decoded_hpgl']
    if not all('pen_down_mm' in row for row in rows):
        return None  # Old reports require reconversion for attribution.
    groups = {k: {} for k in ('category', 'policy', 'pen', 'feature', 'source_color')}
    for row in rows:
        if not row['paths']:
            continue
        source = row.get('source', 'unclassified')
        keys = dict(category=source.split('|')[0], policy=row['representation'],
                    pen=str(report['palette'].index(row['pen_color'])+1), feature=source,
                    source_color=','.join(f'{v:.6f}' for v in row['source_color']))
        for kind, key in keys.items():
            entry = groups[kind].setdefault(key, dict(name=key, paths=0, pen_down_m=0, incoming_pen_up_m=0))
            entry['paths'] += row['paths']
            entry['pen_down_m'] += row['pen_down_mm']/1000
            entry['incoming_pen_up_m'] += row['incoming_pen_up_mm']/1000
    for kind, entries in groups.items():
        values = sorted(entries.values(), key=lambda e: -e['pen_down_m'])
        for entry in values:
            entry['share_percent'] = entry['pen_down_m']*100000/report['metrics']['pen_down_mm'] if report['metrics']['pen_down_mm'] else 0
        for field, metric in [('pen_down_m', 'pen_down_mm'), ('incoming_pen_up_m', 'pen_up_mm')]:
            if not math.isclose(sum(e[field] for e in values)*1000, report['metrics'][metric], rel_tol=1e-9, abs_tol=1e-6):
                raise AssertionError(f'{kind} travel does not reconcile with decoded HPGL: {metric}')
        groups[kind] = values
    travel = dict(groups=groups, total_pen_down_m=report['metrics']['pen_down_mm']/1000,
                  total_pen_up_m=report['metrics']['pen_up_mm']/1000,
                  note='Measured command distance, not estimated time. Pen-up moves are charged to the destination feature, including the initial move from HPGL origin. Tables are alternative breakdowns of the same total; do not add them together.')
    (directory/'travel.json').write_text(json.dumps(travel, indent=2)+'\n')
    with (directory/'travel.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['breakdown','name','paths','pen_down_m','incoming_pen_up_m','share_percent'])
        writer.writeheader()
        for kind, rows in groups.items():
            writer.writerows(dict(breakdown=kind, **row) for row in rows)
    lines = [f"Pen down: **{travel['total_pen_down_m']:,.2f} m**. Pen up: **{travel['total_pen_up_m']:,.2f} m**.", '', travel['note'], '']
    for kind in ['category', 'policy', 'pen', 'feature', 'source_color']:
        lines += [f'### {kind.replace("_", " ").title()}', '', '| Source | Paths | Pen down (m) | Share | Incoming pen up (m) |', '| --- | ---: | ---: | ---: | ---: |']
        for row in groups[kind][:20]:
            name = row['name'].replace('|', ' / ').replace('\n', ' ')
            lines.append(f"| {name} | {row['paths']:,} | {row['pen_down_m']:,.2f} | {row['share_percent']:.1f}% | {row['incoming_pen_up_m']:,.2f} |")
        lines += ['']
    (directory/'travel.md').write_text('\n'.join(lines))
    return travel


def summarize(directory):
    directory = Path(directory)
    report = json.loads((directory / 'report.json').read_text())
    travel = travel_report(directory, report)
    width_policy = {}
    for paint in report['decoded_hpgl']:
        if paint['white'] or paint['visible_area_pt2'] <= 0:
            continue
        status = paint.get('width_status','not_requested')
        bucket = width_policy.setdefault(status, dict(paints=0, paths=0, pen_down_m=0, permitted_excess_area_pt2=0))
        bucket['paints'] += 1
        bucket['paths'] += paint['paths']
        bucket['pen_down_m'] += paint.get('pen_down_mm',0)/1000
        bucket['permitted_excess_area_pt2'] += paint['outside_visible_area_pt2']
    groups, colors, missing = {}, {}, []
    for paint in report['decoded_hpgl']:
        if paint['white']:
            continue
        group = groups.setdefault(paint['representation'], dict(
            visible_area_pt2=0, ink_area_pt2=0, ink_inside_visible_area_pt2=0, excess_ink_area_pt2=0, visible_paints=0, paints_without_paths=0, paths=0))
        group['visible_area_pt2'] += paint['visible_area_pt2']
        group['ink_area_pt2'] += paint['ink_area_pt2']
        group['ink_inside_visible_area_pt2'] += paint.get('ink_inside_visible_area_pt2',paint['ink_area_pt2'])
        group['excess_ink_area_pt2'] += paint['outside_visible_area_pt2']
        group['paths'] += paint['paths']
        if paint['visible_area_pt2'] > 1e-9:
            group['visible_paints'] += 1
            if not paint['paths']:
                group['paints_without_paths'] += 1
                missing.append(dict(id=paint['id'], representation=paint['representation'],
                                    visible_area_pt2=paint['visible_area_pt2'], source_color=paint['source_color']))
        color = colors.setdefault(tuple(paint['source_color']), dict(
            source_rgb=paint['source_color'], pen_rgb=paint['pen_color'],
            visible_area_pt2=0, ink_area_pt2=0, paths=0))
        color['visible_area_pt2'] += paint['visible_area_pt2']
        color['ink_area_pt2'] += paint['ink_area_pt2']
        color['paths'] += paint['paths']
    for group in groups.values():
        group['ink_fraction'] = group['ink_inside_visible_area_pt2'] / group['visible_area_pt2'] if group['visible_area_pt2'] else None
    palette = [dict(pen=i+1, rgb=color,
                    paths=sum(p['paths'] for p in report['decoded_hpgl'] if p['pen_color'] == color))
               for i, color in enumerate(report['palette'])]
    summary = dict(settings=report['settings'], source_nonwhite_colors=len(colors), pens=palette,
                   representation=groups, palette_mapping=list(colors.values()),
                   source_paint_events=len(report['decoded_hpgl']),
                   paths_lost_to_coordinate_rounding=sum(p['paths'] for p in report['planned'])-report['metrics']['paths'],
                   metrics=report['metrics'], timing=report['timing'],
                   routing=report['routing'],
                   travel=travel, width_policy=width_policy,
                   completely_unrepresented_paints=len(missing),
                   completely_unrepresented_area_pt2=sum(p['visible_area_pt2'] for p in missing),
                   note='Missing paint fragments may be thinner than the pen plus numerical/coordinate-rounding clearance. Intentional hatch gaps are measured separately. Area coverage does not guarantee contrast: different source colors sharing a pen can lose distinctions.')
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (directory / 'unrepresented-paints.json').write_text(json.dumps(sorted(missing, key=lambda p: -p['visible_area_pt2']), indent=2)+'\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    print(json.dumps(summarize(parser.parse_args().directory), indent=2))
