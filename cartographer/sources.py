"""Cached OSM vector and bare-earth tiles. All views share this acquisition path."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import pickle
import time
import threading
from urllib.request import Request,urlopen
import uuid

import mapbox_vector_tile
import numpy as np
import contourpy
from PIL import Image
import shapely
from shapely import affinity
from shapely.geometry import shape,box

from . import ROOT
from .progress import noop
from .model import canonical,digest
from .geography import WORLD,HALF,origin,latitude_factor,point,ground_box
from .storage import atomic_json
from build_map import parts

UA='PlotlineCartographer/2 (private map authoring; https://dev.iac.jonh.net)'
ATTRIBUTION='© OpenMapTiles · © OpenStreetMap contributors · openstreetmap.org/copyright'
MAX_BYTES=50_000_000
MAJOR={'motorway','trunk','primary','secondary','tertiary'}


def source_version():
    files=list((ROOT/'cartographer').glob('*.py'))+[ROOT/'tools'/p for p in
           ['build_map.py','regional_map.py','terrain_geometry.py','print_contract.py']]
    files+=list((ROOT/'contracts/v1').glob('*.json'))
    native=ROOT/'native/cartographer-geometry'
    files+=list((native/'src').glob('*.rs'))+[native/'Cargo.toml',native/'Cargo.lock']
    h=hashlib.sha256()
    for p in sorted(files): h.update(str(p.relative_to(ROOT)).encode());h.update(p.read_bytes())
    from .visibility import native_backend
    backend=native_backend()
    h.update(b'rust' if backend else b'python')
    if backend:h.update(Path(backend.__file__).read_bytes())
    for f in ['DejaVuSans.ttf','DejaVuSerif.ttf']: h.update((Path('/usr/share/fonts/truetype/dejavu')/f).read_bytes())
    h.update(canonical(dict(pymupdf=__import__('pymupdf').pymupdf_version,shapely=shapely.__version__,
                            geos=shapely.geos_version_string,numpy=np.__version__,contourpy=contourpy.__version__,decoder='mapbox-vector-tile/2.2.0')))
    return h.hexdigest()


def cached(directory,key,build,on_wait=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(key+'.pickle')
    with (directory/(key+'.lock')).open('a+b') as lock:
        if on_wait:
            while True:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:on_wait();time.sleep(.2)
        else:fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists():
            path.touch() # LRU access; cleanup shares this key's lock.
            with path.open('rb') as f: return pickle.load(f)
        value=build();temp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        with temp.open('wb') as f: pickle.dump(value,f,protocol=5)
        os.replace(temp,path)
        # GEOS precision metadata does not survive Shapely's pickle format.
        # Use the persisted representation on a miss too, so first renders,
        # cache hits and offline replays have identical overlay semantics.
        with path.open('rb') as f:return pickle.load(f)


def fetch(url,cache,maximum=MAX_BYTES):
    """Atomic immutable cache, per-key lock, and four HTTP slots across workers."""
    directory=Path(cache)/'http';directory.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(url.encode()).hexdigest();path=directory/key
    with (directory/(key+'.lock')).open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists(): return path.read_bytes(),dict(url=url,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),cache_key=key)
        slots=ROOT/'build/cartographer-http-slots';slots.mkdir(parents=True,exist_ok=True)
        slot=None
        while slot is None:
            for i in range(4):
                f=(slots/f'slot-{i}.lock').open('a+b')
                try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);slot=f;break
                except BlockingIOError: f.close()
            if slot is None: time.sleep(.05)
        try:
            with urlopen(Request(url,headers={'User-Agent':UA}),timeout=25) as response:
                raw=response.read(maximum+1)
                if len(raw)>maximum: raise ValueError('Source exceeds the per-request download budget')
        finally: slot.close()
        temp=directory/(key+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(raw);os.replace(temp,path)
        meta=dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),cache_key=key)
        atomic_json(directory/(key+'.json'),meta)
        return raw,meta


def tile_source(cache):
    data,_=fetch('https://tiles.openfreemap.org/planet',cache)
    return json.loads(data)['tiles'][0]


def tile_range(domain,recipe,zoom):
    ox,oy=origin(recipe);x0,y0,x1,y1=domain.bounds;n=2**zoom;size=WORLD/n
    west=math.floor((x0+ox+HALF)/size);east=math.floor((x1+ox+HALF-1e-8)/size)
    north=max(0,math.floor((HALF-y1-oy)/size));south=min(n-1,math.floor((HALF-y0-oy-1e-8)/size))
    return west,east,north,south


def tile_count(domain,recipe,zoom):
    w,e,n,s=tile_range(domain,recipe,zoom)
    return max(0,e-w+1)*max(0,s-n+1)


def tile_ids(domain,recipe,zoom):
    w,e,n,s=tile_range(domain,recipe,zoom)
    return [(zoom,x,y) for x in range(w,e+1) for y in range(n,s+1)]


def plan(recipe,domain,quality):
    limit=48 if quality=='draft' else 96
    ground_pixel=recipe['framing']['scale_denominator']*.00028/latitude_factor(recipe)
    zoom=max(0,min(14,round(math.log2(WORLD/(256*ground_pixel)))))
    wanted=[recipe['features']]+([recipe['outside']['features']] if recipe['boundary'] else [])
    all_buildings=any(f['buildings']=='all' for f in wanted)
    if all_buildings:
        if tile_count(domain,recipe,14)>limit and quality=='proof':
            raise ValueError('Every building at this extent exceeds the detail budget. Zoom in, or choose Automatic buildings.')
        zoom=14
    elif quality=='draft' and (zoom<14 or tile_count(domain,recipe,14)>4): zoom=max(0,zoom-1)
    requested=zoom
    while zoom>0 and tile_count(domain,recipe,zoom)>limit: zoom-=1
    warnings=[]
    if zoom<requested: warnings.append(f'Tile budget lowered detail from zoom {requested} to {zoom}.')
    if zoom<14 and any(f['buildings']!='none' for f in wanted):
        warnings.append('Buildings omitted at this scale. Zoom in for individual footprints; “All available” requires a bounded extent.')
    if zoom<12 and any(f['roads']=='all' for f in wanted): warnings.append('OSM source tiles generalize minor roads at this scale.')
    return dict(zoom=zoom,tiles=tile_ids(domain,recipe,zoom),warnings=warnings,
                buildings=zoom==14 and any(f['buildings']!='none' for f in wanted),
                parks=any(f['parks'] for f in wanted),labels=any(f['labels']!='none' for f in wanted),
                roads='all' if any(f['roads']=='all' for f in wanted) else 'major' if any(f['roads']=='major' for f in wanted) else 'none')


def dataset(recipe,domain,cache,quality='proof',progress=None):
    progress=progress or noop
    config=plan(recipe,domain,quality)
    key=digest([hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),recipe['data'],recipe['center'],list(domain.bounds),config,quality])
    progress('tiles','Checking cached map geometry')
    data=cached(Path(cache)/'decoded',key,lambda:_dataset(recipe,domain,cache,config,quality,progress),
                on_wait=lambda:progress('tiles','Waiting for another preview to finish loading this area'))
    progress('geometry','Map geometry ready',len(data['areas'])+len(data['roads']),len(data['areas'])+len(data['roads']),'features')
    return data


def _dataset(recipe,domain,cache,config,quality,progress=None):
    progress=progress or noop
    completed=0;hits=0;progress_lock=threading.Lock();tile_total=len(config['tiles'])
    progress('tiles',f'Loading OSM tiles at zoom {config["zoom"]}',0,tile_total,'tiles')
    ox,oy=origin(recipe);grouped=defaultdict(list);descriptions={};labels={};sources=[];total=0
    def read(tile):
        z,x,y=tile;url=recipe['data']['vector_template'].format(z=z,x=x%(2**z),y=y)
        nonlocal completed,hits
        hit=(Path(cache)/'http'/hashlib.sha256(url.encode()).hexdigest()).exists()
        raw,meta=fetch(url,cache)
        with progress_lock:
            completed+=1;hits+=hit
            progress('tiles',f'Loading OSM tiles at zoom {config["zoom"]} · {hits} cached',completed,tile_total,'tiles')
        return tile,raw,meta
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloaded=list(pool.map(read,config['tiles']))
        for tile_index,((z,x,y),raw,meta) in enumerate(downloaded):
            progress('geometry','Decoding map tiles',tile_index,tile_total,'tiles')
            total+=len(raw)
            if total>MAX_BYTES: raise ValueError('Map tiles exceed 50 MB. Reduce detail or extent.')
            sources.append(meta);size=WORLD/(2**z);left=x*size-HALF-ox;top=HALF-y*size-oy
            layers=mapbox_vector_tile.decode(raw,default_options={'y_coord_down':True})
            tilebox=box(left,top-size,left+size,top).intersection(domain)
            for name,layer in layers.items():
                if name not in ('building','transportation','water','waterway','park','landuse','landcover','place','water_name','transportation_name'): continue
                if name=='building' and not config['buildings']: continue
                if name in ('park','landcover','landuse') and not config.get('parks',True):continue
                if name in ('place','water_name','transportation_name') and not config.get('labels',True):continue
                if name=='transportation' and config.get('roads')=='none':continue
                factor=size/layer['extent']
                for feature in layer['features']:
                    p=feature['properties'];cls=p.get('class','');title=p.get('name:en') or p.get('name') or ''
                    if name=='transportation' and config.get('roads')=='major' and cls not in MAJOR:continue
                    if name=='park' and cls in ('historic','protected_site'):continue
                    if name in ('landcover','landuse') and cls not in ('wood','grass','park','forest','recreation_ground','cemetery'): continue
                    if name in ('transportation','transportation_name') and (cls not in MAJOR|{'minor','service','path','track','pedestrian'} or p.get('brunnel')=='tunnel'): continue
                    g=shape(feature['geometry'])
                    if not g.is_valid:g=shapely.make_valid(g)
                    g=shapely.set_precision(affinity.affine_transform(g,[factor,0,0,-factor,left,top]).intersection(tilebox),.001)
                    if g.is_empty:continue
                    if name in ('place','water_name','transportation_name'):
                        direction=None
                        if name=='transportation_name' and title:
                            lines=parts(g,'LineString')
                            if not lines:continue
                            longest=max(lines,key=lambda line:line.length)
                            a,b=longest.interpolate(.4,normalized=True),longest.interpolate(.6,normalized=True)
                            direction=[b.x-a.x,b.y-a.y]
                            g=longest.interpolate(.5,normalized=True)
                        if title and g.geom_type=='Point':
                            labels[(name,title,round(g.x,2),round(g.y,2))]=dict(name=title,
                                kind='roads' if name=='transportation_name' else name,
                                rank=30 if name=='transportation_name' else p.get('rank',20),geometry=g,direction=direction,cls=cls)
                        continue
                    kind={'building':'buildings','transportation':'roads','water':'water','waterway':'streams'}.get(name,'parks')
                    identity=str(feature.get('id',0))
                    if kind in ('water','parks'): identity=title or cls
                    elif not identity or identity=='0': identity=title or digest(p)[:16]
                    bridge=p.get('brunnel')=='bridge';level=p.get('layer',1 if bridge else 0)
                    key=(kind,identity,title,cls,bridge,level)
                    grouped[key].append(g);descriptions[key]=dict(kind=kind,id=name+'/'+identity,name=title,cls=cls,bridge=bridge,level=level)
    areas=[];roads=[];streams=[]
    budget=18000 if quality=='draft' else 65000
    if len(grouped)>budget: raise ValueError(f'This extent contains {len(grouped):,} feature groups. Zoom in or reduce detail.')
    # Dissolve tile-cut polygons and join line pieces before drawing boundaries.
    progress('geometry','Joining features across tile edges',0,len(grouped),'features')
    for index,(key,geometries) in enumerate(grouped.items()):
        progress('geometry','Joining features across tile edges',index,len(grouped),'features')
        g=shapely.union_all(geometries);d=descriptions[key]
        if d['kind'] in ('roads','streams'):
            lines=parts(g,'LineString')
            if not lines:continue
            g=shapely.line_merge(shapely.MultiLineString(lines))
        else:
            polygons=parts(g,'Polygon')
            if not polygons:continue
            g=shapely.union_all(polygons)
        d['geometry']=g
        (roads if d['kind']=='roads' else streams if d['kind']=='streams' else areas).append(d)
    return dict(areas=areas,roads=roads,streams=streams,labels=list(labels.values()),issues=[],
                warnings=config['warnings'],source=dict(provider='OpenFreeMap / OpenMapTiles',attribution=ATTRIBUTION,
                vector_template=recipe['data']['vector_template'],zoom=config['zoom'],tile_count=len(sources),bytes=total,tiles=sources))


def elevations(recipe,domain,cache,quality='proof',progress=None):
    """Sample a bounded regular grid from Terrarium tiles (bilinear, no extrapolation)."""
    progress=progress or noop
    x0,y0,x1,y1=domain.bounds;ox,oy=origin(recipe)
    cells=32 if quality=='draft' else 64
    zoom=max(0,min(14,math.ceil(math.log2(WORLD*cells/(256*max(x1-x0,y1-y0))))))
    xs=np.linspace(x0,x1,cells+1);ys=np.linspace(y0,y1,cells+1)
    xx,yy=np.meshgrid(xs+ox,ys+oy);size=WORLD/2**zoom
    tx=np.floor((xx+HALF)/size).astype(int);ty=np.clip(np.floor((HALF-yy)/size).astype(int),0,2**zoom-1)
    # Pixel-center coordinates. Fetch the neighboring tile where interpolation
    # crosses a tile boundary; clamp only at the Mercator polar data edge.
    px=(xx+HALF)/size*256-.5;py=(HALF-yy)/size*256-.5
    ix=np.floor(px).astype(int);iy=np.floor(py).astype(int);u=px-ix;v=py-iy
    wanted=set()
    for dx,dy in [(0,0),(1,0),(0,1),(1,1)]:
        wanted.update(zip(((ix+dx)//256).ravel(),(np.clip(iy+dy,0,256*2**zoom-1)//256).ravel()))
    arrays={};sources=[]
    for index,(x,y) in enumerate(sorted(wanted)):
        progress('elevation','Loading elevation tiles',index,len(wanted),'tiles')
        url=f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x%(2**zoom)}/{y}.png'
        raw,meta=fetch(url,cache);sources.append(meta)
        with Image.open(io.BytesIO(raw)) as image: a=np.asarray(image.convert('RGB'),dtype=float)
        arrays[(x,y)]=a[:,:,0]*256+a[:,:,1]+a[:,:,2]/256-32768
    progress('elevation','Sampling elevation grid',len(wanted),len(wanted),'tiles')
    def samples(dx,dy):
        gx=ix+dx;gy=np.clip(iy+dy,0,256*2**zoom-1);out=np.zeros(xx.shape)
        for (x,y),a in arrays.items():
            mask=(gx//256==x)&(gy//256==y);out[mask]=a[gy[mask]%256,gx[mask]%256]
        return out
    z=(1-u)*(1-v)*samples(0,0)+u*(1-v)*samples(1,0)+(1-u)*v*samples(0,1)+u*v*samples(1,1)
    return xs,ys,z,dict(provider='Terrain Tiles / Terrarium',attribution='Terrain Tiles: USGS, SRTM and other sources; tilezen.org/attribution',
                       zoom=zoom,grid=[cells+1,cells+1],tiles=sources,elevation_range_m=[float(z.min()),float(z.max())])
