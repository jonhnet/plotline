#!/usr/bin/env python3
"""Freeze a matched pair of regression rasters and make a static browser review."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def build(source, destination, dpi, sampled=False):
    destination.mkdir(parents=True, exist_ok=True)
    arrays = []
    hashes = {}
    for name in ['reference.png', 'coverage.png']:
        input_name = name.replace('.png', '-sampled.png') if sampled else name
        shutil.copyfile(source/input_name, destination/name)
        hashes[name] = hashlib.sha256((destination/name).read_bytes()).hexdigest()
        arrays.append(np.array(Image.open(destination/name).convert('RGB')))
    reference, coverage = arrays
    if reference.shape != coverage.shape:
        raise ValueError('Rasters must have identical dimensions; no alignment or resampling is applied')
    delta = np.abs(reference.astype(np.int16)-coverage.astype(np.int16))
    bad = delta.max(axis=2) > 64
    ink = np.minimum(reference.min(axis=2), coverage.min(axis=2)) < 224
    # Preserve exact RGB differences, with a separate threshold overlay on context.
    Image.fromarray(delta.astype(np.uint8)).save(destination/'difference.png')
    context = (reference.astype(float)*.18+255*.82).astype(np.uint8)
    context[bad] = [214, 0, 117]
    Image.fromarray(context).save(destination/'threshold.png')
    # White means equal; dark means a large difference. This is for inspection,
    # not an additional pass criterion or a change to the original rasters.
    Image.fromarray((255-np.minimum(delta*4, 255)).astype(np.uint8)).save(destination/'difference-enhanced.png')
    height, width = bad.shape
    metadata = dict(title=source.name, dpi=dpi, width=width, height=height,
                    comparison_kind='supersampled gate' if sampled else 'native diagnostic',
                    source_directory=str(source.relative_to(ROOT)), sha256=hashes,
                    differing_pixels=int(bad.sum()), ink_pixels=int(ink.sum()),
                    ratio=float(bad.sum()/max(ink.sum(), 1)), threshold=64, ink_threshold=224,
                    reference='PDF → MuPDF.js → PNG',
                    coverage='PDF → native MuPDF / FreeType → Rust/GEOS → SVG → mutool → PNG',
                    presets=[dict(name='Whole sheet', fit=True),
                             dict(name='Title', x=220, y=126, zoom=3),
                             dict(name='Footer', x=205, y=744, zoom=5),
                             dict(name='Top clip edge', x=245, y=175.55, zoom=8),
                             dict(name='Right clip edge', x=569.48, y=487, zoom=8),
                             dict(name='Street label', x=404, y=345, zoom=4)])
    if sampled:
        metadata['sampling'] = json.loads((source/'raster-sampling.json').read_text())
        if metadata['sampling']['comparison_dpi'] != dpi:
            raise ValueError('Requested review DPI differs from sampled raster DPI')
    (destination/'review.json').write_text(json.dumps(metadata, indent=2)+'\n')
    shutil.copyfile(ROOT/'tools/raster-review.html', destination/'index.html')
    print(json.dumps({k:metadata[k] for k in ['width','height','dpi','differing_pixels','ink_pixels','ratio']}))
    print(destination/'index.html')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--dpi', type=float, required=True)
    parser.add_argument('--sampled', action='store_true', help='Review the fixed supersampled comparison pair')
    args = parser.parse_args()
    build(args.source.resolve(), args.destination.resolve(), args.dpi, args.sampled)
