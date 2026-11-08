#!/usr/bin/env python3
"""Single-pass pen targets and paper-normalized scan measurements. No hardware I/O."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import warnings

import numpy as np
import PIL
from PIL import Image, ImageCms, ImageDraw
import scipy
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = 200_000_000
FIDUCIALS = [(26, 49, 8), (253, 49, 10), (253, 187, 12), (26, 187, 14)]
DIGITS = ['abcedf', 'bc', 'abged', 'abgcd', 'fgbc', 'afgcd', 'afgecd', 'abc', 'abcdefg', 'abfgcd']
SEGMENTS = dict(a=((0,0),(3,0)), b=((3,0),(3,2.5)), c=((3,2.5),(3,5)), d=((3,5),(0,5)), e=((0,5),(0,2.5)), f=((0,2.5),(0,0)), g=((0,2.5),(3,2.5)))


def write_json(path, value):
    path = Path(path); temp = path.with_suffix('.tmp')
    with temp.open('w') as file:
        json.dump(value, file, allow_nan=False); file.flush(); os.fsync(file.fileno())
    temp.replace(path)


def generate(directory):
    directory = Path(directory); target = json.loads((directory/'target.json').read_text())
    slots = target['slots']; inventory = target['carousel']['slots']
    loaded = sorted(int(n) for n, pen in inventory.items() if pen['state']=='loaded' and pen.get('color'))
    marker = target.get('marker_slot')
    if marker is None:marker = min(loaded, key=lambda n: sum(int(inventory[str(n)]['color'][i:i+2],16) for i in (1,3,5)))
    if marker not in loaded:raise ValueError('Choose a loaded pen for registration marks')
    paths = []; samples = []
    def line(slot, points, kind='reference'):
        paths.append(dict(slot=slot, points=points, kind=kind))
    def digits(text, x, y, slot):
        for k, char in enumerate(text):
            for seg in DIGITS[int(char)]:line(slot, [[x+k*4+a,y+b] for a,b in SEGMENTS[seg]])
    for x,y,size in FIDUCIALS:
        r=size/2
        line(marker,[[x-r,y-r],[x+r,y-r],[x+r,y+r],[x-r,y+r],[x-r,y-r]])
        line(marker,[[x-r,y],[x+r,y]]);line(marker,[[x,y-r],[x,y+r]])
    digits(str(target['sheet_code']), 125, 45, marker)
    bits='1'+f"{target['sheet_code']:020b}"+'1'
    code_centers=[[64+i*7,62.5] for i in range(22)]
    for i,bit in enumerate(bits):
        x,y=code_centers[i]
        if bit=='1':line(marker,[[x,y-2.5],[x,y+2.5]],'code')
    # Four rows of two pens fit the short feed dimension without scaling
    # the stroke widths, crowding adjacent samples or using the back margin.
    for cell, slot in enumerate(slots):
        x=36+123*(cell%2);y=80+29*(cell//2)
        digits(str(slot),x-14,y-2,marker)
        strokes=[[(x,y),(x+18,y)],[(x+18,y+8),(x,y+8)],[(x+31,y-6),(x+31,y+12)],[(x+39,y+12),(x+39,y-6)],
                 [(x+49,y-6),(x+61,y+6)],[(x+71,y+6),(x+59,y-6)],[(x+77,y+6),(x+89,y-6)],[(x+99,y-6),(x+87,y+6)]]
        for k, points in enumerate(strokes):
            line(slot,points,'sample')
            samples.append(dict(slot=slot,index=k,a=points[0],b=points[1],direction=[0,0,90,90,45,45,135,135][k]))
    target.update(fiducials=[dict(label=chr(65+i),center=[x,y],size_mm=size) for i,(x,y,size) in enumerate(FIDUCIALS)],
                  samples=samples,paths=paths,marker_slot=marker,pattern_version=2,code_centers_mm=code_centers)
    a,b,c,d,e,f=target['registration']['sheet_to_device_units']
    hpgl=['IN;','PA;']
    w,h=target['registration']['sheet_size_mm']
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',f'<rect width="{w}" height="{h}" fill="white"/>']
    for slot in sorted(set(slots) | {marker}):
        ink=inventory[str(slot)]; color=ink['color']; rgb=[int(color[i:i+2],16) for i in (1,3,5)]
        width='' if ink['width_mm'] is None else ' width_mm='+str(ink['width_mm'])
        hpgl += [f'CO"pen id={slot} r={rgb[0]} g={rgb[1]} b={rgb[2]}{width}";',f'SP{slot};',f'VS{ink["speed_cm_s"]};',f'FS{ink["force"]};']
        for path in (p for p in paths if p['slot']==slot):
            points=path['points']
            for k,(x,y) in enumerate(points):hpgl.append(f'{"PU" if k==0 else "PD"}{round(a*x+c*y+e)},{round(b*x+d*y+f)};')
            hpgl.append('PU;')
            coords=' '.join(f'{x},{y}' for x,y in points)
            svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{ink["width_mm"] or .3}" stroke-linecap="round" stroke-linejoin="round"/>')
    hpgl+=['SP0;'];svg+=['</svg>']
    (directory/'target.hpgl').write_text('\n'.join(hpgl))
    (directory/'target.svg').write_text('\n'.join(svg))
    write_json(directory/'target.json',target)
    return target


def linear(rgb):
    rgb=np.asarray(rgb,dtype=float)/255
    return np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)


def encoded(rgb):
    rgb=np.clip(rgb,0,1)
    return np.rint(255*np.where(rgb<=.0031308,rgb*12.92,1.055*rgb**(1/2.4)-.055)).astype(np.uint8)


def load_image(path):
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        image=Image.open(path)
        if image.format not in ('PNG','TIFF'):raise ValueError('Use a lossless PNG or TIFF scan')
        if getattr(image,'n_frames',1)!=1:raise ValueError('Upload one scanned sheet, not a multi-page TIFF')
        if image.width*image.height>200_000_000:raise ValueError('Scan exceeds 200 megapixels')
        profile=image.info.get('icc_profile'); notes=[]
        if profile:
            try:image=ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(profile)),ImageCms.createProfile('sRGB'),outputMode='RGB')
            except Exception as error:raise ValueError('Could not read the scan color profile; export an sRGB scan') from error
        else:
            image=image.convert('RGB');notes.append('No embedded color profile; treating scan RGB as sRGB.')
        image.load()
    return image,notes


def component_marks(preview):
    rgb=np.asarray(preview,dtype=float); white=np.percentile(rgb.reshape(-1,3),95,axis=0)
    mask=np.max(1-rgb/np.maximum(white,1),axis=2)>.09
    mask=ndimage.binary_closing(mask,iterations=1)
    labels,count=ndimage.label(mask);objects=ndimage.find_objects(labels)
    candidates=[]
    for label,region in enumerate(objects,1):
        if region is None:continue
        sy,sx=region;h,w=sy.stop-sy.start,sx.stop-sx.start
        if min(w,h)<20 or max(w,h)>180 or not .7<w/h<1.4:continue
        patch=labels[region]==label
        holes=ndimage.binary_fill_holes(patch)&~patch
        hs,n=ndimage.label(holes);sizes=np.bincount(hs.ravel())[1:]
        if sum(sizes>10)!=4:continue
        # The box and cross are symmetric even after rotation. Component bbox
        # midpoint is less sensitive than centroid to uneven ink at crossings.
        candidates.append(((sx.start+sx.stop-1)/2,(sy.start+sy.stop-1)/2,(w+h)/2))
    if len(candidates)!=4:raise ValueError('Could not identify all four registration crosses. Mark A, B, C, D on the scan below.')
    return np.array(sorted(candidates,key=lambda p:p[2]))[:,:2]


def sample_rgb(image, matrix, xy):
    points=np.asarray(xy)@matrix[:2]+matrix[2]
    if np.any(points<1) or np.any(points[:,0]>=image.shape[1]-2) or np.any(points[:,1]>=image.shape[0]-2):
        raise ValueError('The scan cuts off calibration strokes. Include all four crosses and all pen rows.')
    ix=np.floor(points[:,0]).astype(int);iy=np.floor(points[:,1]).astype(int)
    dx=(points[:,0]-ix)[:,None];dy=(points[:,1]-iy)[:,None]
    return (image[iy,ix]*(1-dx)*(1-dy)+image[iy,ix+1]*dx*(1-dy)+image[iy+1,ix]*(1-dx)*dy+image[iy+1,ix+1]*dx*dy)


def registration(target, image, preview, manual=None):
    expected=np.array([p['center'] for p in target['fiducials']])
    if manual is not None:
        actual=np.asarray(manual,dtype=float)
        if actual.shape!=(4,2) or not np.isfinite(actual).all() or np.any(actual<0) or np.any(actual>1):raise ValueError('Mark four crosses A, B, C, D on the scan')
        actual*=np.array(image.size)
    else:
        actual=component_marks(preview)*np.array(image.size)/np.array(preview.size)
    design=np.column_stack([expected,np.ones(4)])
    matrix=np.linalg.lstsq(design,actual,rcond=None)[0]
    scales=np.linalg.svd(matrix[:2],compute_uv=False)
    if scales.min()<4 or scales.max()/scales.min()>1.1:raise ValueError('Scan scale or distortion is unsuitable. Use a flatbed scan of the complete sheet.')
    error=np.linalg.norm(design@matrix-actual,axis=1).max()/scales.min()
    if error>.35:raise ValueError('Registration crosses disagree by more than 0.35 mm. Check their positions or rescan flat.')
    return matrix,actual,error,float(1/scales.min())


def verify_code(target, image, matrix):
    strengths=[]
    # Original portrait sheets retain their geometry and remain analyzable.
    centers=target.get('code_centers_mm',[[32+i*7,61.5] for i in range(22)])
    for cx,cy in centers:
        xy=np.array([[cx+x,cy+y] for y in np.linspace(-1.5,1.5,9) for x in np.linspace(-2.5,2.5,61)])
        rgb=sample_rgb(image,matrix,xy).reshape(9,61,3)
        white=np.percentile(rgb,95,axis=(0,1))
        darkness=np.max(1-rgb/np.maximum(white,1),axis=2)
        strengths.append(float(np.median(np.max(darkness,axis=1))))
    peak=max(strengths)
    if peak<.04:raise ValueError('Sheet identifier is too faint to read. Use a darker pen for the reference marks.')
    bits=''.join('1' if v>max(.035,peak*.3) else '0' for v in strengths)
    expected='1'+f"{target['sheet_code']:020b}"+'1'
    if bits!=expected:raise ValueError('Scan does not match this sheet ID, or registration is inaccurate. Check the printed number and A–D crosses.')


def stroke_sample(image,matrix,center,normal,pitch):
    step=min(.02,pitch/2);offsets=np.linspace(-4,4,math.ceil(8/step)+1)
    rgb=sample_rgb(image,matrix,center+offsets[:,None]*normal)
    # Local paper white, in linear RGB. The high percentile ignores dark
    # strokes and nearby dirt without treating a filled patch as solid ink.
    white=np.percentile(rgb,95,axis=0)
    if np.min(white)<80:return None,'Paper reference is too dark'
    normalized=linear(rgb)/np.maximum(linear(white),.001)
    darkness=np.max(1-normalized,axis=1)
    near=np.abs(offsets)<.8;peak=np.percentile(darkness[near],90)
    if peak<.035:return None,'Faint or missing stroke'
    active=darkness>peak*.5
    labels,n=ndimage.label(active)
    middle=np.flatnonzero(near)[np.argmax(darkness[near])];label=labels[middle]
    run=np.flatnonzero(labels==label) if label else np.array([])
    if not len(run) or run[0]==0 or run[-1]==len(offsets)-1:return None,'Stroke extends beyond sampling band'
    lo,hi=run[0],run[-1]
    def edge(i,j):
        return float(offsets[i]+(peak*.5-darkness[i])/(darkness[j]-darkness[i])*(offsets[j]-offsets[i]))
    left,right=edge(lo-1,lo),edge(hi,hi+1);width=right-left
    if not .03<=width<=5 or abs((left+right)/2)>.75:return None,'Ambiguous stroke position or width'
    band=(offsets>=left+.2*width)&(offsets<=right-.2*width)
    if not band.any():return None,'Insufficient resolution for color'
    ink=np.mean(normalized[band],axis=0)
    return dict(width_mm=width,color_linear=ink.tolist(),paper_rgb=np.rint(white).astype(int).tolist(),
                center_mm=center.tolist(),edges_mm=[(center+left*normal).tolist(),(center+right*normal).tolist()],
                center_band_mm=[(center+(left+.2*width)*normal).tolist(),(center+(right-.2*width)*normal).tolist()],
                contrast=float(peak),pixels_across=width/pitch),None


def analyze(directory):
    directory=Path(directory); target=json.loads((directory.parent.parent/'target.json').read_text())
    def status(detail,percent):write_json(directory/'progress.json',dict(detail=detail,percent=percent))
    status('Reading scan and color profile',5)
    image,notes=load_image(directory/'original.scan'); preview=image.copy();preview.thumbnail((1600,1600))
    preview.save(directory/'preview.jpg',quality=90)
    write_json(directory/'image.json',dict(size=list(image.size),preview_size=list(preview.size)))
    options=json.loads((directory/'options.json').read_text())
    status('Locating registration crosses',20)
    matrix,marks,error,pitch=registration(target,image,preview,options.get('marks'))
    pixels=np.asarray(image)
    verify_code(target,pixels,matrix)
    rows=[];overlay=preview.copy();draw=ImageDraw.Draw(overlay);factor=np.array(preview.size)/np.array(image.size)
    for i,p in enumerate(marks):
        x,y=p*factor;draw.ellipse((x-9,y-9,x+9,y+9),outline='red',width=2);draw.text((x+10,y),chr(65+i),fill='red')
    for slot_index,slot in enumerate(target['slots']):
        status(f'Measuring slot {slot}: widths and center-band color',30+60*slot_index/len(target['slots']))
        samples=[];rejected=[]
        for stroke in [s for s in target['samples'] if s['slot']==slot]:
            a,b=np.array(stroke['a']),np.array(stroke['b']);along=(b-a)/np.linalg.norm(b-a);normal=np.array([-along[1],along[0]])
            for t in np.linspace(.15,.85,16):
                sample,reason=stroke_sample(pixels,matrix,a+t*(b-a),normal,pitch)
                if sample:
                    sample.update(direction=stroke['direction'],stroke=stroke['index']);samples.append(sample)
                    ends=(np.array(sample['edges_mm'])@matrix[:2]+matrix[2])*factor
                    draw.line([tuple(p) for p in ends],fill='#ff0000',width=1)
                else:rejected.append(reason)
        row=dict(slot=slot,samples=samples,rejected=len(rejected),rejection_reasons=sorted(set(rejected)),valid=False)
        if len(samples)>=64 and len(set(s['direction'] for s in samples))==4:
            widths=np.array([s['width_mm'] for s in samples]);median=np.median(widths)
            directions=np.array([s['direction'] for s in samples]);keep=np.zeros(len(samples),dtype=bool)
            # A broad nib can differ systematically by direction. Reject local
            # outliers within each direction, not the legitimate wide axis.
            for angle in [0,45,90,135]:
                selected=directions==angle;center=np.median(widths[selected]);mad=np.median(abs(widths[selected]-center))
                keep[selected]=abs(widths[selected]-center)<=max(4*mad,2*pitch,.1*center)
            widths=widths[keep];accepted=[s for s,k in zip(samples,keep) if k]
            if len(accepted)>=64 and len(set(s['direction'] for s in accepted))==4:
                # Robust aggregate in linear light, encoded to sRGB only once.
                colors=np.array([s['color_linear'] for s in accepted]);lo,hi=np.percentile(colors,[10,90],axis=0)
                color=encoded(np.clip(colors,lo,hi).mean(axis=0))
                row.update(valid=True,accepted=len(accepted),mean_width_mm=round(float(widths.mean()),4),median_width_mm=round(float(np.median(widths)),4),
                           width_mm=round(float(widths.mean()),3),width_p05_mm=round(float(np.percentile(widths,5)),4),width_p95_mm=round(float(np.percentile(widths,95)),4),
                           color_srgb='#'+''.join(f'{v:02x}' for v in color),paper_rgb=np.median([s['paper_rgb'] for s in accepted],axis=0).astype(int).tolist(),
                           directions={str(angle):round(float(np.median([s['width_mm'] for s in accepted if s['direction']==angle])),4) for angle in [0,45,90,135]},
                           outliers=len(samples)-len(accepted),warnings=[])
                if np.median(widths)/pitch<5:row['warnings'].append('Fewer than five pixels across the stroke; a higher-resolution scan would improve measurement.')
                if max(row['directions'].values())-min(row['directions'].values())>max(2*pitch,.2*median):row['warnings'].append('Width differs by stroke direction.')
                if np.percentile(widths,95)-np.percentile(widths,5)>max(2*pitch,.3*median):row['warnings'].append('Stroke width varies substantially.')
        if not row['valid']:row['error']='Too few reliable samples in all four directions. Check the pen and scan.'
        if samples:
            sample=samples[len(samples)//2];center=np.array(sample['center_mm'])@matrix[:2]+matrix[2]
            radius=int(4/pitch);x,y=np.rint(center).astype(int)
            crop=image.crop((x-radius,y-radius,x+radius,y+radius));detail=ImageDraw.Draw(crop)
            for field,color in [('edges_mm','#ff0000'),('center_band_mm','#00ba70')]:
                ends=np.array(sample[field])@matrix[:2]+matrix[2]-np.array([x-radius,y-radius])
                detail.line([tuple(p) for p in ends],fill=color,width=2)
                if field=='edges_mm':
                    for px,py in ends:detail.ellipse((px-2,py-2,px+2,py+2),fill=color)
            crop.save(directory/f'slot-{slot}.png')
        rows.append(row)
    status('Saving measurements and review images',95)
    overlay.save(directory/'overlay.jpg',quality=92)
    with (directory/'original.scan').open('rb') as original:scan_sha256=hashlib.file_digest(original,'sha256').hexdigest()
    report=dict(schema=1,sheet_code=target['sheet_code'],rows=rows,pixel_pitch_mm=pitch,registration_error_mm=error,matrix=matrix.tolist(),marks=(marks/np.array(image.size)).tolist(),
                scan_sha256=scan_sha256,analysis_version=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),versions=dict(numpy=np.__version__,pillow=PIL.__version__,scipy=scipy.__version__),
                color_band_fraction=.6,white_point='Local unmarked paper, normalized in linear sRGB',notes=notes+['Color is paper-normalized scan sRGB, not a spectrophotometric ink measurement.','Widths use half-contrast edges; scanner blur and paper bleed can bias all samples.'])
    write_json(directory/'report.json',report);status('Measurements ready for review',100)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['generate','analyze']);parser.add_argument('directory',type=Path);args=parser.parse_args()
    try:
        generate(args.directory) if args.command=='generate' else analyze(args.directory)
    except Exception as error:
        write_json(args.directory/'error.json',dict(error=str(error)))
        print(str(error),file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
