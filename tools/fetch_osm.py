#!/usr/bin/env python3
"""Explicitly fetch a small OSM bbox and complete its multipolygon ways; freeze it."""
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET


def fetch(bounds, output):
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -85 < south < north < 85) or (east-west)*(north-south) > .02:
        raise ValueError('Use a small neighborhood bbox (at most .02 square degrees)')
    output = Path(output)
    if not output.name.endswith('.osm.gz'):
        raise ValueError('Snapshot filename must end in .osm.gz')
    if output.exists():
        raise ValueError('Refusing to replace an existing snapshot; choose a new filename')
    requests = []
    def get(endpoint):
        url = 'https://api.openstreetmap.org/api/0.6/'+endpoint
        request = urllib.request.Request(url, headers={'User-Agent':'Plotline/0.1 (local vector map development)'})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        requests.append(dict(url=url, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
        return ET.fromstring(data)
    root = get('map?bbox='+','.join(map(str,bounds)))
    elements = {(e.tag,e.get('id')): e for e in root if e.get('id')}
    for relation in list(root.findall('relation')):
        t = {e.get('k'):e.get('v') for e in relation.findall('tag')}
        if t.get('type') != 'multipolygon':
            continue
        if any((e.get('type'),e.get('ref')) not in elements for e in relation.findall('member')):
            extra = get('relation/'+relation.get('id')+'/full')
            for element in extra:
                key = element.tag, element.get('id')
                if key[1] and key not in elements:
                    root.append(element)
                    elements[key] = element
    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    metadata = dict(bbox=bounds, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), requests=requests,
                    fetched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    attribution='OpenStreetMap contributors', license='ODbL 1.0',
                    copyright_url='https://www.openstreetmap.org/copyright',
                    note='Current API neighborhood snapshot with missing multipolygon members fetched; not the missing historical Roosevelt input.')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(gzip.compress(data,mtime=0))
    output.with_name(output.name.removesuffix('.osm.gz')+'.source.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='New .osm.gz snapshot filename')
    parser.add_argument('--bbox',nargs=4,type=float,required=True,metavar=('WEST','SOUTH','EAST','NORTH'))
    args = parser.parse_args()
    fetch(args.bbox,args.output)
