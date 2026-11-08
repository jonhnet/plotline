"""Continuous wave halftoning experiments; newly implemented from the idea.

Ahmed & Deussen (2016) motivate amplitude-to-arc-length mapping. FM and the
joint AM/FM variants below are experiments of our own, not paper replications.
The lookup models centerline length, not measured finite-width ink coverage.
"""
from functools import lru_cache
import math

import cv2
import numpy as np


def screen_recipe(recipe, pen):
    """Use the same diameter-scaled screen for coverage calibration and drawing.

    Spacing grows with diameter, so thinner pens draw more rows for the same
    area coverage. Frequency shrinks with diameter to preserve finite-pen
    clearance and the reviewed waveform proportions.
    """
    factor = pen/.3
    return dict(recipe, row_spacing_mm=1.2*factor, amplitude_mm=.42*factor,
                min_frequency_cpm=.08/factor, max_frequency_cpm=1.25/factor,
                highlight_run_mm=max(recipe['highlight_run_mm'], 2*pen),
                min_length_mm=min(recipe['min_length_mm'], pen))


def modulation(q, mode, amplitude, minimum_frequency, maximum_frequency):
    if mode == 'am':
        return amplitude * q, np.full_like(q, maximum_frequency)
    if mode == 'fm':
        return np.full_like(q, amplitude), minimum_frequency + q * (maximum_frequency - minimum_frequency)
    if mode == 'amfm':
        root = np.sqrt(q)
        return amplitude * root, minimum_frequency + root * (maximum_frequency - minimum_frequency)
    raise ValueError('Unknown wave modulation')


@lru_cache(maxsize=64)
def tone_lookup(mode, amplitude, minimum_frequency, maximum_frequency, shape):
    q = np.linspace(0, 1, 1025)
    a, f = modulation(q, mode, amplitude, minimum_frequency, maximum_frequency)
    if shape == 'triangle':
        ratio = np.sqrt(1 + (4 * a * f) ** 2)
    elif shape == 'sine':
        phase = np.linspace(0, 2 * np.pi, 512, endpoint=False)
        ratio = np.sqrt(1 + (2 * np.pi * a[:, None] * f[:, None] * np.cos(phase)) ** 2).mean(axis=1)
    else:
        raise ValueError('Unknown wave shape')
    return ratio, q


def integrate_phase(frequency, step, initial=0):
    """Integrate instantaneous frequency; f(x)*x would create phase jumps."""
    increments = .5 * (frequency[:-1] + frequency[1:]) * step * (2 * np.pi)
    return initial + np.r_[0., np.cumsum(increments)]


def distributed_screen(t, requested_ratio, row, angle, recipe):
    """Seeded stop/start intervals throughout the tonal range.

    Highlight duty encodes demand below a straight carrier's minimum tone.
    Even dark rows occasionally lift; additional arc length compensates where
    the finite-pen modulation limit allows it. Phase is never reset at a lift.
    """
    gap = recipe.get('screen_gap_fraction', .08)
    run_mm = recipe.get('screen_run_mm', 5.)
    if not 0 < gap < 1 or run_mm <= 0:
        raise ValueError('Distributed screen needs a positive run length and a gap fraction in (0, 1)')
    rng = np.random.default_rng(np.random.SeedSequence([
        recipe.get('screen_seed', 73), row % 2**32, round(angle * 100) % 2**32]))
    count = int(np.ceil((t[-1] - t[0] + 2 * run_mm) / (run_mm * .5))) + 2
    boundaries = t[0] - rng.uniform(0, run_mm) + np.cumsum(rng.uniform(.5, 1.5, count) * run_mm)
    thresholds = rng.random(count + 1)
    noise = thresholds[np.searchsorted(boundaries, t)]
    duty = np.minimum(requested_ratio, 1 - gap)
    arc_ratio = np.divide(requested_ratio, duty, out=np.ones_like(duty), where=duty > 0)
    return arc_ratio, noise < duty


