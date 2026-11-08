"""Longitudinal error diffusion confined to demand below one straight carrier."""
import math

import numpy as np


def highlight_screen(t, requested_ratio, supported, row, angle, recipe, pen_mm):
    """Quantize light ink demand into short runs; keep dark waves continuous.

    Debt stays within each connected light interval. White/unsupported pixels
    are hard barriers, and debt never interrupts a dark interval. Seeded packet
    sizes and initial debt decorrelate rows without discarding accumulated error.
    The budget counts round caps: a run of centerline length L has approximate
    footprint area d*L + pi*d*d/4. This is geometric area, not optical density.
    """
    run_mm = recipe['highlight_run_mm']
    if run_mm < 2 * pen_mm:
        raise ValueError('Highlight packet must be at least two pen diameters')
    step = float(t[1] - t[0])
    if step <= 0:
        raise ValueError('Highlight samples must advance')
    rng = np.random.default_rng(np.random.SeedSequence([
        recipe.get('highlight_seed', 137), row % 2**32, round(angle * 100) % 2**32]))
    supported = np.asarray(supported, bool)
    light = supported & (requested_ratio > 0) & (requested_ratio < 1)
    active = supported & (requested_ratio >= 1)
    changes = np.diff(np.r_[False, light, False].astype(np.int8))
    minimum = max(2, math.ceil(recipe['min_length_mm'] / step) + 1)
    cap_length = math.pi * pen_mm / 4
    for start, end in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]):
        # A terminal sub-packet cannot repay ink outside its supported region.
        if end - start < minimum:
            continue
        debt = rng.uniform(-.5, .5) * (run_mm + cap_length - step)
        at = start
        while at < end:
            count = max(minimum, round(run_mm * rng.uniform(.8, 1.2) / step))
            stop = min(end, at + count)
            if end - stop < minimum:
                stop = end
            length = (stop - at) * step
            debt += float(np.sum(requested_ratio[at:stop])) * step
            continuing = at > 0 and active[at - 1]
            cost = length + (0 if continuing else cap_length - step)
            if debt >= cost / 2:
                active[at:stop] = True
                debt -= cost
            at = stop
    return active
