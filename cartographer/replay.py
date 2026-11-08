"""Recompose a downloaded bundle using its verified input tiles, without fetching."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from .compose import compose
from .model import validate_recipe


def replay(bundle,output,cache):
    with zipfile.ZipFile(bundle) as archive:
        recipe=json.loads(archive.read('recipe.json'));validate_recipe(recipe)
        provenance=json.loads(archive.read('provenance.json'))
        inputs=provenance['inputs']['map'].get('tiles',[])+(provenance['inputs'].get('elevation') or {}).get('tiles',[])
        verified={}
        for source in inputs:
            key=hashlib.sha256(source['url'].encode()).hexdigest()
            if source['cache_key']!=key:raise ValueError('Invalid source cache key')
            info=archive.getinfo('sources/'+key)
            if info.file_size>50_000_000:raise ValueError('Source exceeds tile budget')
            raw=archive.read(info)
            if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('Source tile checksum mismatch')
            verified[key]=raw
    # Read-only mapping is injected for acquisition. A missing tile is an error,
    # never a request to a newer provider revision during offline replay.
    from unittest.mock import patch
    def frozen(url,*args,**kwargs):
        key=hashlib.sha256(url.encode()).hexdigest()
        if key not in verified:raise ValueError('The bundle does not include a required source tile')
        raw=verified[key]
        directory=Path(cache)/'http';directory.mkdir(parents=True,exist_ok=True)
        (directory/key).write_bytes(raw)
        return raw,dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),cache_key=key)
    with patch('cartographer.sources.fetch',side_effect=frozen):
        # Prepopulate for decoded geometry cache hits and bundle reconstruction.
        for source in inputs:frozen(source['url'])
        return compose(recipe,output,provenance['quality'],cache)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--cache',type=Path,default=Path('build/cartographer-replay'))
    args=parser.parse_args();result=replay(args.bundle,args.output,args.cache)
    print(json.dumps(dict(source_sha256=result['source_sha256'],seconds=result['seconds'])))