def wave_paths(channel, drawing, angle, recipe, attention=None):
    from .engine import finish_paths
    spacing, amplitude = recipe['row_spacing_mm'], recipe['amplitude_mm']
    minimum_frequency, maximum_frequency = recipe['min_frequency_cpm'], recipe['max_frequency_cpm']
    if not 0 <= recipe['density'] <= 1:
        raise ValueError('Wave ink demand must be between zero and one')
    if recipe['density'] == 0:
        return []
    if spacing <= drawing.pen_mm or not 0 < amplitude <= (spacing - drawing.pen_mm) / 2:
        raise ValueError('Wave amplitude must fit between rows with pen clearance')
    if not 0 <= minimum_frequency <= maximum_frequency <= 1 / (2.5 * drawing.pen_mm):
        raise ValueError('Wave frequency exceeds the finite-pen sampling limit')
    # At least 20 samples per shortest cycle, also bounded by the pen diameter.
    step = min(drawing.pen_mm / 3, 1 / (20 * maximum_frequency))
    a = math.radians(angle)
    u, v = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
    corners = np.array([[0, 0], [drawing.width_mm, 0],
                        [0, drawing.height_mm], [drawing.width_mm, drawing.height_mm]])
    ts, ns = corners @ u, corners @ v
    t = np.arange(ts.min() - spacing, ts.max() + spacing, step)
    ratios, qs = tone_lookup(recipe['modulation'], amplitude, minimum_frequency,
                            maximum_frequency, recipe['shape'])
    # Sampling the band average suppresses variations too fine for a wave row.
    sigma_x = recipe['smooth_mm'] * channel.shape[1] / drawing.width_mm
    sigma_y = recipe['smooth_mm'] * channel.shape[0] / drawing.height_mm
    smooth = cv2.GaussianBlur(channel, (0, 0), sigmaX=sigma_x, sigmaY=sigma_y)
    paths = []
    for row in range(math.floor(ns.min() / spacing), math.ceil(ns.max() / spacing) + 1):
        # The same gentle bend applies to neighboring rows, preserving their
        # spacing. Staggered phases prevent a grid of synchronized wave peaks.
        bend = recipe.get('bend_mm', 0) * np.sin(2 * np.pi * t / 24)
        baseline = t[:, None] * u + (row * spacing + bend)[:, None] * v
        xs = (baseline[:, 0] / drawing.width_mm * channel.shape[1] - .5).astype(np.float32)[None, :]
        ys = (baseline[:, 1] / drawing.height_mm * channel.shape[0] - .5).astype(np.float32)[None, :]
        values = cv2.remap(smooth, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[0]
        if attention is not None:
            values *= cv2.remap(attention, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[0]
        # Approximate requested ink area by diameter × arc length. A straight
        # carrier is a nonzero floor; overlapping footprints limit the dark end.
        requested_ratio = values * recipe['density'] * spacing / drawing.pen_mm
        if recipe.get('screen_carrier') == 'distributed':
            arc_ratio, carrier_gate = distributed_screen(t, requested_ratio, row, angle, recipe)
        else:
            arc_ratio, carrier_gate = requested_ratio, None
        q = np.interp(arc_ratio, ratios, qs)
        amps, frequencies = modulation(q, recipe['modulation'], amplitude,
                                      minimum_frequency, maximum_frequency)
        phase = integrate_phase(frequencies, step, row * 2.399963229728653)
        if recipe['shape'] == 'sine':
            displacement = amps * np.sin(phase)
        else:
            displacement = amps * (2 / np.pi) * np.arcsin(np.sin(phase))
        points = baseline + displacement[:, None] * v
        # Lift in near-white regions. This makes the highlight/continuity
        # compromise explicit instead of drawing a carrier through white paper.
        active = values > recipe['white_cutoff']
        if recipe.get('screen_carrier'):
            # Below the straight-line coverage floor, progressively retain fewer
            # rows. A fixed low-discrepancy threshold per row preserves long runs
            # rather than introducing a high-frequency dotted carrier.
            if carrier_gate is not None:
                active &= carrier_gate
            elif recipe.get('screen_carrier') != 'highlight-diffusion':
                threshold = ((row + 1) * .618033988749895 + angle / 360) % 1
                active &= requested_ratio > max(.03, threshold)
            # A wave wanders away from its baseline: lift if its actual position
            # has no demand for this ink, or lies outside the edge's fade band.
            actual_x = (points[:, 0] / drawing.width_mm * channel.shape[1] - .5).astype(np.float32)[None, :]
            actual_y = (points[:, 1] / drawing.height_mm * channel.shape[0] - .5).astype(np.float32)[None, :]
            actual = cv2.remap(channel, actual_x, actual_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[0]
            if attention is not None:
                actual *= cv2.remap(attention, actual_x, actual_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[0]
            active &= actual > recipe['white_cutoff']
            if recipe.get('screen_carrier') == 'highlight-diffusion':
                from .highlights import highlight_screen
                active = highlight_screen(t, requested_ratio, active, row, angle, recipe, drawing.pen_mm)
        changes = np.diff(np.r_[False, active, False].astype(np.int8))
        for start, end in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]):
            if end - start < 2:
                continue
            simple = cv2.approxPolyDP(points[start:end].astype(np.float32), .02, False).reshape(-1, 2)
            if len(simple) > 1:
                paths.append(simple.tolist())
    return finish_paths(paths, drawing, recipe['min_length_mm'])


def draw(image, recipe):
    from .engine import Drawing, analysis_image
    rgb = analysis_image(image)
    long_mm = recipe['long_edge_in'] * 25.4
    drawing = Drawing(long_mm * image.width / max(image.size), long_mm * image.height / max(image.size),
                      recipe['pen_mm'], {})
    if recipe['palette'] == 'black':
        channels = {'K': 1 - cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255}
        angles = {'K': 0}
    elif recipe['palette'] == 'cmy':
        channels = {ink: 1 - rgb[:, :, index].astype(np.float32) / 255 for index, ink in enumerate('CMY')}
        angles = dict(C=0, M=60, Y=120)
    else:
        raise ValueError('Unsupported wave palette')
    for ink, channel in channels.items():
        drawing.paths[ink] = wave_paths(channel, drawing, angles[ink], recipe)
    return drawing
