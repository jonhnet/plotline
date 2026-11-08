"""Contrast-local ink allocation and narrow dark-line recovery.

Local, model-free experiments. A color boundary attracts ink on both sides;
the original local color determines which inks to draw there. Dark ridges are
tested separately from step edges, so a thick ink band has one centerline.
"""
import cv2
import numpy as np
from collections import Counter


def sample(field, x, y):
    return cv2.remap(field, x.astype(np.float32), y.astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def color_edges(rgb, drawing, recipe):
    """Thin contrast seeds in float CIELAB, including equal-lightness hue edges."""
    h, w = rgb.shape[:2]
    px_mm = .5 * (w / drawing.width_mm + h / drawing.height_mm)
    sigma = max(.6, recipe['edge_smooth_mm'] * px_mm)
    lab = cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2Lab)
    lab = cv2.GaussianBlur(lab, (0, 0), sigma)
    dx = cv2.Sobel(lab, cv2.CV_32F, 1, 0, ksize=3, scale=1/8)
    dy = cv2.Sobel(lab, cv2.CV_32F, 0, 1, ksize=3, scale=1/8)
    xx, yy, xy = (dx * dx).sum(2), (dy * dy).sum(2), (dx * dy).sum(2)
    angle = .5 * np.arctan2(2 * xy, xx - yy)
    nx, ny = np.cos(angle), np.sin(angle)
    magnitude = np.sqrt(np.maximum(0, .5 * (xx + yy + np.hypot(xx - yy, 2 * xy))))
    y, x = np.mgrid[:h, :w].astype(np.float32)
    peak = ((magnitude >= sample(magnitude, x + nx, y + ny)) &
            (magnitude > sample(magnitude, x - nx, y - ny)))
    reach = max(1., recipe['edge_probe_mm'] * px_mm)
    contrast = np.linalg.norm(sample(lab, x + reach * nx, y + reach * ny) -
                              sample(lab, x - reach * nx, y - reach * ny), axis=2)
    low, high = recipe['edge_delta_e_low'], recipe['edge_delta_e_high']
    strength = np.clip((contrast - low) / (high - low), 0, 1)
    # No artificial contrast against an assumed white border outside the image.
    return np.where(peak & (magnitude > .1), strength, 0).astype(np.float32)


def ink_focus(rgb, drawing, recipe):
    """Maximum of contrast-weighted, compact cosine falloffs in page mm.

    Quantized strength levels keep strong distant edges from being hidden by a
    nearer weak edge. Distance transforms avoid an all-pixels × all-edges loop.
    """
    radius = recipe['edge_fade_mm']
    if radius <= 0:
        return np.ones(rgb.shape[:2], np.float32)
    seeds = color_edges(rgb, drawing, recipe)
    mm_px = .5 * (drawing.width_mm / rgb.shape[1] + drawing.height_mm / rgb.shape[0])
    focus = np.zeros(seeds.shape, np.float32)
    for level in np.arange(1, 9) / 8:
        selected = seeds >= level
        if not selected.any():
            continue
        distance = cv2.distanceTransform((~selected).astype(np.uint8), cv2.DIST_L2,
                                         cv2.DIST_MASK_PRECISE) * mm_px
        weight = .5 + .5 * np.cos(np.pi * np.minimum(distance / radius, 1))
        np.maximum(focus, level * weight, out=focus)
    return focus


