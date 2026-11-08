"""Offline audit of the frozen Washington fast-rectangle-clipping experiment.

Run after both original experiment HPGL files exist; see
docs/map-preparation-performance-03.md. Never connects to the plotter.
"""
import json, math
import argparse
from pathlib import Path
from collections import Counter
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
REPO=parser.parse_args().repo.resolve()
root=REPO/'build/prepare-perf-washington-2'
out=REPO/'build/prepare-perf-washington-3'
out.mkdir(parents=True,exist_ok=True)

def read(name):
    result=Counter(); pos=(0,0); pen=0
    for raw in (root/name/'plot.hpgl').read_text().split(';'):
        op=raw.strip()[:2]; args=raw.strip()[2:]
        if op=='SP': pen=int(args)
        elif op in ('PU','PD') and args:
            nums=[int(x) for x in args.split(',')]
            for i in range(0,len(nums),2):
                point=tuple(nums[i:i+2])
                if op=='PD' and pos!=point:
                    a,b=sorted((pos,point)); result[pen,a,b]+=1
                pos=point
    return result

a=read('baseline');b=read('candidate-8')
removed=a-b;added=b-a
result={'removed':[list(k)+[v] for k,v in removed.items()], 'added':[list(k)+[v] for k,v in added.items()]}
(out/'clip-segment-delta.json').write_text(json.dumps(result,indent=2)+'\n')
for label,c in [('baseline',a),('fast_clip',b),('removed',removed),('added',added)]:
    lengths={}
    for (pen,p,q),n in c.items():lengths[pen]=lengths.get(pen,0)+math.dist(p,q)*n/40
    print(label,'segments',sum(c.values()),'length_mm',lengths)
for name in ('baseline','candidate-8'):
    s=json.loads((root/name/'summary.json').read_text())
    print(name, s['metrics'])
    if name=='baseline': base=s
    else:
        for key,value in base['by_feature'].items():
            if value!=s['by_feature'][key]:print('changed_feature',key,value,s['by_feature'][key])
