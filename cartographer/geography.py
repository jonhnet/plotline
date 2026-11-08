"""One orthographic camera and inverse viewport footprint for both 2D and relief."""
import math
import numpy as np
from shapely.geometry import box
from shapely.ops import transform
from .model import MM, layout

R = 6378137.0
WORLD = 2*math.pi*R
HALF = WORLD/2


def point(lon,lat):
    return R*math.radians(lon), R*math.log(math.tan(math.pi/4+math.radians(max(-85.05112878,min(85.05112878,lat)))/2))


def lonlat(x,y):
    return math.degrees(x/R), math.degrees(2*math.atan(math.exp(y/R))-math.pi/2)


def projection(recipe):
    az=math.radians(recipe['camera']['azimuth_deg'])
    el=math.radians(recipe['camera']['elevation_deg'] if recipe['camera']['relief'] else 90)
    return np.array([[math.cos(az),-math.sin(az),0],[-math.sin(el)*math.sin(az),-math.sin(el)*math.cos(az),-math.cos(el)]])


def origin(recipe):
    return np.array(point(*recipe['center']))


def latitude_factor(recipe):
    return math.cos(math.radians(recipe['center'][1]))


def framing(recipe,center_height=0):
    frame=np.array(layout(recipe)['frame_mm'])*MM
    scale=1000*MM*latitude_factor(recipe)/recipe['framing']['scale_denominator']
    offset=(frame[:2]+frame[2:])/2+np.array(recipe['framing']['offset_mm'])*MM
    offset-=projection(recipe)[:,2]*center_height*recipe['camera']['exaggeration']*scale
    return scale,offset


def ground_box(bounds):
    return box(*point(*bounds[:2]),*point(*bounds[2:]))


def geo_geometry(geometry,recipe):
    from shapely import affinity
    o=origin(recipe)
    def project(x,y,z=None):
        return np.radians(x)*R, R*np.log(np.tan(np.pi/4+np.radians(y)/2))
    return affinity.translate(transform(project,geometry),-o[0],-o[1])


def footprint(recipe,height_range=0):
    if recipe['extent']=='bounds':
        from shapely import affinity
        return affinity.translate(ground_box(recipe['bounds']),*-origin(recipe))
    scale,offset=framing(recipe)
    x0,y0,x1,y1=np.array(layout(recipe)['frame_mm'])*MM
    corners=np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])
    ground=(corners-offset) @ np.linalg.inv(projection(recipe)[:,:2]).T/scale
    lo,hi=ground.min(axis=0),ground.max(axis=0)
    # Width/label halo plus terrain displacement along the viewing direction.
    halo=max(4*MM/scale,.015*max(hi-lo))
    if recipe['camera']['relief']:
        halo+=height_range/latitude_factor(recipe)*recipe['camera']['exaggeration']/math.tan(math.radians(recipe['camera']['elevation_deg']))
    lo-=halo;hi+=halo
    o=origin(recipe)
    if hi[0]-lo[0]>WORLD or lo[1]+o[1]<-HALF or hi[1]+o[1]>HALF:
        raise ValueError('The viewport exceeds Web Mercator coverage. Zoom in or move away from the poles.')
    return box(*lo,*hi)


def bounds_for(domain,recipe):
    x0,y0,x1,y1=domain.bounds; ox,oy=origin(recipe)
    return [*lonlat(x0+ox,y0+oy),*lonlat(x1+ox,y1+oy)]
