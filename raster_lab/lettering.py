"""Connected-ink experiments for small marks. No OCR or semantic recognition."""
import cv2
import numpy as np
from PIL import Image

from . import engine


def component_mask(gray, drawing, recipe):
    return gray < recipe['threshold']


def analysis_gray(image, recipe):
    rgb = engine.analysis_image(image, recipe['analysis_long_edge_px'])
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    scale = recipe.get('lettering_upsample', 1)
    if scale not in (1, 2):
        raise ValueError('Lettering interpolation is either native or 2x cubic')
    if scale == 2:
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return gray


def narrow_skeleton(mask, drawing, recipe):
    distance = cv2.distanceTransform(np.pad(mask.astype(np.uint8), 1), cv2.DIST_L2,
                                     cv2.DIST_MASK_PRECISE)[1:-1, 1:-1]
    mm_px = .5 * (drawing.width_mm / mask.shape[1] + drawing.height_mm / mask.shape[0])
    algorithm = recipe.get('lettering_thinning', 'zhang-suen')
    if algorithm == 'medial-axis':
        from skimage.morphology import medial_axis
        thin = medial_axis(np.pad(mask, 1), rng=recipe.get('medial_seed', 0))[1:-1, 1:-1]
        # Equal-distance tie ordering can leave a one-pixel cavity in solid
        # ink at a junction. Restore only cavities containing NO source white;
        # genuine letter counters must survive. Padding identifies the outside
        # even when source ink touches every image edge. Tracing then suppresses
        # the diagonal diamond shortcuts around the restored junction pixel.
        count, background = cv2.connectedComponents((~np.pad(thin, 1)).astype(np.uint8), connectivity=4)
        invented = np.ones(count, bool)
        invented[0] = False
        invented[np.unique(background[~np.pad(mask, 1)])] = False
        thin |= invented[background[1:-1, 1:-1]]
    elif algorithm in ('zhang-suen', 'guo-hall'):
        method = cv2.ximgproc.THINNING_ZHANGSUEN if algorithm == 'zhang-suen' else cv2.ximgproc.THINNING_GUOHALL
        thin = cv2.ximgproc.thinning(np.pad(mask.astype(np.uint8) * 255, 1), thinningType=method)[1:-1, 1:-1] > 0
    else:
        raise ValueError('Unknown lettering thinning method: ' + algorithm)
    # Zhang-Suen thinning can erase a tiny component entirely (e.g. a 2x2 dot).
    # Restore one pixel of any vanished component before applying the width veto.
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    survived = np.zeros(count, bool)
    survived[np.unique(labels[thin])] = True
    survived[0] = True  # Background is not a mark.
    for label in np.flatnonzero(~survived):
        x, y, w, h, _ = stats[label]
        ys, xs = np.nonzero(labels[y:y+h, x:x+w] == label)
        nearest = np.argmin((xs + x - centers[label, 0]) ** 2 + (ys + y - centers[label, 1]) ** 2)
        thin[y + ys[nearest], x + xs[nearest]] = True
    # Veto wide interiors AFTER thinning. Do not turn a solid fill into an
    # artificial hollow band, or discard thin letters connected to a broad mark.
    return thin & (distance * mm_px <= recipe['maximum_component_radius_mm'])


def topology(mask):
    foreground = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)[0] - 1
    background = cv2.connectedComponents((~np.pad(mask, 1)).astype(np.uint8), connectivity=4)[0]
    return dict(components=int(foreground), holes=int(background - 2))


def component_paths(mask, drawing, already_thin=False):
    h, w = mask.shape
    paths = []
    for run in engine.skeleton_paths(mask, include_points=True, already_thin=already_thin):
        points = (np.asarray(run, np.float64) + .5) * [drawing.width_mm / w, drawing.height_mm / h]
        if len(points) == 1:
            # Preserve punctuation and other isolated marks. A move-only SVG
            # path paints nothing, so use a tiny round-capped physical segment.
            half = max(.025, drawing.pen_mm * .1) / 2
            x, y = points[0]
            paths.append([[x - half, y], [x + half, y]])
            continue
        closed = run[0] == run[-1]
        simple = cv2.approxPolyDP(points.astype(np.float32), 0, closed).reshape(-1, 2)
        if closed and len(simple) > 1:
            simple = np.vstack((simple, simple[0]))
        paths.append(simple.tolist())
    # Keep short junction arms as well as dots; length is not semantic importance.
    return engine.finish_paths(paths, drawing, 1e-6)


