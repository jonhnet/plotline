#!/usr/bin/env python3
"""Reference PDF rasterization, optionally bypassing raster pattern-tile caching."""
import argparse
import pymupdf
from pymupdf import mupdf as m
from pdf_native import make_pattern_clips_explicit


def render(filename, output, dpi=216, expand_patterns=False):
    with pymupdf.open(filename) as doc:
        if expand_patterns:
            make_pattern_clips_explicit(doc)
        page = doc[0]
        scale = m.FzMatrix(dpi/72, 0, 0, dpi/72, 0, 0)
        bounds = m.fz_round_rect(m.fz_transform_rect(m.FzRect(*page.rect), scale))
        pixmap = m.fz_new_pixmap_with_bbox(m.fz_device_rgb(), bounds, m.FzSeparations(), 0)
        m.fz_clear_pixmap_with_value(pixmap, 255)
        device = m.fz_new_draw_device(m.FzMatrix(), pixmap)
        if expand_patterns:
            m.fz_enable_device_hints(device, m.FZ_NO_TILING)
        m.fz_run_page(page.this, device, scale, m.FzCookie())
        m.fz_close_device(device)
        m.fz_save_pixmap_as_png(pixmap, str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--dpi", type=float, default=216)
    parser.add_argument("--expand-patterns", action="store_true")
    args = parser.parse_args()
    render(args.input, args.output, args.dpi, args.expand_patterns)
