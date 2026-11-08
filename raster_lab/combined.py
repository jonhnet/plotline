"""Combined raster treatments for review before PDF Studio integration.

Channel letters are separation roles. Their rendered colors come from a frozen
material snapshot; a turquoise C-role is not relabeled as a calibrated cyan.
"""
import cv2
import numpy as np
from PIL import Image

from . import engine, fill_join, lettering, paper, study
from .focus import ridge_paths
from .waves import screen_recipe, wave_paths

VERSION = 'combined-raster-1'


def ink_mask(image, recipe):
    gray = lettering.analysis_gray(image, recipe)
    mask = gray < recipe['threshold']
    policy = recipe['black_policy']
    if policy == 'neutral':
        rgb = engine.analysis_image(image, recipe['analysis_long_edge_px'])
        palette = recipe['palette']
        if palette == 'source':
            palette = study.source_palette(engine.analysis_image(image))
        if palette == 'black':
            # Do not let JPEG color fringes remove marks from a source already
            # classified (or explicitly requested) as monochrome.
            return mask
        if recipe.get('lettering_upsample', 1) == 2:
            rgb = cv2.resize(rgb, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        # A near-neutral dark region can reasonably be assigned to K. Saturated
        # dark colors stay available to the color screen. This is a color rule,
        # not recognition of ink strokes, faces, text or photographic objects.
        spread = rgb.max(2).astype(np.int16)-rgb.min(2).astype(np.int16)
        mask &= (rgb.max(2) < recipe['threshold']) & (spread <= recipe['neutral_span'])
    elif policy != 'luminance':
        raise ValueError('Unknown joined-ink mask policy: '+policy)
    return mask


def remaining_channels(rgb, footprint, palette):
    """Reuse the lab's geometric residual approximation, not spectral fitting."""
    if palette == 'black':
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)/255
        return {'K': np.maximum(0, 1-gray-footprint)}
    remaining = np.maximum(0, 1-rgb.astype(np.float32)/255-footprint[:, :, None])
    if palette == 'cmyk':
        black = remaining.min(2)
        remaining = (remaining-black[:, :, None])/np.maximum(1-black[:, :, None],1e-6)
    elif palette != 'cmy':
        raise ValueError('Screen palette must be black, cmy, cmyk or source')
    channels = {ink:remaining[:,:,i] for i,ink in enumerate('CMY')}
    if palette == 'cmyk':
        channels['K'] = black
    return channels


