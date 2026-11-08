"""Small, inspectable baselines for the raster lab, written for this project.

All geometry uses page millimetres. These are experiments, not faithful inverse
rendering: fixed-width skeletons lose brush weight; edge detectors can double
outlines; the hatch screen and CMY/CMYK model are deliberately uncalibrated.
"""
from dataclasses import dataclass, field
from io import BytesIO
import math
import re
from xml.sax.saxutils import escape

import cairo
import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps

ALGORITHM_VERSION = "raster-lab-1"
ANALYSIS_LONG_EDGE = 1000
PREVIEW_DPI = 150
COLORS = {"K": "#222222", "C": "#00bcd4", "M": "#e32991", "Y": "#efcf00"}
# Illustrative pen RGBs, not a measured palette. Multiplication in sRGB is only
# a preview model, matching the SVG's multiply groups rather than physical ink.
PALETTES = {"black": [("K", 45), ("K", 135)],
            "cmy": [("C", 15), ("M", 75), ("Y", 135)],
            "cmyk": [("C", 15), ("M", 75), ("Y", 135), ("K", 45)]}


def check_dependencies():
    """The usual PyPI wheel includes proprietary IPP; use a build without it."""
    info = cv2.getBuildInformation()
    for name in ('Intel IPP', 'Non-free algorithms'):
        match = re.search(r'^\s*' + re.escape(name) + r':\s*(.*)$', info, re.M)
        if match and match.group(1).strip() not in ('NO', 'OFF'):
            raise RuntimeError(f"Raster lab requires an open-source-only OpenCV build ({name} enabled). See raster_lab/README.md.")
    cv2.ocl.setUseOpenCL(False)
    if not hasattr(cv2, 'ximgproc') or not hasattr(cv2.ximgproc, 'thinning'):
        raise RuntimeError('OpenCV contrib thinning is required; see raster_lab/README.md')


@dataclass
class Drawing:
    width_mm: float
    height_mm: float
    pen_mm: float
    paths: dict
    pen_mm_by_ink: dict = field(default_factory=dict)
    ink_colors: dict = field(default_factory=dict)

    def ink_width(self, ink):
        return self.pen_mm_by_ink.get(ink, self.pen_mm)

    def ink_color(self, ink):
        color = self.ink_colors.get(ink, COLORS.get(ink))
        if color is None:
            raise ValueError('Missing color for ink: ' + ink)
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Ink color must be a six-digit sRGB hex value')
        return color


def read_image(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        profile = source.info.get("icc_profile")
        if profile:
            image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(profile)),
                                              ImageCms.createProfile("sRGB"), outputMode="RGB")
        return image


def analysis_image(image, long_edge_px=ANALYSIS_LONG_EDGE):
    if long_edge_px <= 0:
        raise ValueError('Analysis pixel limit must be positive')
    scale = min(1, long_edge_px / max(image.size))
    return np.asarray(image.resize(tuple(max(1, round(v * scale)) for v in image.size),
                                   Image.Resampling.LANCZOS)).copy()


def skeleton_paths(mask, include_points=False, already_thin=False):
    """Trace each undirected skeleton edge once, including closed loops.

    Suppress diagonal shortcuts when an orthogonal connection exists: otherwise
    a simple staircase produces tiny triangular cycles and excessive lifts.
    Junctions terminate runs; no guessed semantic stroke order is introduced.
    """
    thin = mask if already_thin else cv2.ximgproc.thinning(np.pad(mask.astype(np.uint8) * 255, 1))[1:-1, 1:-1]
    ys, xs = np.nonzero(thin)
    pixels = set(zip(xs.tolist(), ys.tolist()))
    adjacent = {}
    for x, y in sorted(pixels):
        neighbors = []
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
            q = (x + dx, y + dy)
            if q not in pixels:
                continue
            if dx and dy and ((x + dx, y) in pixels or (x, y + dy) in pixels):
                continue
            neighbors.append(q)
        adjacent[(x, y)] = neighbors
    used = set()

    def edge(a, b):
        return (a, b) if a < b else (b, a)

    def walk(a, b):
        points = [a, b]
        used.add(edge(a, b))
        while len(adjacent[b]) == 2:
            q = adjacent[b][0] if adjacent[b][0] != a else adjacent[b][1]
            if edge(b, q) in used:
                break
            used.add(edge(b, q))
            points.append(q)
            a, b = b, q
        return points

    runs = [[p] for p in sorted(adjacent) if not adjacent[p]] if include_points else []
    for p in sorted(adjacent):
        if len(adjacent[p]) != 2:
            for q in adjacent[p]:
                if edge(p, q) not in used:
                    runs.append(walk(p, q))
    for p in sorted(adjacent):
        for q in adjacent[p]:
            if edge(p, q) not in used:
                runs.append(walk(p, q))
    return runs


