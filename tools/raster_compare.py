"""PDF interpretation comparison with a fixed, shared supersampling policy.

Keep native-resolution diagnostics, but gate on 4x4 area samples. This reduces
glyph-cache and rectangular-clip pixel snapping without aligning the images,
matching neighboring pixels, or changing the 64/255 and 1% thresholds.
"""
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
FACTOR = 4


def measure(reference, actual):
    if reference.size != actual.size:
        raise AssertionError(f'Raster dimensions differ: {reference.size}, {actual.size}')
    a, b = reference.convert('RGB'), actual.convert('RGB')
    r, g, b_delta = ImageChops.difference(a, b).split()
    delta = ImageChops.lighter(ImageChops.lighter(r, g), b_delta).histogram()
    r, g, b_ink = ImageChops.darker(a, b).split()
    ink = ImageChops.darker(ImageChops.darker(r, g), b_ink).histogram()
    differences, ink_pixels = sum(delta[65:]), sum(ink[:224])
    return dict(differing_pixels=differences, ink_pixels=ink_pixels,
                ratio=differences/max(ink_pixels, 1))


def run(*args):
    args = [str(a) for a in args]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}\n{result.stderr}")


def open_rendered(path, pixel_budget):
    # These PNGs were just produced by our renderers. Permit their expected
    # sample count (tabloid at 864 dpi exceeds Pillow's usual photo warning),
    # while preserving Pillow's default for all other file reads.
    previous = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = max(previous or 0, pixel_budget)
        return Image.open(path)
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def render_comparison(pdf, directory, dpi=216, reference_mode='default'):
    directory = Path(directory).resolve()
    dpi = float(dpi)
    if reference_mode not in ('default', 'expanded_patterns'):
        raise ValueError(f'Unknown reference mode {reference_mode}')
    measurements = {}
    pixel_budget = Image.MAX_IMAGE_PIXELS or 89_478_485
    for factor in (1, FACTOR):
        suffix = '' if factor == 1 else '-sampled'
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            temp = Path(temp)
            reference, coverage = temp/'reference.png', temp/'coverage.png'
            run('node', 'tools/render-pdf.mjs', pdf, reference, '1', dpi*factor)
            if reference_mode == 'expanded_patterns':
                with open_rendered(reference, pixel_budget) as im:
                    im.convert('RGB').reduce(factor).save(directory/f'reference-default{suffix}.png')
                run('.venv/bin/python', 'tools/render_native.py', pdf, reference,
                    '--dpi', dpi*factor, '--expand-patterns')
            run('mutool', 'draw', '-q', '-r', dpi*factor, '-o', coverage, directory/'coverage.svg')
            sizes = []
            for source, stem in [(reference, 'reference'), (coverage, 'coverage')]:
                with open_rendered(source, pixel_budget) as im:
                    sizes.append(im.size)
                    # reduce() averages disjoint blocks; it does not blur across
                    # comparison pixels or stretch the image to fit its partner.
                    im.convert('RGB').reduce(factor).save(directory/f'{stem}{suffix}.png')
            if sizes[0] != sizes[1]:
                raise AssertionError(f'Raster dimensions differ before reduction: {sizes}')
            if factor == 1:
                pixel_budget = sizes[0][0]*sizes[0][1]*FACTOR**2
        with Image.open(directory/f'reference{suffix}.png') as a, Image.open(directory/f'coverage{suffix}.png') as b:
            measurements['native' if factor == 1 else 'sampled'] = measure(a, b)
            ImageChops.difference(a.convert('RGB'), b.convert('RGB')).save(directory/f'raster-difference{suffix}.png')
            if reference_mode == 'expanded_patterns':
                with Image.open(directory/f'reference-default{suffix}.png') as default:
                    measurements['default_native' if factor == 1 else 'default_sampled'] = measure(default, b)
    result = dict(comparison_dpi=dpi, render_dpi=dpi*FACTOR, factor=FACTOR,
                  reduction='disjoint block average', channel_threshold=64,
                  ink_threshold=224, maximum_ratio=.01, **measurements)
    (directory/'raster-sampling.json').write_text(json.dumps(result, indent=2)+'\n')
    return result
