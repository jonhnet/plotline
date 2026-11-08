#!/usr/bin/env python3
"""Freeze raw USGS 3DEP elevation samples; routine terrain builds are offline."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

SERVICE = 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer'


def save_snapshot(output, data, response, url):
    output = Path(output)
    sidecar = output.with_suffix('.source.json')
    if output.exists() or sidecar.exists():
        raise ValueError('Use a new filename; preserve existing snapshots')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    sidecar.write_text(json.dumps(dict(
        attribution='USGS National Map 3D Elevation Program (3DEP)',
        fetched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        service=SERVICE, request=url, response=response,
        sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
        horizontal_crs='EPSG:4326', elevation_units='metres',
        note='Raw F32 bare-earth elevation, bilinear resampling, rasterFunction=None. '
             'GeoTIFF samples are pixel centers, not bbox edges. Water datum and source resolution '
             'follow the USGS service; this is not bathymetry.'), indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--bounds', nargs=4, type=float, default=[-122.43, 47.58, -122.25, 47.70])
    parser.add_argument('--samples', type=int, default=257)
    args = parser.parse_args()
    params = dict(bbox=','.join(map(str, args.bounds)), bboxSR=4326, imageSR=4326,
                  size=f'{args.samples},{args.samples}', format='tiff', pixelType='F32',
                  interpolation='RSP_BilinearInterpolation', renderingRule=json.dumps({'rasterFunction': 'None'}),
                  adjustAspectRatio='false', f='json')
    url = SERVICE + '/exportImage?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=50) as response:
        metadata = json.load(response)
    if 'error' in metadata:
        raise ValueError(metadata['error'])
    with urllib.request.urlopen(metadata['href'], timeout=50) as response:
        data = response.read()
    save_snapshot(args.output, data, metadata, url)


if __name__ == '__main__':
    main()
