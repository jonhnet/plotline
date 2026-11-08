"""Paper sizes and offline HP7585 margin snapshots, independent of orientation.

Sizes: https://support.hp.com/us-en/document/bpp01676
Margin conventions match Plotline Studios's controller/src/media.rs. Live advertised
profiles take precedence in the UI; nominal fallbacks never claim calibration.
"""
import copy
import json

from . import ROOT


def paper_sizes():
    inch = [
        ('letter', 'Letter / ANSI A', 8.5, 11),
        ('legal', 'Legal', 8.5, 14),
        ('tabloid', 'Tabloid / ANSI B', 11, 17),
        ('ansi-c', 'ANSI C', 17, 22),
        ('ansi-d', 'ANSI D', 22, 34),
        ('ansi-e', 'ANSI E', 34, 44),
        ('arch-a', 'ARCH A', 9, 12),
        ('arch-b', 'ARCH B', 12, 18),
        ('arch-c', 'ARCH C', 18, 24),
        ('arch-d', 'ARCH D', 24, 36),
        ('arch-e1', 'ARCH E1', 30, 42),
        ('arch-e', 'ARCH E', 36, 48),
    ]
    result = [dict(id=key, label=f'{name} · {w:g} × {h:g} in',
                   group='ARCH' if key.startswith('arch-') else 'US / ANSI',
                   size_mm=[round(w*25.4, 3), round(h*25.4, 3)])
              for key, name, w, h in inch]
    result += [dict(id=key.lower(), label=f'{key} · {w} × {h} mm', group='ISO', size_mm=[w, h])
               for key, w, h in [('A4',210,297),('A3',297,420),('A2',420,594),('A1',594,841),('A0',841,1189)]]
    return result


def orient_profile(media, orientation):
    """Turn a complete snapshot, including calibration, without changing scale."""
    result = copy.deepcopy(media)
    w, h = result['sheet_size_mm']
    base = result.get('orientation_deg', 0 if w >= h else 90)
    if orientation not in (base, (base+180)%360):
        raise ValueError('Orientation must match the sheet dimensions')
    result['orientation_deg'] = orientation
    if orientation != base:
        x, y, rw, rh = result['printable_rect_mm']
        result['printable_rect_mm'] = [round(w-x-rw,6),round(h-y-rh,6),rw,rh]
        result['id'] = result['id'][:56]+f'-r{orientation}'
        if 'calibration' in result:
            a,b,c,d,e,f = result['calibration']['sheet_to_device_units']
            result['calibration']['sheet_to_device_units'] = [-a,-b,-c,-d,a*w+c*h+e,b*w+d*h+f]
    return result


def plotter_profile(size, orientation=None):
    base = _plotter_profile(size)
    return orient_profile(base, orientation) if orientation is not None else base


def _plotter_profile(size):
    w, h = size
    if size == [431.8, 279.4]:
        return json.loads((ROOT/'contracts/v1/examples/hp7585-tabloid-landscape.json').read_text())
    sides = sorted(size)
    if sides == [215.9, 279.4]:
        back, front, left, right = 39, 15, 15, 15
    elif sides == [279.4, 431.8]:
        back, front, left, right = 39.425, 15.425, 14.8, 11
    else:
        back, front, left, right = 39, 15, 14.8, 11
    l, t, r, b = (back, right, front, left) if w >= h else (left, back, right, front)
    return dict(schema='plotline.media-profile/1', id=f'hp7585-{round(w*100)}-{round(h*100)}', revision=1,
                label='HP7585 · '+('recorded margins' if sides in ([215.9,279.4],[279.4,431.8]) else 'nominal margins'),
                sheet_size_mm=copy.deepcopy(size), printable_rect_mm=[l,t,round(w-l-r,6),round(h-t-b,6)],
                content_inset_mm=1)


def plotter_profiles():
    return [plotter_profile(size, angle) for paper in paper_sizes()
            for angle in (0,90,180,270)
            for size in [paper['size_mm'] if angle%180 else list(reversed(paper['size_mm']))]]