def generate(image, recipe, destination=None, *, size_mm=None, progress=None):
    recipe = paper.resolve(image.size, recipe)
    long_mm = recipe['long_edge_in']*25.4
    drawing = engine.Drawing(long_mm*image.width/max(image.size),
                             long_mm*image.height/max(image.size), recipe['pen_mm'], {},
                             recipe.get('pen_mm_by_ink', {}), recipe.get('ink_colors', {}))
    if size_mm is not None:
        drawing.width_mm, drawing.height_mm = size_mm
    report_progress = progress or (lambda phase: None)
    report_progress("Recovering black marks")
    for ink in drawing.ink_colors:
        drawing.ink_color(ink)  # Validate before expensive path generation.
    policy = recipe['black_policy']
    black_drawing = engine.Drawing(drawing.width_mm,drawing.height_mm,drawing.ink_width('K'),{})
    raw = mask = None
    if policy in ('luminance','neutral'):
        mask = ink_mask(image, recipe)
        raw = lettering.ink_fill_paths(mask, black_drawing)
        recovered = fill_join.join(raw,black_drawing.pen_mm,recipe['join_max_extra_mm'])
    elif policy == 'ridge':
        gray = cv2.cvtColor(engine.analysis_image(image,recipe['analysis_long_edge_px']),cv2.COLOR_RGB2GRAY)
        recovered = ridge_paths(gray,black_drawing,recipe)
    elif policy == 'off':
        recovered = []
    else:
        raise ValueError('Unknown black recovery policy: '+policy)
    drawing.paths['K'] = list(recovered)
    rgb = engine.analysis_image(image,recipe['screen_analysis_long_edge_px'])
    palette = study.source_palette(rgb) if recipe['palette']=='source' else recipe['palette']
    footprint = engine.stroke_mask(drawing,'K',rgb.shape[1],rgb.shape[0],2).astype(np.float32)/255
    screen_counts = {}
    separation_report = None
    if recipe.get('screening', True):
        field = study.emphasis(rgb,drawing,recipe)
        curves = {}
        if recipe.get('palette_mapping') == 'swatch':
            from . import palette as mapper
            colors = {ink:recipe['ink_colors'][ink] for ink in
                      recipe.get('screen_inks', recipe['ink_colors'])}
            report_progress('Measuring software hatch coverage')
            for ink in colors:
                pen = drawing.ink_width(ink)
                curves[ink] = mapper.tone_curve(mapper.screen_recipe(recipe, pen), pen)
            # All selected pens share the achievable coverage of the weakest
            # screen. This conservative bound avoids extrapolating darker tones.
            ceiling = min(curve[-1][-1] for curve in curves.values())
            target = 255-(255-rgb.astype(float))*field[:, :, None]
            if recovered:
                # Remove the recovered ink's contribution in the same multiply
                # model. Fully covered pixels cannot benefit from more ink.
                black = mapper.colors_rgb((('K', drawing.ink_color('K')),))[0]
                factor = 1-footprint[:, :, None]*(1-black)
                target = np.minimum(255, target/np.maximum(factor, 1e-6))
                target[footprint >= .999] = 255
            report_progress('Choosing pen mixtures')
            channels, separation_report = mapper.separate(target, colors,
                {ink:drawing.ink_width(ink) for ink in colors}, max_coverage=float(ceiling),
                color_space=recipe.get('palette_color_space', 'cielab'))
            separation_report['software_tone_curves'] = {
                k:dict(demand=v[0].tolist(),coverage=v[1].tolist()) for k,v in curves.items()}
            # Fullness is applied to the target above, so it is not applied again
            # to individual ink fields after the nonlinear color separation.
            field = None
        else:
            channels = remaining_channels(rgb,footprint,palette)
        for ink, channel in channels.items():
            report_progress("Screening " + ink)
            pen = drawing.ink_width(ink)
            # Keep the screen valid as physical pen diameters change. Scale its
            # row geometry with diameter; preserve the reviewed 0.30 mm ratios.
            physical_screen = screen_recipe(recipe, pen)
            pen_drawing = engine.Drawing(drawing.width_mm,drawing.height_mm,pen,{})
            angle = (recipe['ink_angles'][ink] if curves else
                     {'C':0,'M':60,'Y':120,'K':0 if palette=='black' else 45}[ink])
            if curves:
                demands, coverage = curves[ink]
                channel = np.interp(channel, coverage, demands).astype(np.float32)
            paths = wave_paths(channel,pen_drawing,angle,physical_screen,attention=field)
            drawing.paths.setdefault(ink,[]).extend(paths)
            screen_counts[ink] = len(paths)
    report = dict(version=VERSION,black_policy=policy,screen_palette=palette,
                  recovered_paths=len(recovered),
                  recovered_meters=sum(map(engine.path_length,recovered))/1000,
                  screen_paths_by_ink=screen_counts,
                  recovered_coverage=float(footprint.mean()),
                  color_model='Illustrative sRGB multiplication; no ink calibration or color-gamut compensation')
    if separation_report:
        report['separation'] = separation_report
        report['color_model'] = separation_report['model']
    if mask is not None:
        report['mask_fraction'] = float(mask.mean())
        report['fill_joining'] = fill_join.metrics(raw,recovered)
        if destination is not None:
            Image.fromarray(np.where(mask,0,255).astype(np.uint8)).save(destination/'ink-mask.png')
    return drawing, report
