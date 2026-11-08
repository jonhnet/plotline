"""Content-addressed build nodes and relocatable vector paint artifacts.

Node keys contain explicit settings and upstream identities, never the whole
recipe. Files use the geometry cache's process lock and atomic publication.
"""
import hashlib
import re
import time
import zlib

import numpy as np
import shapely

from .model import digest
from .sources import cached


# Private paint templates contain deferred ink operators as well as relocatable
# OCG resources. Substitute both in one pass, so new object numbers cannot be
# mistaken for old ones. Text and arbitrary imported PDF never enter this cache.
PAINT_REFERENCE = re.compile(rb'%PLINK (/PL[0-9]+) (RG|rg)\n|/PL[0-9]+(?=\s)')


def fingerprint(value):
    """Stable identities for geometry/arrays as well as ordinary recipe data."""
    def plain(v):
        if isinstance(v,shapely.Geometry):return {'wkb':hashlib.sha256(v.wkb).hexdigest(),'precision':float(shapely.get_precision(v))}
        if isinstance(v,np.ndarray):return {'array':hashlib.sha256(v.tobytes()).hexdigest(),'dtype':str(v.dtype),'shape':v.shape}
        if isinstance(v,dict):return {k:plain(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [plain(x) for x in v]
        return v
    return digest(plain(value))


class BuildCache:
    def __init__(self,directory,version,progress):
        self.directory=directory/'nodes';self.version=version;self.progress=progress;self.records=[]

    def node(self,name,dependencies,build,stage='drawing'):
        key=fingerprint(['map-build-node/2',self.version,name,dependencies]);built=False
        started=time.monotonic()
        def create():
            nonlocal built
            built=True;self.progress(stage,'Building '+name)
            start=time.monotonic();value=build()
            return dict(value=value,build_seconds=time.monotonic()-start)
        self.progress(stage,'Checking cached '+name)
        result=cached(self.directory,key,create,on_wait=lambda:self.progress(stage,'Waiting for shared '+name))
        elapsed=time.monotonic()-started
        record=dict(node=name,key=key,reused=not built,seconds=round(elapsed,4),build_seconds=round(result['build_seconds'],4))
        self.records.append(record)
        if not built:self.progress(stage,'Reusing '+name)
        return result['value']

    def paint(self,draw,counts,name,dependencies,build):
        """Replay path paint only; labels/collision state are composed afterward."""
        draw.commit() # Each block begins and ends with a balanced mask stack.
        def create():
            start=len(draw.streams);before=counts.copy();before_paints=draw.counts.copy()
            previous=draw.defer_path_colors;draw.defer_path_colors=True
            try:
                build();draw.commit()
            finally:
                draw.defer_path_colors=previous
            stream=b'\n'.join(draw.streams[start:]);del draw.streams[start:]
            if re.search(rb'(?<!\S)BT(?=\s)',stream):
                raise ValueError('Cached path paint must not contain text; place labels after replay')
            used=set(re.findall(rb'/PL[0-9]+(?=\s)',stream))
            layers=[(f'/PL{xref}'.encode(),key,draw.bindings[key]) for key,xref in draw.layers.items() if f'/PL{xref}'.encode() in used]
            artifact=dict(stream=zlib.compress(stream,3),layers=layers,counts=dict(counts-before),paints=dict(draw.counts-before_paints))
            counts.clear();counts.update(before);draw.counts.clear();draw.counts.update(before_paints)
            return artifact
        artifact=self.node(name,dependencies,create)
        substitutions={};colors={}
        for resource,key,binding in artifact['layers']:
            key,binding=draw.rebind_layer(key,binding)
            if key not in draw.layers:
                draw.layers[key]=draw.doc.add_ocg(key);draw.bindings[key]=binding
                draw.properties[f'PL{draw.layers[key]}']=draw.layers[key]
            substitutions[resource]=f'/PL{draw.layers[key]}'.encode()
            colors[resource]={op:draw.color_code(binding['ink_id'],op) for op in (b'RG',b'rg')}
        def substitute(match):
            resource,operator=match.groups()
            return colors[resource][operator] if resource else substitutions[match.group()]
        stream=PAINT_REFERENCE.sub(substitute,zlib.decompress(artifact['stream']))
        if stream:draw.streams.append(stream)
        counts.update(artifact['counts']);draw.counts.update(artifact['paints'])

    def summary(self):
        return dict(nodes=self.records,reused=sum(r['reused'] for r in self.records),
                    built=sum(not r['reused'] for r in self.records),
                    reused_build_seconds=round(sum(r['build_seconds'] for r in self.records if r['reused']),3))