def ink_fill_paths(mask, drawing):
    """Dense physical strokes inside the binary ink's pixel-cell rectangles.

    This is a shape/weight control, not centerline recovery. Require the entire
    pen bounding square to lie in ink, conservatively preserving white holes.
    Pixel-row centers retain very thin horizontal marks that a regular hatch
    grid could miss. There is no skeleton, component-size veto or OCR here.
    """
    h, w = mask.shape
    dx, dy = drawing.width_mm / w, drawing.height_mm / h
    r = drawing.pen_mm / 2 + .001
    ys = np.unique(np.round(np.r_[np.arange(r, drawing.height_mm-r, drawing.pen_mm*.8),
                                  (np.arange(h)+.5)*dy], 3))
    paths = []
    for y in ys:
        if not r <= y <= drawing.height_mm-r:
            continue
        lo = max(0, int(np.floor((y-r)/dy)))
        hi = min(h-1, int(np.floor((y+r)/dy)))
        support = np.all(mask[lo:hi+1], axis=0)
        changes = np.diff(np.r_[False, support, False].astype(np.int8))
        for start, end in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]):
            left, right = start*dx+r, end*dx-r
            if right > left:
                paths.append([[left,y],[right,y]])
    return engine.finish_paths(paths, drawing, 1e-6)


def draw(image, recipe):
    long_mm = recipe['long_edge_in'] * 25.4
    drawing = engine.Drawing(long_mm * image.width / max(image.size),
                             long_mm * image.height / max(image.size), recipe['pen_mm'], {})
    gray = analysis_gray(image, recipe)
    mask = component_mask(gray, drawing, recipe)
    if recipe.get('lettering_representation') == 'ink-fill':
        drawing.paths['K'] = ink_fill_paths(mask, drawing)
        if recipe.get('join_fill', False):
            from .fill_join import join
            drawing.paths['K'] = join(drawing.paths['K'], drawing.pen_mm, recipe['join_max_extra_mm'])
    else:
        drawing.paths['K'] = component_paths(narrow_skeleton(mask, drawing, recipe), drawing,
                                            already_thin=recipe.get('trace_skeleton_directly', False))
        if recipe.get('fit_tolerance_mm', 0):
            from .ink_fit import fit
            drawing.paths['K'] = fit(drawing.paths['K'], mask, drawing, recipe)
    return drawing


def diagnostics(image, drawing, recipe, destination):
    """Inspectable intermediate pixels, not an OCR/readability score."""
    gray = analysis_gray(image, recipe)
    mask = component_mask(gray, drawing, recipe)
    skeleton = None if recipe.get('lettering_representation') == 'ink-fill' else narrow_skeleton(mask, drawing, recipe)
    stages = [('threshold-mask', mask)]
    if skeleton is not None:
        stages.append(('accepted-skeleton', skeleton))
    for name, pixels in stages:
        Image.fromarray(np.where(pixels, 0, 255).astype(np.uint8)).save(destination / (name + '.png'))
    h, w = mask.shape
    regions = {'whole_image': (slice(None), slice(None)),
               'upper_right_quadrant': (slice(0, h // 2), slice(w // 2, w))}
    counts = {}
    # Count isolated pixels before tracing; they now become physical dot strokes.
    neighbors = None if skeleton is None else cv2.filter2D(skeleton.astype(np.float32), -1, np.ones((3, 3), np.float32),
                                                          borderType=cv2.BORDER_CONSTANT) - skeleton
    for name, region in regions.items():
        counts[name] = dict(threshold_topology=topology(mask[region]))
        if skeleton is not None:
            counts[name].update(skeleton_topology=topology(skeleton[region]),
                                isolated_skeleton_pixels=int(np.sum((skeleton & (neighbors == 0))[region])))
    joining = {}
    if recipe.get('join_fill', False):
        from .fill_join import metrics
        joining['fill_joining'] = metrics(ink_fill_paths(mask, drawing), drawing.paths['K'])
    return dict(regions=counts, tracing_pixels=list(gray.shape[::-1]), **joining,
                skeleton_used=skeleton is not None,
                skeleton_role='before path fitting' if recipe.get('fit_tolerance_mm', 0) else 'centerline tracing',
                thinning_method=recipe.get('lettering_thinning', 'zhang-suen') if skeleton is not None else None,
                interpolation='2x cubic; no added source information' if recipe.get('lettering_upsample', 1) == 2 else 'native pixels',
                interpretation='Pixel components/holes are not glyph counts or readability scores; quadrant boundaries can split marks.')
