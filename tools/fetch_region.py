#!/usr/bin/env python3
"""Freeze public Seattle GIS layers for the regional map prototypes."""
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

BASE = 'https://services.arcgis.com/ZOyb2t4B0UYuYNYH/arcgis/rest/services/'
LAYERS = {
    'landwater': ('Seattle_and_Vicinity/FeatureServer/0', '1=1', 'OBJECTID,TYPE,POLYGON,FEATURE'),
    'districts': ('Seattle_City_Council_Districts_including_Water/FeatureServer/1', '1=1', '*'),
    'parks': ('Park_Boundaries/FeatureServer/1', '1=1', 'OBJECTID,NAME,PMA'),
    'streets': ('Seattle_Streets_1/FeatureServer/0', 'ARTCLASS IN (1,2,3,4,5,9)',
                'OBJECTID,STNAME_ORD,ARTCLASS,ARTDESCRIPT,STREETTYPE,OWNER'),
}


def fetch(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new directory; existing snapshots are preserved')
    output.mkdir(parents=True)
    manifest = dict(fetched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    attribution='City of Seattle; Seattle Department of Transportation; Seattle Parks and Recreation',
                    bounds=[-122.49,47.48,-122.20,47.76], layers={},
                    note='Public agency cartographic inputs. City mask is the union of the seven 2024 council districts, including water. Not an OSM snapshot or a survey.')
    for name,(path,where,fields) in LAYERS.items():
        features, requests = [], []
        offset = 0
        while True:
            params = dict(where=where, outFields=fields, outSR=4326, f='geojson',
                          geometry=','.join(map(str,manifest['bounds'])), inSR=4326,
                          geometryType='esriGeometryEnvelope', spatialRel='esriSpatialRelIntersects',
                          resultOffset=offset, resultRecordCount=2000, orderByFields='OBJECTID')
            url = BASE+path+'/query?'+urllib.parse.urlencode(params)
            req = urllib.request.Request(url,headers={'User-Agent':'Plotline/0.1 (local map prototype)'})
            with urllib.request.urlopen(req,timeout=50) as response:
                data = response.read()
            page = json.loads(data)
            if 'error' in page:
                raise ValueError(page['error'])
            batch = page['features']
            features.extend(batch)
            requests.append(dict(url=url,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)))
            if len(batch)<2000 and not page.get('exceededTransferLimit'):
                break
            if not batch:
                raise ValueError('Pagination made no progress')
            offset += len(batch)
        ids = [f['properties']['OBJECTID'] for f in features]
        if len(ids)!=len(set(ids)):
            raise ValueError('Duplicate records across pages')
        data = json.dumps(dict(type='FeatureCollection',features=features),separators=(',',':')).encode()
        filename = name+'.geojson.gz'
        (output/filename).write_bytes(gzip.compress(data,mtime=0))
        manifest['layers'][name] = dict(file=filename,features=len(features),sha256=hashlib.sha256(data).hexdigest(),
                                        service=BASE+path,requests=requests)
        print(name,len(features),len(data),flush=True)
    (output/'source.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',help='New snapshot directory')
    fetch(parser.parse_args().output)
