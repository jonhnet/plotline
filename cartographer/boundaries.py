"""Explicit US Census boundary search; snapshots travel with the recipe."""
import json
import re
from urllib.parse import urlencode
from shapely.geometry import shape,mapping
from .sources import fetch
from .model import digest

SERVICES={'state':('State_County',0),'county':('State_County',1),'city':('Places_CouSub_ConCity_SubMCD',4)}
BASE='https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/'


def query(kind,parameters,cache):
    if kind not in SERVICES: raise ValueError('Choose city, county or state')
    service,layer=SERVICES[kind]
    url=f'{BASE}{service}/MapServer/{layer}/query?'+urlencode(parameters)
    raw,_=fetch(url,cache,8_000_000);value=json.loads(raw)
    if value.get('error'): raise ValueError('Census boundary service: '+value['error'].get('message','query failed'))
    return value,url


def search(name,kind,cache):
    if not isinstance(name,str) or not 2<=len(name.strip())<=80: raise ValueError('Enter at least two characters of the boundary name')
    # Match literal text; do not pass SQL wildcards/operators from user input.
    name=name.strip().replace("'","''").replace('%','').replace('_','')
    value,_=query(kind,dict(where=f"UPPER(NAME) LIKE '%{name.upper()}%'",outFields='NAME,GEOID,STATE,CENTLAT,CENTLON',
                 returnGeometry='false',orderByFields='NAME',resultRecordCount=30,f='json'),cache)
    states,_=query('state',dict(where='1=1',outFields='STATE,STUSAB',returnGeometry='false',resultRecordCount=100,f='json'),cache)
    abbreviations={f['attributes']['STATE']:f['attributes']['STUSAB'] for f in states.get('features',[])}
    return [dict(name=f['attributes']['NAME'],id=f['attributes']['GEOID'],kind=kind,state=abbreviations.get(f['attributes']['STATE'],f['attributes']['STATE']),
                 center=[float(f['attributes']['CENTLON']),float(f['attributes']['CENTLAT'])]) for f in value.get('features',[])]


def select(kind,identity,cache):
    if not isinstance(identity,str) or not re.fullmatch('[0-9]{2,10}',identity):raise ValueError('Invalid Census geographic identifier')
    value,url=query(kind,dict(where=f"GEOID='{identity}'",outFields='NAME,GEOID',returnGeometry='true',outSR=4326,f='geojson'),cache)
    features=value.get('features',[])
    if len(features)!=1:raise ValueError('Boundary was not found or is ambiguous')
    return snapshot(features[0]['geometry'],features[0]['properties']['NAME'],'US Census TIGERweb; '+url)


def snapshot(geometry,name,source):
    g=shape(geometry)
    if g.geom_type not in ('Polygon','MultiPolygon') or g.is_empty or not g.is_valid:
        raise ValueError('Import a valid GeoJSON Polygon or MultiPolygon')
    geometry=json.loads(json.dumps(mapping(g)))
    return dict(name=name,geometry=geometry,source=source,sha256=digest(geometry))
