"""Local PDF image-to-pen worker, using the reviewed open-source lab renderer.

Runs in the distribution Python (OpenCV without Intel IPP), isolated from the
PDF interpreter's pinned environment. Pixels use lossless PPM; paths use NumPy
arrays rather than another large JSON intermediate. No network or model calls.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from . import combined, engine, study

VERSION = 'pdf-raster-1'
# Frozen production recipe: lab recipe edits cannot silently alter a saved job.
BASE = dict(long_edge_in=8, pen_mm=.25, min_length_mm=.2, density=.7,
            row_spacing_mm=1.2, amplitude_mm=.42, min_frequency_cpm=.08,
            max_frequency_cpm=1.25, white_cutoff=.004, smooth_mm=.2, shape='sine',
            bend_mm=.15, modulation='amfm', screen_carrier='highlight-diffusion',
            edge_smooth_mm=.3, edge_probe_mm=.8, edge_delta_e_low=8,
            edge_delta_e_high=32, ridge_scales_mm=[.14,.25,.4,.65,.9,1.3],
            ridge_dark_limit=.85, ridge_contrast=.05, ridge_response=.004,
            ridge_min_aspect=2.5, analysis_long_edge_px=2048,
            screen_analysis_long_edge_px=1000, highlight_run_mm=.8,
            highlight_seed=137, threshold=128, lettering_upsample=2,
            join_max_extra_mm=.45)
PT = 72/25.4


def placed_image(image, matrix):
    """Resample into an orthogonal physical plane BEFORE making finite pens.

    Handles rotation/reflection/shear/nonuniform scale without stretching pen
    diameters. White outside the affine quadrilateral never generates demand;
    the original quadrilateral and PDF clip remain hard limits in Rust.
    """
    a,b,c,d,e,f = matrix
    corners=np.array([[e,f],[e+a,f+b],[e+c,f+d],[e+a+c,f+b+d]])/PT
    low,high=corners.min(0),corners.max(0)
    size=high-low
    if min(size)<=1e-8 or abs(a*d-b*c)<1e-10:
        raise ValueError('Degenerate PDF image placement')
    # Preserve native density up to the reviewed recovery analysis limit.
    density=min(image.width/(np.hypot(a,b)/PT), image.height/(np.hypot(c,d)/PT))
    density=min(density,2048/max(size))
    pixels=np.maximum(1,np.ceil(size*density).astype(int))
    pixel_mm=size/pixels
    inv=np.linalg.inv(np.array([[a,c],[b,d]]))
    affine=np.diag(image.size) @ inv @ np.diag(pixel_mm*PT)
    offset=np.diag(image.size) @ inv @ (low*PT-np.array([e,f]))
    resampled=image.transform(tuple(pixels),Image.Transform.AFFINE,
        tuple([affine[0,0],affine[0,1],offset[0],affine[1,0],affine[1,1],offset[1]]),
        Image.Resampling.BICUBIC,fillcolor='white')
    return resampled, size.tolist(), low.tolist()


def render(request, progress=lambda label:None):
    with Image.open(request['pixels']) as src:
        original=src.convert('RGB')
    image,size,offset=placed_image(original,request['matrix'])
    options=request['options']
    palette=options['palette']
    if palette=='auto':palette=study.source_palette(engine.analysis_image(original))
    elif palette=='k':palette='black'
    selected = palette == 'selected'
    role_inks = {}
    screen_roles = []
    if selected:
        selected_ids = options.get('screen_inks', [])
        if options['screening']:
            if not 1 <= len(selected_ids) <= 8 or len(set(selected_ids)) != len(selected_ids):
                raise ValueError('Select between one and eight distinct screening pens')
            for index, ink_id in enumerate(selected_ids):
                # A physical pen shared with recovery gets one combined group.
                role = ('K' if options['black_treatment'] != 'off' and
                        ink_id == options['inks'].get('K') else f'P{index}')
                role_inks[role] = ink_id
                screen_roles.append(role)
    else:
        roles = ('K' if palette=='black' else 'CMY' if palette=='cmy' else 'CMYK') if options['screening'] else ''
        role_inks = {role:options['inks'].get(role) for role in roles}
    if options['black_treatment']!='off':role_inks['K'] = options['inks'].get('K')
    if not selected:role_inks = dict(sorted(role_inks.items()))
    inks={ink['id']:ink for ink in request['inks']}
    mapped={role:inks.get(ink_id) for role,ink_id in role_inks.items()}
    if any(ink is None for ink in mapped.values()):
        raise ValueError('Assign a document pen for raster channel(s): '+', '.join(sorted(k for k,v in mapped.items() if v is None)))
    if not role_inks:raise ValueError('Enable screening or a black treatment, or choose Omit / Reserve blank space')
    recipe=dict(BASE, screening=options['screening'],palette=palette,
                black_policy={'off':'off','lines':'ridge','shapes':'luminance'}[options['black_treatment']],
                interior_ink=options['fullness'],edge_fade_mm=options['edge_falloff_mm'],
                pen_mm_by_ink={r:i['width_mm'] for r,i in mapped.items()},
                ink_colors={r:i.get('measured_color_srgb') or i['color_srgb'] for r,i in mapped.items()})
    if selected:
        recipe.update(palette_mapping='swatch', palette_color_space='oklab',
                      screen_inks=screen_roles,
                      ink_angles={role:index*180/len(screen_roles) for index,role in enumerate(screen_roles)})
    drawing,report=combined.generate(image,recipe,size_mm=size,progress=progress)
    arrays={}
    for role,paths in drawing.paths.items():
        if not paths:continue
        points=np.concatenate([np.asarray(path,dtype=np.float64) for path in paths])
        points=(points+offset)*PT
        arrays[role+'_points']=points
        arrays[role+'_offsets']=np.cumsum([0]+[len(p) for p in paths],dtype=np.int64)
    np.savez(request['paths'],**arrays)
    report.update(version=VERSION,screening=options["screening"],role_inks=role_inks,
                  placed_size_mm=size,placed_origin_mm=offset)
    if selected:
        report['screen_inks'] = [dict(id=role_inks[r], label=mapped[r]['label'],
            color_srgb=recipe['ink_colors'][r], declared_color_srgb=mapped[r]['color_srgb'],
            width_mm=mapped[r]['width_mm']) for r in screen_roles]
    Path(request['report']).write_text(json.dumps(report,allow_nan=False)+'\n')
    return report


def main():
    engine.check_dependencies()
    # Avoid oversubscribing a machine shared with Map Studio.
    import cv2
    cv2.setNumThreads(2)
    request=json.loads(Path(sys.argv[1]).read_text())
    for i,item in enumerate(request['images'],1):
        def progress(label):
            if not request.get('progress_file'):return
            value=dict(version=1,phase='raster',label=f'Image {i}/{len(request["images"])} · {label}',
                       completed=i-1,total=len(request['images']),unit='images',updated_at=time.time(),
                       phase_elapsed_seconds=0,**request['context'])
            path=Path(request['progress_file']);tmp=path.with_suffix('.raster.tmp')
            tmp.write_text(json.dumps(value));tmp.replace(path)
        render(item,progress)


if __name__=='__main__':main()
