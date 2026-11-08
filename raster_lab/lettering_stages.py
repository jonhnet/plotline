"""Inspect the published Visitor tracing stages without recognition or new recipes."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import engine, lettering


def make_stages(state, output):
    engine.check_dependencies()
    portfolio = json.loads((output/'portfolio.json').read_text())
    cell = portfolio['cells']['the-visitor']['letter-cubic']
    recipe = cell['signature']['recipe']
    source = next(s for s in json.loads((state/'sources.json').read_text())['images'] if s['id']=='the-visitor')
    path = state/'inputs'/source['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == cell['signature']['source']
    image = engine.read_image(path)
    gray = lettering.analysis_gray(image, recipe)
    folder = output/cell['artifact']
    with Image.open(folder/'threshold-mask.png') as im:
        mask = np.array(im) == 0
    with Image.open(folder/'accepted-skeleton.png') as im:
        thin = np.array(im) == 0
    drawing = engine.Drawing(**json.loads((folder/'paths.json').read_text()))
    paint = engine.stroke_mask(drawing, 'K', gray.shape[1], gray.shape[0], 4)
    twice = cv2.ximgproc.thinning(np.pad(thin.astype(np.uint8)*255, 1))[1:-1,1:-1] > 0
    # This is the user-requested upper-right panel, not an automatic text detector.
    h,w = gray.shape
    crop = (w//2, 0, w, h//2)
    region = (slice(4,h//2), slice(w//2,w-4))  # exclude outer paper clipping
    points = int(thin[region].sum())
    report = dict(source_sha256=source['sha256'], result_key=cell['key'], recipe=recipe,
                  normalized_crop=[.5,0,1,.5], tracing_pixels=[w,h],
                  threshold_topology=lettering.topology(mask[region]),
                  skeleton_topology=lettering.topology(thin[region]),
                  accepted_skeleton_pixels=points,
                  accepted_pixels_without_any_pen_coverage=int(np.sum(thin[region] & (paint[region] == 0))),
                  pixels_changed_by_second_thinning=int(np.sum(thin[region] != twice[region])),
                  interpretation='Pixel topology/coverage are not glyph or readability scores. Outer paper-edge clipping is excluded from counts.')
    with Image.open(folder/'preview.png') as im:
        final = im.crop((im.width//2,0,im.width,im.height//2)).copy()
    panels = [
        ('1. Grayscale input (2x cubic)', Image.fromarray(gray).crop(crop)),
        ('2. Binary mask (threshold 128)', Image.fromarray(np.where(mask,0,255).astype(np.uint8)).crop(crop)),
        ('3. Skeleton pixels (not pen width)', Image.fromarray(np.where(thin,0,255).astype(np.uint8)).crop(crop)),
        ('4. Actual 0.15 mm pen rendering', final),
    ]
    # Display each stage at the pen preview's scale; never feed these display
    # resizes back into recovery. Binary pixels use nearest-neighbor display.
    size = final.size
    margin, title_height = 25, 65
    sheet = Image.new('RGB',(size[0]*2+margin*3,(size[1]+title_height)*2+margin*3),'#f2f2ed')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',28)
    files = ['gray','mask','skeleton','pen']
    for i,(label,panel) in enumerate(panels):
        panel = panel.resize(size,Image.Resampling.NEAREST if i in (1,2) else Image.Resampling.LANCZOS).convert('RGB')
        panel.save(output/f'visitor-stage-{files[i]}.png')
        x=margin+(i%2)*(size[0]+margin);y=margin+(i//2)*(size[1]+title_height+margin)
        draw.text((x,y+12),label,fill='#263c3b',font=font)
        sheet.paste(panel,(x,y+title_height))
    sheet.save(output/'visitor-stages.png')
    (output/'visitor-stages.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(make_stages(args.state,args.output),indent=2))