def ridge_paths(gray, drawing, recipe):
    """Scale-limited dark valleys, with brighter samples on BOTH sides.

    Hessian normals and a local Taylor extremum locate subpixel centers. This
    borrows the differential ridge idea, not Steger's full bias/width recovery.
    A one-sided step or a broad flat fill is deliberately not an ink stroke.
    """
    from .engine import finish_paths, skeleton_paths
    gray = gray.astype(np.float32) / 255
    h, w = gray.shape
    px_mm = .5 * (w / drawing.width_mm + h / drawing.height_mm)
    y, x = np.mgrid[:h, :w].astype(np.float32)
    best = np.zeros(gray.shape, np.float32)
    seeds = np.zeros(gray.shape, bool)
    offset_x, offset_y = np.zeros_like(best), np.zeros_like(best)
    best_scale = np.zeros_like(best)
    for scale in recipe['ridge_scales_mm']:
        sigma = max(recipe.get('ridge_min_sigma_px', .65), scale * px_mm)
        smooth = cv2.GaussianBlur(gray, (0, 0), sigma)
        dx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, scale=1/8)
        dy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, scale=1/8)
        xx = cv2.Sobel(smooth, cv2.CV_32F, 2, 0, scale=1/4)
        yy = cv2.Sobel(smooth, cv2.CV_32F, 0, 2, scale=1/4)
        xy = cv2.Sobel(smooth, cv2.CV_32F, 1, 1, scale=1/4)
        disc = np.hypot(xx - yy, 2 * xy)
        normal, tangent = .5 * (xx + yy + disc), .5 * (xx + yy - disc)
        angle = .5 * np.arctan2(2 * xy, xx - yy)
        nx, ny = np.cos(angle), np.sin(angle)
        offset = -(dx * nx + dy * ny) / np.maximum(normal, 1e-8)
        ox, oy = offset * nx, offset * ny
        # Evaluate contrast on a lightly smoothed source, rather than allowing
        # a large Gaussian to wash the dark center into its surroundings.
        probe = cv2.GaussianBlur(gray, (0, 0), .6)
        center = sample(probe, x + ox, y + oy)
        reach = 2.5 * sigma
        sides = np.minimum(sample(probe, x + ox + reach * nx, y + oy + reach * ny),
                           sample(probe, x + ox - reach * nx, y + oy - reach * ny))
        accepted = ((normal * sigma ** 2 > recipe.get('ridge_response', .012)) &
                    (np.abs(tangent) < .75 * normal) &
                    (np.abs(ox) <= .6) & (np.abs(oy) <= .6) &
                    (center < recipe['ridge_dark_limit']) &
                    (sides - center > recipe['ridge_contrast']))
        score = np.where(accepted, normal * sigma ** 2, 0)
        if 'ridge_seed_contrast' in recipe:
            seeds |= accepted & (sides - center > recipe['ridge_seed_contrast'])
        better = score > best
        best[better], offset_x[better], offset_y[better] = score[better], ox[better], oy[better]
        best_scale[better] = sigma / px_mm
    paths = []
    mask = best > 0
    if 'ridge_seed_contrast' in recipe:
        # Hysteresis keeps weaker continuations connected to convincing marks;
        # it does not draw a guessed connector across an empty gap.
        _, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
        keep = np.unique(labels[seeds])
        keep = keep[keep != 0]
        mask = np.isin(labels, keep)
    runs = skeleton_paths(mask)
    endpoints = Counter(point for run in runs for point in (run[0], run[-1]))
    for run in runs:
        points = np.asarray(run, np.int32)
        ix, iy = points[:, 0], points[:, 1]
        physical = np.column_stack(((ix + .5 + offset_x[iy, ix]) * drawing.width_mm / w,
                                    (iy + .5 + offset_y[iy, ix]) * drawing.height_mm / h))
        if recipe.get('ridge_min_aspect'):
            length = np.linalg.norm(np.diff(physical, axis=0), axis=1).sum()
            # A tiny diagonal in a filled corner is short relative to the scale
            # that "found" it. A fine eye stroke can be short in absolute units
            # yet long relative to its width. Keep interior junction connectors.
            interior = endpoints[run[0]] > 1 and endpoints[run[-1]] > 1
            if not interior and length < recipe['ridge_min_aspect'] * np.median(best_scale[iy, ix]):
                continue
        closed = run[0] == run[-1]
        simple = cv2.approxPolyDP(physical.astype(np.float32), recipe.get('ridge_simplify_mm', .025), closed).reshape(-1, 2)
        # OpenCV returns a closed contour without repeating its first vertex;
        # our stroke format needs an explicit final segment back to the start.
        if closed and len(simple) > 1:
            simple = np.vstack((simple, simple[0]))
        paths.append(simple.tolist())
    return finish_paths(paths, drawing, recipe['min_length_mm'])


def draw(image, recipe):
    from .engine import Drawing, analysis_image, stroke_mask
    from .waves import wave_paths
    rgb = analysis_image(image)
    long_mm = recipe['long_edge_in'] * 25.4
    drawing = Drawing(long_mm * image.width / max(image.size),
                      long_mm * image.height / max(image.size), recipe['pen_mm'], {})
    if recipe.get('recover_ink'):
        drawing.paths['K'] = ridge_paths(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), drawing, recipe)
    if recipe['method'] == 'ridges':
        return drawing
    focus = ink_focus(rgb, drawing, recipe)
    # Subtract only the actual recovered K footprint, not the whole dark region.
    footprint = (stroke_mask(drawing, 'K', rgb.shape[1], rgb.shape[0], 2).astype(np.float32) / 255
                 if drawing.paths.get('K') else np.zeros(rgb.shape[:2], np.float32))
    for i, (ink, angle) in enumerate(zip('CMY', [0, 60, 120])):
        channel = 1 - rgb[:, :, i].astype(np.float32) / 255
        channel = np.maximum(0, channel - footprint)
        drawing.paths[ink] = wave_paths(channel, drawing, angle, recipe, attention=focus)
    return drawing