def clip_segment(a, b, bounds):
    """Liang–Barsky, with an inset rectangle for finite pen footprints."""
    xmin, ymin, xmax, ymax = bounds
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0., 1.
    for p, q in ((-dx, a[0] - xmin), (dx, xmax - a[0]),
                 (-dy, a[1] - ymin), (dy, ymax - a[1])):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            lo = max(lo, q / p)
        else:
            hi = min(hi, q / p)
        if lo > hi:
            return None
    return ([a[0] + lo * dx, a[1] + lo * dy], [a[0] + hi * dx, a[1] + hi * dy])


def path_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def finish_paths(paths, drawing, minimum):
    result = []
    # Reserve additional rounding clearance for the three-decimal SVG paths.
    r = drawing.pen_mm / 2 + .001
    bounds = (r, r, drawing.width_mm - r, drawing.height_mm - r)
    for points in paths:
        current = []
        for a, b in zip(points, points[1:]):
            clipped = clip_segment(a, b, bounds)
            if clipped:
                c, d = ([round(v, 3) for v in p] for p in clipped)
                if current and current[-1] != c:
                    if path_length(current) >= minimum:
                        result.append(current)
                    current = []
                if not current:
                    current.append(c)
                if current[-1] != d:
                    current.append(d)
            elif current:
                if path_length(current) >= minimum:
                    result.append(current)
                current = []
        if path_length(current) >= minimum:
            result.append(current)
    return result


def centerlines(gray, drawing, experiment, edge_mode=False):
    if edge_mode:
        smooth = cv2.GaussianBlur(gray, (0, 0), experiment['sigma'])
        mask = cv2.Canny(smooth, experiment['low'], experiment['high']) > 0
    else:
        mask = gray < experiment['threshold']
    sy, sx = drawing.height_mm / gray.shape[0], drawing.width_mm / gray.shape[1]
    paths = []
    for raw in skeleton_paths(mask):
        pts = (np.asarray(raw, np.float32) + .5) * [sx, sy]
        simplified = cv2.approxPolyDP(pts.astype(np.float32), .035, False).reshape(-1, 2)
        if len(simplified) > 1:
            paths.append(simplified.tolist())
    return finish_paths(paths, drawing, experiment['min_length_mm'])


def hatch_paths(channel, drawing, angle, density, minimum):
    """Screen continuous rows with deterministic ordered coverage thresholds.

    Adjacent rows carry different thresholds; smooth variation along each row
    produces continuous runs. This trades spatial tone resolution for fewer
    lifts. Threshold bands are visible on purpose as a baseline to improve.
    """
    spacing = drawing.pen_mm * 1.5
    a = math.radians(angle)
    u, v = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
    corners = np.array([[0, 0], [drawing.width_mm, 0],
                        [0, drawing.height_mm], [drawing.width_mm, drawing.height_mm]])
    ts, ns = corners @ u, corners @ v
    t = np.arange(ts.min(), ts.max() + spacing, drawing.pen_mm / 2)
    output = []
    # An eight-row bit-reversed screen, decorrelating adjacent thresholds.
    levels = [0, 4, 2, 6, 1, 5, 3, 7]
    for row in range(math.floor(ns.min() / spacing), math.ceil(ns.max() / spacing) + 1):
        points = t[:, None] * u + row * spacing * v
        xs = (points[:, 0] / drawing.width_mm * channel.shape[1] - .5).astype(np.float32)[None, :]
        ys = (points[:, 1] / drawing.height_mm * channel.shape[0] - .5).astype(np.float32)[None, :]
        samples = cv2.remap(channel, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[0]
        # One family covers at most diameter/spacing of its region. Scale the
        # threshold to compensate, but saturation remains a physical limit.
        active = samples * density * spacing / drawing.pen_mm >= (levels[row % 8] + .5) / 8
        changes = np.diff(np.r_[False, active, False].astype(np.int8))
        for start, end in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]):
            if end - start > 1:
                output.append([points[start].tolist(), points[end - 1].tolist()])
    return finish_paths(output, drawing, minimum)


def stroke_mask(drawing, ink, width, height, supersample=1):
    w, h = width * supersample, height * supersample
    surface = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
    context = cairo.Context(surface)
    context.scale(w / drawing.width_mm, h / drawing.height_mm)
    context.set_source_rgba(1, 1, 1, 1)
    context.set_line_width(drawing.ink_width(ink))
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    for path in drawing.paths.get(ink, []):
        context.move_to(*path[0])
        for point in path[1:]:
            context.line_to(*point)
    context.stroke()
    surface.flush()
    mask = np.frombuffer(surface.get_data(), np.uint8).reshape(h, surface.get_stride())[:, :w].copy()
    if supersample > 1:
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_AREA)
    return mask


