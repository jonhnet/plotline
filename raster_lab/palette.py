"""Arbitrary-ink separation for the lab's illustrative multiply preview.

No measured spectra or scanner round trip: approximate independent hatch
coverage with prod(1 - coverage * (1 - swatch)). Fit in Oklab, using a small
palette-specific RGB lookup table rather than optimizing every image pixel.
This intentionally models the existing sRGB preview, not physical reflectance.
"""
from functools import lru_cache
import time

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.optimize import minimize
from skimage.color import rgb2lab
from .waves import screen_recipe

VERSION = 'swatch-palette-1'


@lru_cache(maxsize=16)
def _tone_curve(pen, settings):
    from . import engine
    from .waves import wave_paths
    recipe = dict(settings)
    demands = np.linspace(0, 1, 33)
    coverage = []
    # A software swatch: measure the generated finite pen footprints, including
    # self-overlap and highlight gaps. No physical measurements are implied.
    for value in demands:
        drawing = engine.Drawing(pen*100, pen*100, pen, {})
        drawing.paths['K'] = wave_paths(np.full((96, 96), value, np.float32),
                                        drawing, 0, recipe)
        mask = engine.stroke_mask(drawing, 'K', 600, 600, 2)
        coverage.append(float(mask[96:-96, 96:-96].mean()/255))
    return demands, np.maximum.accumulate(coverage)


def tone_curve(recipe, pen):
    keys = ('row_spacing_mm', 'amplitude_mm', 'min_frequency_cpm',
            'max_frequency_cpm', 'density', 'modulation', 'shape', 'smooth_mm',
            'bend_mm', 'white_cutoff', 'screen_carrier', 'highlight_run_mm',
            'highlight_seed', 'min_length_mm')
    return _tone_curve(pen, tuple((k, recipe[k]) for k in keys if k in recipe))


def colors_rgb(colors):
    from .engine import Drawing
    d = Drawing(1, 1, .25, {}, ink_colors=dict(colors))
    return np.array([[int(d.ink_color(ink)[i:i+2], 16)/255 for i in (1, 3, 5)]
                     for ink, _ in colors], dtype=np.float64)


def predict(coverage, colors):
    """Expected coarse sRGB preview under independent area coverages."""
    return np.prod(1-np.asarray(coverage)[..., :, None]*(1-colors), axis=-2)


def _lab(rgb):
    return rgb2lab(np.asarray(rgb).reshape(-1, 1, 3)).reshape(-1, 3)/100


def _oklab(rgb):
    # Björn Ottosson's public-domain transform (2021 matrices):
    # https://bottosson.github.io/posts/oklab/
    # Unlike CIELAB, its blue-to-white trajectories remain perceptually blue.
    rgb = np.asarray(rgb).reshape(-1, 3)
    linear = np.where(rgb <= .04045, rgb/12.92,
                      ((np.maximum(rgb, 0)+.055)/1.055)**2.4)
    lms = linear @ np.array([[.4122214708,.5363325363,.0514459929],
                            [.2119034982,.6806995451,.1073969566],
                            [.0883024619,.2817188376,.6299787005]]).T
    return np.cbrt(lms) @ np.array([[.2104542553,.7936177850,-.0040720468],
                                   [1.9779984951,-2.4285922050,.4505937099],
                                   [.0259040371,.7827717662,-.8086757660]]).T


