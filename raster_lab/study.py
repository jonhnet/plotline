"""Focused stroke/screen comparisons, independent of the archived recipes."""
import cv2
import numpy as np

from . import engine
from .focus import ink_focus, ridge_paths
from .waves import wave_paths


def round_preferences(preferences, number):
    # Historical preferred-profile columns must not change when new votes land.
    return preferences.get('rounds', {}).get(str(number), preferences)


def resolve_recovery(recipe, profile_id, profiles):
    """Snapshot the chosen recovery, removing old seed/threshold overrides."""
    resolved = dict(recipe)
    if not recipe.get('use_preferred_recovery'):
        return resolved
    profile = profiles[profile_id]
    for key in list(resolved):
        if key.startswith('ridge_'):
            del resolved[key]
    resolved.update({key: value for key, value in profile.items() if key.startswith('ridge_')})
    resolved['min_length_mm'] = profile['min_length_mm']
    resolved['analysis_long_edge_px'] = profile.get('analysis_long_edge_px', engine.ANALYSIS_LONG_EDGE)
    resolved['recovery_profile_id'] = profile_id
    resolved['recovery_profile_name'] = profile['name']
    if recipe.get('use_profile_pen'):
        if recipe['method'] == 'stroke_study':
            resolved['pen_mm'] = profile['pen_mm']
        else:
            resolved['black_pen_mm'] = profile['pen_mm']
    return resolved


def source_palette(rgb):
    """Conservative, inspectable pixel rule; minor JPEG chroma is not color art.

    More than 1% of pixels must span over 12/255 across RGB channels. This
    classifies the portfolio's three monochrome sources without recognition.
    """
    chroma = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2)
    return 'cmy' if float(np.mean(chroma > 12)) > .01 else 'black'


def emphasis(rgb, drawing, recipe):
    floor = recipe.get('interior_ink', 0)
    if not 0 <= floor <= 1:
        raise ValueError('Interior ink must be between zero and one')
    if floor == 1:
        return np.ones(rgb.shape[:2], np.float32)
    field = ink_focus(rgb, drawing, recipe)
    return floor + (1 - floor) * field


def draw(image, recipe):
    recovery_rgb = engine.analysis_image(image, recipe.get('analysis_long_edge_px', engine.ANALYSIS_LONG_EDGE))
    long_mm = recipe['long_edge_in'] * 25.4
    drawing = engine.Drawing(long_mm * image.width / max(image.size),
                             long_mm * image.height / max(image.size), recipe['pen_mm'], {})
    black_pen = recipe.get('black_pen_mm', drawing.pen_mm)
    ink_drawing = engine.Drawing(drawing.width_mm, drawing.height_mm, black_pen, {})
    gray = cv2.cvtColor(recovery_rgb, cv2.COLOR_RGB2GRAY)
    drawing.paths['K'] = ridge_paths(gray, ink_drawing, recipe)
    if black_pen != drawing.pen_mm:
        drawing.pen_mm_by_ink['K'] = black_pen
    if recipe['method'] == 'stroke_study':
        return drawing
    # Recovery can use native source detail while the CMY screen keeps the
    # comparison's fixed analysis scale. Changing K need not change color sampling.
    rgb = (engine.analysis_image(image, recipe['screen_analysis_long_edge_px'])
           if 'screen_analysis_long_edge_px' in recipe else recovery_rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    palette = source_palette(rgb) if recipe['palette'] == 'source' else recipe['palette']
    if palette == 'black':
        channels = {'K': 1 - gray.astype(np.float32) / 255}
        angles = {'K': 0}
    elif palette == 'cmy':
        channels = {ink: 1 - rgb[:, :, i].astype(np.float32) / 255 for i, ink in enumerate('CMY')}
        angles = dict(C=0, M=60, Y=120)
    else:
        raise ValueError('Unsupported study palette')
    field = emphasis(rgb, drawing, recipe)
    footprint = engine.stroke_mask(drawing, 'K', rgb.shape[1], rgb.shape[0], 2).astype(np.float32) / 255
    for ink, channel in channels.items():
        remaining = np.maximum(0, channel - footprint)
        pen_drawing = engine.Drawing(drawing.width_mm, drawing.height_mm, drawing.ink_width(ink), {})
        shading = wave_paths(remaining, pen_drawing, angles[ink], recipe, attention=field)
        # For monochrome, recovered strokes and shading share K. Never replace
        # the recovered lines when adding that channel's halftone paths.
        drawing.paths.setdefault(ink, []).extend(shading)
    return drawing