def draw(image, experiment):
    if 'paper' in experiment:
        from .paper import resolve
        experiment = resolve(image.size, experiment)
    if experiment['method'] == 'combined_study':
        from .combined import generate
        return generate(image, experiment)[0]
    if experiment['method'] == 'component_study':
        from .lettering import draw as draw_components
        return draw_components(image, experiment)
    if experiment['method'] in ('stroke_study', 'screen_study'):
        from .study import draw as draw_study
        return draw_study(image, experiment)
    if experiment['method'] in ('ridges', 'edge_wave'):
        from .focus import draw as draw_focus
        return draw_focus(image, experiment)
    if experiment['method'] == 'wave':
        from .waves import draw as draw_waves
        return draw_waves(image, experiment)
    rgb = analysis_image(image)
    h, w = rgb.shape[:2]
    long_mm = experiment['long_edge_in'] * 25.4
    drawing = Drawing(long_mm * image.width / max(image.size), long_mm * image.height / max(image.size), experiment['pen_mm'], {})
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    method = experiment['method']
    if method in ('centerline', 'edges', 'hybrid'):
        drawing.paths['K'] = centerlines(gray, drawing, experiment, method == 'edges')
    if method in ('hatch', 'hybrid'):
        palette = experiment['palette']
        if palette == 'black':
            channels = {'K': 1 - gray.astype(np.float32) / 255}
        else:
            cmy = 1 - rgb.astype(np.float32) / 255
            if palette == 'cmyk':
                k = np.min(cmy, axis=2)
                cmy = (cmy - k[:, :, None]) / np.maximum(1 - k[:, :, None], 1e-6)
            channels = {ink: cmy[:, :, n] for n, ink in enumerate('CMY')}
            if palette == 'cmyk':
                channels['K'] = k
        if method == 'hybrid':
            # A black pen already explains part of all channels' darkness.
            footprint = stroke_mask(drawing, 'K', w, h, 2).astype(np.float32) / 255
            channels = {ink: np.maximum(0, values - footprint) for ink, values in channels.items()}
        for ink, angle in PALETTES[palette]:
            density = experiment['density']
            if palette == 'black':
                # Two crossing families contribute toward the same demand.
                channel = 1 - np.sqrt(1 - channels[ink] * density)
                density = 1
            else:
                channel = channels[ink]
            paths = hatch_paths(channel.astype(np.float32), drawing, angle, density,
                                experiment['min_length_mm'])
            drawing.paths.setdefault(ink, []).extend(paths)
    return drawing


def render(drawing, dpi=PREVIEW_DPI):
    w, h = [max(1, round(v / 25.4 * dpi)) for v in (drawing.width_mm, drawing.height_mm)]
    composite = np.ones((h, w, 3), np.float32)
    union = np.zeros((h, w), np.float32)
    for ink in drawing.paths:
        mask = stroke_mask(drawing, ink, w, h, 3).astype(np.float32) / 255
        color = np.array([int(drawing.ink_color(ink)[i:i+2], 16) / 255 for i in (1, 3, 5)])
        composite *= 1 - mask[:, :, None] * (1 - color)
        union = np.maximum(union, mask)
    rgb = np.uint8(np.clip(composite * 255 + .5, 0, 255))
    return Image.fromarray(rgb), float(union.mean())


def svg(drawing, title):
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{drawing.width_mm:.3f}mm" height="{drawing.height_mm:.3f}mm" viewBox="0 0 {drawing.width_mm:.3f} {drawing.height_mm:.3f}">',
             f'<title>{escape(title)}</title>',
             '<desc>Experimental pen paths. Color overlap is an illustrative multiply preview, not calibrated ink.</desc>',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<g fill="none" stroke-width="{drawing.pen_mm}" stroke-linecap="round" stroke-linejoin="round" style="isolation:isolate">']
    for ink, paths in drawing.paths.items():
        width = f' stroke-width="{drawing.ink_width(ink)}"' if ink in drawing.pen_mm_by_ink else ''
        lines.append(f'<g id="ink-{ink}" stroke="{drawing.ink_color(ink)}"{width} style="mix-blend-mode:multiply">')
        for points in paths:
            d = 'M' + ' L'.join(f'{x:.3f},{y:.3f}' for x, y in points)
            lines.append(f'<path d="{d}"/>')
        lines.append('</g>')
    lines.extend(['</g>', '</svg>'])
    return '\n'.join(lines)


def metrics(drawing):
    by_ink = {ink: dict(paths=len(paths), meters=round(sum(path_length(p) for p in paths) / 1000, 5))
              for ink, paths in drawing.paths.items()}
    meters = round(sum(v['meters'] for v in by_ink.values()), 5)
    # Deposited area proxy counts every pass and ink, including overlaps.
    # It is not ink volume or the union of painted pixels on the page.
    if drawing.pen_mm_by_ink:
        for ink, stats in by_ink.items():
            stats['pen_mm'] = drawing.ink_width(ink)
        ink_area_mm2 = sum(stats['meters'] * 1000 * drawing.ink_width(ink) for ink, stats in by_ink.items())
    else:
        ink_area_mm2 = meters * 1000 * drawing.pen_mm
    ink_area = ink_area_mm2 / (drawing.width_mm * drawing.height_mm)
    result = dict(paths=sum(v['paths'] for v in by_ink.values()),
                meters=meters, ink_area_ratio=ink_area,
                by_ink=by_ink, width_in=drawing.width_mm / 25.4,
                height_in=drawing.height_mm / 25.4, pen_mm=drawing.pen_mm)
    if drawing.pen_mm_by_ink:
        result['pen_mm_by_ink'] = drawing.pen_mm_by_ink
    return result