@lru_cache(maxsize=16)
def lookup(colors, widths, max_coverage=.7, grid=17, color_space='cielab'):
    """Bounded, batched solve. All arguments are frozen/cache-keyed materials."""
    if not 1 <= len(colors) <= 8 or len(widths) != len(colors):
        raise ValueError('Select between one and eight inks with known widths')
    if not 0 < max_coverage <= 1 or grid < 3 or grid > 65:
        raise ValueError('Invalid palette coverage or lookup resolution')
    if color_space not in ('cielab', 'oklab'):
        raise ValueError('Unknown palette color space')
    # Keep the original lab experiment reproducible; Studio selects Oklab.
    transform = _oklab if color_space == 'oklab' else _lab
    widths = np.asarray(widths, dtype=float)
    if not np.all(np.isfinite(widths)) or np.any(widths <= 0):
        raise ValueError('Palette pen widths must be positive and finite')
    started = time.perf_counter()
    inks = colors_rgb(colors)
    targets = np.stack(np.meshgrid(*([np.linspace(0, 1, grid)]*3), indexing='ij'), -1)
    target_lab = transform(targets)
    count, n = grid**3, len(inks)
    # Coverage / diameter estimates distance per unit area. A small penalty
    # breaks near-ties in favor of less travel, without a UI tuning parameter.
    cost = .00015 * (.25 / widths)
    absorption = 1-inks

    def objective(flat):
        c = flat.reshape(count, n)
        factors = np.maximum(1-c[:, :, None]*absorption, 1e-7)
        rgb = np.prod(factors, axis=1)
        error = transform(rgb)-target_lab
        loss = np.sum(error*error) + np.sum(c*cost)
        # Differentiate only the three-channel color transform numerically;
        # coverage derivatives remain analytic and batched across the table.
        d_rgb = np.empty_like(rgb)
        step = 1e-5
        for channel in range(3):
            offset = np.zeros(3); offset[channel] = step
            derivative = (transform(rgb+offset)-transform(rgb-offset))/(2*step)
            d_rgb[:, channel] = 2*np.sum(error*derivative, axis=1)
        gradient = -np.sum(d_rgb[:, None, :]*rgb[:, None, :]*absorption/factors, axis=2)+cost
        # Adjacent source colors should not jump between unrelated mixtures.
        volume = c.reshape(grid, grid, grid, n)
        gvolume = gradient.reshape(volume.shape)
        for axis in range(3):
            lo = [slice(None)]*4; hi = lo.copy()
            lo[axis], hi[axis] = slice(None, -1), slice(1, None)
            difference = volume[tuple(hi)]-volume[tuple(lo)]
            loss += .0002*np.sum(difference*difference)
            gvolume[tuple(hi)] += .0004*difference
            gvolume[tuple(lo)] -= .0004*difference
        return loss, gradient.ravel()

    initial = np.zeros((count, n))
    result = minimize(objective, initial.ravel(), jac=True, method='L-BFGS-B',
                      bounds=[(0, max_coverage)]*(count*n),
                      options=dict(maxiter=600 if color_space == 'oklab' else 400,
                                   ftol=1e-8, gtol=1e-6, maxcor=8))
    table = np.clip(result.x.reshape(grid, grid, grid, n), 0, max_coverage)
    table[-1, -1, -1] = 0  # Paper white must never request ink.
    report = dict(version=VERSION, colors=dict(colors), widths_mm=widths.tolist(),
                  grid=grid, max_coverage=max_coverage, iterations=int(result.nit),
                  converged=bool(result.success), seconds=time.perf_counter()-started,
                  color_space=color_space,
                  model='Uncalibrated independent coverage, sRGB multiply swatches; '+
                        ('Oklab' if color_space == 'oklab' else 'CIELAB')+' fit')
    table.setflags(write=False)
    return table, report


def separate(rgb, colors, widths, *, max_coverage=.7, grid=17, color_space='cielab'):
    """Return physical coverage fields in stable document-ink order."""
    colors = tuple(colors.items())
    table, report = lookup(colors, tuple(widths[k] for k, _ in colors), max_coverage, grid, color_space)
    points = np.clip(np.asarray(rgb, dtype=float)/255, 0, 1)*(grid-1)
    coordinates = np.moveaxis(points, -1, 0)
    return {ink: map_coordinates(table[..., i], coordinates, order=1, prefilter=False)
            for i, (ink, _) in enumerate(colors)}, dict(report)
