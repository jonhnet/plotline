"""Fit artwork before path generation; place it on a physical sheet afterward."""
from dataclasses import replace
import math


def layout(image_size, paper):
    iw, ih = image_size
    pw, ph = paper['width_in'], paper['height_in']
    margin = paper['margin_in']
    if not all(math.isfinite(v) and v > 0 for v in (iw, ih, pw, ph)):
        raise ValueError('Image and paper dimensions must be positive and finite')
    if not math.isfinite(margin) or not 0 <= 2 * margin < min(pw, ph):
        raise ValueError('Margins must leave a positive printable area')
    orientation = paper.get('orientation', 'auto')
    if orientation not in ('auto', 'fixed'):
        raise ValueError('Paper orientation must be auto or fixed')
    choices = [(pw, ph)] if orientation == 'fixed' else [(pw, ph), (ph, pw)]
    pw, ph = max(choices, key=lambda size: min((size[0]-2*margin)/iw, (size[1]-2*margin)/ih))
    scale = min((pw-2*margin)/iw, (ph-2*margin)/ih)
    aw, ah = iw*scale, ih*scale
    x, y = (pw-aw)/2, (ph-ah)/2
    return dict(width_in=pw, height_in=ph, margin_in=margin,
                art_width_in=aw, art_height_in=ah,
                offset_x_in=x, offset_y_in=y,
                actual_margins_in=dict(left=x, right=x, top=y, bottom=y))


def resolve(image_size, recipe):
    if 'paper' not in recipe:
        return recipe
    fitted = layout(image_size, recipe['paper'])
    return dict(recipe, long_edge_in=max(fitted['art_width_in'], fitted['art_height_in']))


def place(drawing, fitted):
    """Translate already generated physical paths, without scaling pen or geometry."""
    if not (math.isclose(drawing.width_mm/25.4, fitted['art_width_in'], abs_tol=1e-9)
            and math.isclose(drawing.height_mm/25.4, fitted['art_height_in'], abs_tol=1e-9)):
        raise ValueError('Generate paths at the fitted art size before placing them')
    x, y = fitted['offset_x_in']*25.4, fitted['offset_y_in']*25.4
    paths = {ink: [[[px+x, py+y] for px, py in path] for path in runs]
             for ink, runs in drawing.paths.items()}
    return replace(drawing, width_mm=fitted['width_in']*25.4,
                   height_mm=fitted['height_in']*25.4, paths=paths)
