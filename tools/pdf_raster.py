"""PDF-side raster bridge. Keep image paint order separate from screen overlap."""
import json
import os
import subprocess
from pathlib import Path

import numpy as np
from pymupdf import mupdf as m

DEFAULTS=dict(screening=True,palette='auto',fullness=1.,edge_falloff_mm=6.35,
              black_treatment='lines',inks={})


def pixels(raw_image, params):
    """Decode the actual callback image (including inline/reused image instances)."""
    image=m.FzImage(m.ll_fz_keep_image(raw_image))
    pix=m.fz_get_unscaled_pixmap_from_image(image)
    rgb=m.fz_convert_pixmap(pix,m.fz_device_rgb(),m.FzColorspace(),m.FzDefaultColorspaces(),m.FzColorParams(params),0)
    # RGB samples are tightly packed only when stride == width * 3.
    w,h=rgb.w(),rgb.h()
    samples=bytes(m.fz_pixmap_samples_memoryview(rgb))
    stride=rgb.stride()
    if stride!=w*3:samples=b''.join(samples[y*stride:y*stride+w*3] for y in range(h))
    return f'P6\n{w} {h}\n255\n'.encode()+samples


def generate(events, device, affine, inks, output, progress_file, context, *, keep_intermediates=False):
    from pdf_driver import multiply, rect_path
    images=output/'raster';images.mkdir()
    requests=[]
    for index,event in enumerate(events['events']):
        if event['kind']!='image':continue
        key=event['pixels_key']
        source=images/(key+'.ppm')
        if not source.exists():source.write_bytes(device.images[key])
        requests.append(dict(event=index,pixels=str(source.resolve()),
                             matrix=multiply(affine,event['matrix']),inks=inks,
                             options={**DEFAULTS,**device.policies.get('raster_settings',{})},
                             paths=str((images/f'{index}.npz').resolve()),
                             report=str((images/f'{index}.json').resolve())))
    if not requests:return {},[]
    root=Path(__file__).resolve().parents[1]
    request=images/'request.json'
    request.write_text(json.dumps(dict(images=requests,progress_file=str(progress_file.resolve()),context=context)))
    with (images/'renderer.log').open('w') as log:
        completed=subprocess.run(['/usr/bin/python3','-m','raster_lab.pdf_render',str(request.resolve())],
            cwd=root,stdout=log,stderr=subprocess.STDOUT,timeout=600,
            env={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
    if completed.returncode:
        raise ValueError('Raster rendering failed: '+(images/'renderer.log').read_text()[-3000:])
    outputs={};reports=[]
    ids={ink['id']:ink for ink in inks}
    for item in requests:
        report=json.loads(Path(item['report']).read_text());reports.append(report)
        matrix=item['matrix'];a,b,c,d,e,f=matrix
        quad=[dict(op='move',p=[e,f]),dict(op='line',p=[e+a,f+b]),
              dict(op='line',p=[e+a+c,f+b+d]),dict(op='line',p=[e+c,f+d]),dict(op='close')]
        channels=[]
        with np.load(item['paths'],allow_pickle=False) as data:
            for role,ink_id in report['role_inks'].items():
                if role+'_points' not in data:continue
                points=data[role+'_points'];offsets=data[role+'_offsets']
                ink=ids[ink_id]
                color=ink.get('measured_color_srgb') or ink['color_srgb']
                channels.append(dict(kind='raster_paths',path=quad,matrix=[1,0,0,1,0,0],
                    color=[int(color[i:i+2],16)/255 for i in (1,3,5)],
                    lines=[points[a:b].tolist() for a,b in zip(offsets[:-1],offsets[1:])],
                    ink_id=ink_id,source=f'raster/image-{item["event"]}/{role}'))
        outputs[item['event']]=channels
    if not keep_intermediates:
        for path in [*images.glob("*.ppm"),*images.glob("*.npz"),request]:
            path.unlink()
    return outputs,reports
