"""Paper-scale elevations for flat-map contours and hillshade.

The small coverage/sketch grid and the adaptive relief mesh keep their own
budgets. Flat terrain detail is sampled from real source tiles independently.
"""
from concurrent.futures import ThreadPoolExecutor
import io
import math

import contourpy
import numpy as np
from PIL import Image
import shapely

from .geography import WORLD, HALF, origin, framing
from .model import MM
from .progress import noop
from .sources import fetch


def pixel_axes(recipe, xs, ys, zoom):
    ox,oy=origin(recipe)
    pixel=WORLD/(256*2**zoom)
    px=(xs+ox+HALF)/pixel-.5
    py=(HALF-ys-oy)/pixel-.5
    ix=np.floor(px).astype(np.int64);iy=np.floor(py).astype(np.int64)
    return ix,iy,px-ix,py-iy


def wanted_tiles(ix,iy,zoom):
    n=2**zoom
    xx=set((np.r_[ix,ix+1]//256 % n).tolist())
    yy=set((np.clip(np.r_[iy,iy+1],0,256*n-1)//256).tolist())
    return sorted((x,y) for x in xx for y in yy)


def sampling_plan(recipe,domain,quality='proof'):
    if quality not in ('draft','proof'):raise ValueError('Unknown contour quality')
    scale,_=framing(recipe)
    target_mm=.5 if quality=='draft' else .2
    max_points=2_000_000 if quality=='draft' else 8_000_000
    max_axis=4096 if quality=='draft' else 8192
    max_tiles=128 if quality=='draft' else 512
    x0,y0,x1,y1=domain.bounds
    width,height=x1-x0,y1-y0
    if not np.isfinite([width,height,scale]).all() or min(width,height,scale)<=0:
        raise ValueError('Contour extent and scale must be finite and positive')
    step=max(target_mm*MM/scale, math.sqrt(width*height/max_points),
             width/(max_axis-1),height/(max_axis-1))

    def axes(spacing):
        nx=max(2,math.ceil(width/spacing)+1);ny=max(2,math.ceil(height/spacing)+1)
        return np.linspace(x0,x1,nx),np.linspace(y0,y1,ny)
    xs,ys=axes(step)
    while len(xs)*len(ys)>max_points or max(len(xs),len(ys))>max_axis:
        step*=max(math.sqrt(len(xs)*len(ys)/max_points),len(xs)/max_axis,len(ys)/max_axis)*1.001
        xs,ys=axes(step)
    zoom=max(0,min(14,math.ceil(math.log2(WORLD/(256*step)))))
    for zoom in range(zoom,-1,-1):
        # Avoid inventing finer detail by oversampling a capped source level.
        spacing=max(step,WORLD/(256*2**zoom))
        xs,ys=axes(spacing)
        ix,iy,_,_=pixel_axes(recipe,xs,ys,zoom)
        tiles=wanted_tiles(ix,iy,zoom)
        if len(tiles)<=max_tiles:break
    actual=np.array([width/(len(xs)-1),height/(len(ys)-1)])*scale/MM
    return dict(zoom=zoom,grid=[len(xs),len(ys)],bounds=list(domain.bounds),
                target_spacing_mm=target_mm,spacing_mm=actual.tolist(),
                source_pixel_mm=WORLD/(256*2**zoom)*scale/MM,
                tile_count=len(tiles),max_points=max_points,max_tiles=max_tiles,
                limited=bool(actual.max()>target_mm*1.01))


def elevation_grid(recipe,domain,cache,quality='proof',progress=None):
    progress=progress or noop
    plan=sampling_plan(recipe,domain,quality)
    x0,y0,x1,y1=plan['bounds'];nx,ny=plan['grid'];zoom=plan['zoom']
    xs=np.linspace(x0,x1,nx);ys=np.linspace(y0,y1,ny)
    ix,iy,u,v=pixel_axes(recipe,xs,ys,zoom)
    wanted=wanted_tiles(ix,iy,zoom)
    def read(key):
        x,y=key
        raw,meta=fetch(f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x}/{y}.png',cache)
        with Image.open(io.BytesIO(raw)) as image:a=np.asarray(image.convert('RGB'),dtype=float)
        return key,a[:,:,0]*256+a[:,:,1]+a[:,:,2]/256-32768,meta
    arrays={};inputs=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index,(key,a,meta) in enumerate(pool.map(read,wanted),1):
            arrays[key]=a;inputs.append(meta)
            progress('elevation','Loading terrain elevation',index,len(wanted),'tiles')
    progress('elevation','Sampling detailed terrain elevations',0,4,'passes')
    z=np.zeros((ny,nx))
    # Sample rectangular tile blocks. Work is O(grid points), rather than
    # scanning the entire dense grid once for every source tile.
    for dy in (0,1):
        gy=np.clip(iy+dy,0,256*2**zoom-1);ty=gy//256;wy=v if dy else 1-v
        for dx in (0,1):
            gx=ix+dx;tx=gx//256 % 2**zoom;wx=u if dx else 1-u
            columns={int(x):np.flatnonzero(tx==x) for x in np.unique(tx)}
            for y in np.unique(ty):
                rows=np.flatnonzero(ty==y)
                for x,cols in columns.items():
                    z[np.ix_(rows,cols)]+=arrays[(x,int(y))][np.ix_(gy[rows]%256,gx[cols]%256)]*wy[rows,None]*wx[cols]
            progress('elevation','Sampling detailed terrain elevations',dy*2+dx+1,4,'passes')
    return xs,ys,z,dict(provider='Terrain Tiles / Terrarium',sampling=plan,tiles=inputs,
                       elevation_range_m=[float(z.min()),float(z.max())])


def contours(xs,ys,z,interval):
    """Connected regular-grid isolines; relief still uses its exact triangles."""
    if not np.isfinite(z).all() or not np.isfinite(interval) or interval<=0:
        raise ValueError('Contour heights and interval must be finite; interval must be positive')
    levels=np.arange(math.floor(z.min()/interval)*interval,z.max(),interval)
    if len(levels)>400:raise ValueError('More than 400 contour levels. Increase the contour interval.')
    generator=contourpy.contour_generator(x=xs,y=ys,z=z,name='serial',line_type='Separate')
    for level in levels:
        lines=generator.lines(float(level))
        yield float(level),shapely.MultiLineString(lines) if lines else shapely.GeometryCollection()
