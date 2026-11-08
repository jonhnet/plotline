#!/usr/bin/env python3
"""Advertise shared HP7585 margins and arbitrary custom PDF intake dimensions."""

import json
import math
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CUSTOM_MIN, CUSTOM_MAX = 100, 508000  # 1 mm to 200 inches; never shrink unsupported media to Letter.


def catalog():
    binary = Path(os.environ.get('PLOTLINE_BINARY') or ROOT/'controller/target/release/plotline-studios')
    return json.loads(subprocess.check_output([str(binary), '--media-catalog']))


def margins(profile):
    w, h = profile['sheet_size_mm']; x, y, rw, rh = profile['printable_rect_mm']
    return dict(left=math.ceil(x*100-1e-6), top=math.ceil(y*100-1e-6),
                right=math.ceil((w-x-rw)*100-1e-6), bottom=math.ceil((h-y-rh)*100-1e-6))


def size(width, height, tag='integer'):
    return f'{{ MEMBER {tag} x-dimension {width} MEMBER {tag} y-dimension {height} }}'


def media_col(item):
    name, profile = item['name'], item['profile']
    width, height = [round(x*100) for x in profile['sheet_size_mm']]
    return (f'{{ MEMBER keyword media-size-name {name} '
            f'MEMBER collection media-size {size(width, height)} '
            'MEMBER keyword media-source main MEMBER keyword media-type stationery '
            + ' '.join(f'MEMBER integer media-{edge}-margin {value}' for edge, value in margins(profile).items())
            + ' }')


def attributes():
    media = catalog()['ipp_media']
    sizes = [size(*[round(v*100) for v in m['profile']['sheet_size_mm']]) for m in media]
    sizes.append(size(f'{CUSTOM_MIN}-{CUSTOM_MAX}', f'{CUSTOM_MIN}-{CUSTOM_MAX}', 'rangeOfInteger'))
    names = [m['name'] for m in media] + ['custom_min_1x1mm', 'custom_max_200x200in']
    default = media_col(media[0])
    # Zero remains acceptable for an application's custom/full-page request.
    # Named sizes carry the actual/nominal plotter imageable rectangle.
    supported = {edge: sorted({0, *[margins(m['profile'])[edge] for m in media]})
                 for edge in ['bottom','left','right','top']}
    return '\n'.join([
        '# Generated from Plotline Studios shared paper profiles; completed means received for review.',
        'ATTR text printer-make-and-model "Plotline Studios HP7585"',
        'ATTR boolean color-supported true',
        'ATTR integer copies-default 1',
        'ATTR rangeOfInteger copies-supported 1-1',
        'ATTR enum finishings-default 3',
        'ATTR enum finishings-supported 3',
        f"ATTR keyword media-default {media[0]['name']}",
        'ATTR keyword media-supported '+','.join(names),
        'ATTR collection media-size-supported '+',\n'.join(sizes),
        'ATTR collection media-col-database '+',\n'.join(media_col(m) for m in media),
        f'ATTR collection media-col-default {default}',
        f'ATTR collection media-col-ready {default}',
        f"ATTR keyword media-ready {media[0]['name']}",
        *[f'ATTR integer media-{edge}-margin-supported '+','.join(map(str,values)) for edge,values in supported.items()],
        'ATTR keyword media-source-supported main',
        'ATTR keyword media-type-supported stationery',
        'ATTR enum orientation-requested-default 3',
        'ATTR enum orientation-requested-supported 3,4,5,6',
        'ATTR keyword print-color-mode-default color',
        'ATTR keyword print-color-mode-supported color',
        'ATTR keyword print-scaling-default none',
        'ATTR keyword print-scaling-supported none',
        'ATTR resolution printer-resolution-default 300dpi',
        'ATTR resolution printer-resolution-supported 300dpi',
        'ATTR enum print-quality-default 4',
        'ATTR enum print-quality-supported 4',
        'ATTR keyword sides-default one-sided',
        'ATTR keyword sides-supported one-sided',
        'ATTR integer pages-per-minute 1',
        'ATTR integer pages-per-minute-color 1',
        '',
    ])


if __name__ == '__main__':
    print(attributes(), end='')
