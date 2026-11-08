"""Offline acceleration and carriage-travel experiments with whole-family folds.

Uses the frozen November 6 review inputs, not a changing live spool selection.
No serial access, service changes or automatic model promotion.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from dataclasses import dataclass
import hashlib
import html
import json
import multiprocessing
from pathlib import Path
import time

import numpy as np
from scipy.optimize import least_squares

from .evaluate import completion, difference
from .model import (PARAMETERS, Capture, command_costs, load_capture, packetize,
                    pipeline)


FAMILIES = {
    'maps': ['88cb8e31', 'd01d3b55', 'aada5cc2'],
    'screened-images': ['e1cbe1f9', '16682ad0', '6c47f040'],
    'line-art': ['e0fc70ea'],
    'sierpinski': ['6126a1d3', 'b58f9b2c'],
    'branching': ['1cda0652'],
    'hilbert': ['e97282fe'],
    'calibration': ['f7421d34', 'a2380177', 'db4be81e'],
}
VARIANTS = ('refit', 'acceleration', 'carriage', 'both')


def carriage_distances(source, y_min, units=40.):
    """Across-sheet travel proxy at SP, in metres, not measured carriage paths.

    The HP758X diagram puts the carousel on the -Y side. Reference its side of
    the observed drawable window. An exchange has two legs; SP0 has one. The
    unrecorded off-paper distance is left to the fixed overhead. First pickup
    has unknown carriage position and contributes no distance, as in v1.
    """
    if not np.isfinite(y_min) or not np.isfinite(units) or units <= 0:
        raise ValueError('Need a finite observed Y limit and positive units')
    position = None
    slot = 0
    indices, metres = [], []
    for index, raw in enumerate(source.splitlines()):
        op, arg = raw[:2], raw[2:-1]
        if op in (b'PU', b'PD') and arg:
            position = float(arg.split(b',')[1])
        elif op == b'SP':
            new_slot = int(arg)
            if new_slot != slot and position is not None:
                distance = position-y_min
                if distance < -1:
                    raise ValueError('Pen position lies outside observed carousel-side limit')
                indices.append(index)
                legs = 2 if slot and new_slot else 1
                metres.append(max(0., distance)/units/1000 * legs)
            slot = new_slot
    return np.asarray(indices, dtype=np.int64), np.asarray(metres, dtype=float)


@dataclass
class Drawing:
    capture: Capture
    family: str
    name: str
    finish: dict
    travel_indices: np.ndarray
    travel_metres: np.ndarray
    gaps: np.ndarray
    left: np.ndarray
    right: np.ndarray
    y_min: float = 0.


def simulate(drawing, parameters, carriage_s_m=0.):
    if not np.isfinite(carriage_s_m) or carriage_s_m < 0:
        raise ValueError('Carriage time must be finite and nonnegative')
    c = drawing.capture
    p = c.program
    costs = command_costs(np.asarray(parameters), p.lengths, p.down, p.speeds,
                          p.radius, p.move_index, p.transitions, p.selections)
    costs[drawing.travel_indices] += carriage_s_m*drawing.travel_metres
    return pipeline(costs, p.ends, drawing.gaps, parameters[6], c.byte_rate)


def load_drawings(snapshot, spool, turnaround):
    drawings = []
    for row in snapshot['plots']:
        aid = row['attempt']
        families = [name for name, ids in FAMILIES.items() if aid[:8] in ids]
        if len(families) != 1:
            raise ValueError('Assign an explicit drawing family to '+aid)
        c = load_capture(Path(spool)/aid)
        if c.program.sha256 != row['source_sha256'] or c.trace_sha256 != row['trace_sha256']:
            raise ValueError('Frozen capture hash mismatch: '+aid)
        if (c.summary['injected_writes'] != c.summary['completion_queries']
                or c.summary['acknowledged_prepared_bytes'] != len(c.program.source)):
            raise ValueError('Need an uninterrupted complete trace')
        record = json.loads((c.directory/'record.json').read_text())
        limit = record['observation']['limits'][1]
        indices, metres = carriage_distances(c.program.source, limit,
                                            c.header['metadata']['units_per_mm'])
        packet_ids, gaps = packetize(c.program, turnaround)
        if not np.array_equal(packet_ids, c.packet_indices):
            raise ValueError('Recorded packetization differs from forecast')
        # Non-overlapping ~16 KiB sections, omitting the unknown initial move.
        # No internal audit split: the entire omitted family supplies validation.
        left = np.arange(32, len(packet_ids)-1, 32)
        right = np.minimum(left+32, len(packet_ids)-1)
        drawings.append(Drawing(c, families[0], row['name'], completion(c.directory),
                                indices, metres, gaps, left, right, float(limit)))
    return drawings


def residuals(drawings, parameters, carriage_s_m):
    """Equal families, equal drawings; whole duration + local relative errors.

    Each long drawing assigns half its squared weight to its whole duration,
    half to the mean squared section error. Short sheets use duration alone.
    A 10-second denominator floor avoids amplifying tiny intervals. These
    weights/bounds are fixed for every candidate and fold, not tuned to results.
    """
    counts = {name:sum(d.family == name for d in drawings) for name in FAMILIES}
    result = []
    for d in drawings:
        incoming, done = simulate(d, parameters, carriage_s_m)
        observed = d.finish['OS']['seconds'] if d.finish else d.capture.drain[-1]
        predicted = done[-1] if d.finish else incoming[-1]
        weight = 1/np.sqrt(counts[d.family])
        if len(d.left):
            weight /= np.sqrt(2)
        result.append(np.array([(predicted-observed)/max(10., observed)*weight]))
        if len(d.left):
            curve = incoming[d.capture.packet_indices]
            actual = d.capture.drain[d.right]-d.capture.drain[d.left]
            delta = curve[d.right]-curve[d.left]-actual
            result.append(delta/np.maximum(10., actual)*weight/np.sqrt(len(actual)))
    return np.concatenate(result)


def fit(drawings, initial, variant, free_base=(0, 1, 3, 4, 5)):
    acceleration = variant in ('acceleration', 'both')
    carriage = variant in ('carriage', 'both')
    free = list(free_base) + ([2] if acceleration else [])
    lower = np.log(np.array([.2,10.,500.,.00001,.01,.00001,128.])[free])
    upper = np.log(np.array([2.,1000.,1e7,.3,15.,.06,4096.])[free])
    if carriage:
        lower = np.r_[lower,0.]; upper = np.r_[upper,100.]
    profiles = []
    family_count = len({d.family for d in drawings})

    def objective(residual):
        return float(np.sum(residual**2)/family_count)

    def optimize(start, travel):
        def unpack(theta):
            p = np.asarray(initial, dtype=float).copy()
            p[free] = np.exp(theta[:len(free)])
            return p, float(theta[-1]) if carriage else 0.
        x = np.log(np.asarray(start)[free])
        if carriage: x = np.r_[x,travel]
        def fun(theta):
            p, coefficient = unpack(theta)
            return residuals(drawings, p, coefficient)
        if not len(x):
            profiles.append(dict(initial_acceleration=float(start[2]),
                                 objective=objective(fun(x)), nfev=0, status=0,
                                 parameters=dict(zip(PARAMETERS,np.asarray(initial).tolist())),
                                 carriage_s_m=0.))
            return
        result = least_squares(fun, np.clip(x,lower+1e-8,upper-1e-8), bounds=(lower,upper),
                               diff_step=1e-4, max_nfev=80, ftol=1e-7, xtol=1e-7, gtol=1e-7)
        p, coefficient = unpack(result.x)
        profiles.append(dict(initial_acceleration=float(start[2]),
                             objective=objective(result.fun), nfev=result.nfev, status=result.status,
                             parameters=dict(zip(PARAMETERS,p.tolist())),
                             carriage_s_m=coefficient))

    if acceleration:
        # Multiple finite starts avoid the flat no-correction plateau. Preserve
        # the nested off case explicitly, rather than forcing finite acceleration.
        for a in (2000.,10000.,100000.):
            start = np.asarray(initial).copy(); start[2] = a
            optimize(start,2.)
        nested = fit(drawings, initial, 'carriage' if carriage else 'refit',free_base=free_base)
        profiles.append(dict(nested_variant=nested['variant'], **{k:nested[k] for k in
                            ('objective','parameters','carriage_s_m')}))
    else:
        optimize(initial,2.)
    if carriage:
        nested = fit(drawings,initial,'acceleration' if acceleration else 'refit',free_base=free_base)
        profiles.append(dict(nested_variant=nested['variant'], **{k:nested[k] for k in
                            ('objective','parameters','carriage_s_m')}))
    best = min(profiles, key=lambda p:p['objective'])
    return dict(variant=variant, parameters=best['parameters'], carriage_s_m=best['carriage_s_m'],
                objective=best['objective'], starts=profiles,
                free_base_parameters=[PARAMETERS[i] for i in free_base],
                training_attempts=[d.capture.directory.name for d in drawings])


def score(drawing, parameters, carriage_s_m):
    incoming, done = simulate(drawing, parameters, carriage_s_m)
    result = dict(feed=difference(incoming[-1],drawing.capture.drain[-1]))
    if drawing.finish:
        result['completion'] = difference(done[-1],drawing.finish['OS']['seconds'])
    if len(drawing.left):
        curve = incoming[drawing.capture.packet_indices]
        actual = drawing.capture.drain[drawing.right]-drawing.capture.drain[drawing.left]
        delta = curve[drawing.right]-curve[drawing.left]-actual
        result['section_rms_relative'] = float(np.sqrt(np.mean((delta/np.maximum(10.,actual))**2)))
    return result


# Fork workers inherit immutable parsed captures, avoiding repeated large JSON
# reads. This offline Linux development command has no runtime dependency.
_DRAWINGS = []
_INITIAL = None


def fold(omitted):
    training = [d for d in _DRAWINGS if d.family != omitted]
    testing = [d for d in _DRAWINGS if omitted is None or d.family == omitted]
    start = time.monotonic()
    fits = []
    baseline = fit(training, _INITIAL, 'refit')
    initial = np.array([baseline['parameters'][n] for n in PARAMETERS])
    for variant in VARIANTS:
        fitted = baseline if variant == 'refit' else fit(training, initial, variant)
        p = np.array([fitted['parameters'][n] for n in PARAMETERS])
        fitted['scores'] = {d.capture.directory.name:score(d,p,fitted['carriage_s_m']) for d in testing}
        fits.append(fitted)
    # Follow-up checks hold unrelated v1 parameters fixed. They distinguish an
    # unhelpful new term from instability introduced by refitting everything.
    restricted = []
    for name,variant,free in [('acceleration-only','acceleration',()),
                              ('pen-overhead-only','refit',(4,)),
                              ('pen-overhead-and-carriage','carriage',(4,))]:
        fitted = fit(training,_INITIAL,variant,free_base=free)
        fitted['variant'] = name
        p = np.array([fitted['parameters'][n] for n in PARAMETERS])
        fitted['scores'] = {d.capture.directory.name:score(d,p,fitted['carriage_s_m']) for d in testing}
        restricted.append(fitted)
    v1_objective=float(np.sum(residuals(training,_INITIAL,0.)**2)/len({d.family for d in training}))
    return dict(omitted_family=omitted, fits=fits, restricted=restricted,v1_objective=v1_objective,
                elapsed_s=time.monotonic()-start)


def run(args):
    global _DRAWINGS, _INITIAL
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    snapshot_path = Path(args.snapshot); snapshot = json.loads(snapshot_path.read_text())
    model_path = Path(snapshot['model']); model = json.loads(model_path.read_text())
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != snapshot['model_sha256']:
        raise ValueError('Deployed model changed since the frozen review')
    _INITIAL = np.array([model['parameters'][n] for n in PARAMETERS])
    _DRAWINGS = load_drawings(snapshot,args.spool,model['host_turnaround_s'])
    for d in _DRAWINGS:
        print(f'Loaded {d.family}: {d.name}, carriage proxy {d.travel_metres.sum():.3f} m',flush=True)
    result = dict(schema='plotline.timing-extensions.v1',
                  snapshot=str(snapshot_path), snapshot_sha256=hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
                  model_sha256=snapshot['model_sha256'], families=FAMILIES,
                  protocol=dict(buffer_bytes=float(_INITIAL[6]),host_turnaround_s=model['host_turnaround_s'],
                                section_packets=32,denominator_floor_s=10.,
                                long_drawing_whole_duration_weight=.5,
                                finite_acceleration_starts=[2000.,10000.,100000.],
                                acceleration_bounds=[500.,1e7],carriage_bounds_s_m=[0.,100.],
                                multistart_selection='training objective only; includes nested off case',
                                restricted_checks='Added after broad-refit results; freeze unrelated v1 coefficients'),
                  method='Exploratory leave-one-family-out; fixed 1 KiB buffer and sender turnaround; equal-family duration and section residuals; frozen before running',
                  plots=[dict(attempt=d.capture.directory.name,name=d.name,family=d.family,
                              travel_metres=float(d.travel_metres.sum()),
                              carriage_reference_y_units=d.y_min,
                              units_per_mm=d.capture.header['metadata']['units_per_mm'],
                              source_sha256=d.capture.program.sha256,trace_sha256=d.capture.trace_sha256,
                              v1=score(d,_INITIAL,0.)) for d in _DRAWINGS], folds=[])
    folds = [*FAMILIES,None] if args.fold == 'all' else [None if args.fold=='pooled' else args.fold]
    def save():
        result['folds'].sort(key=lambda f:f['omitted_family'] or '~pooled')
        (output/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    save()
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('fork')) as pool:
        futures = {pool.submit(fold,omitted):omitted for omitted in folds}
        for future in as_completed(futures):
            record = future.result(); result['folds'].append(record); save()
            print(json.dumps({'completed':record['omitted_family'] or 'pooled','seconds':record['elapsed_s'],
                              'parameters':{f['variant']:{**f['parameters'],'carriage_s_m':f['carriage_s_m']}
                                            for f in record['fits']}}),flush=True)
    result['validation'] = validation(result)
    save()
    (output/'snapshot.json').write_text(json.dumps(compact_result(result),indent=2,allow_nan=False)+'\n')
    report(output,result)
    print('Saved '+str(output/'results.json'),flush=True)


def compact_result(result):
    frozen = {k:v for k,v in result.items() if k != 'folds'}
    frozen['folds'] = [{k:v for k,v in f.items() if k not in ('fits','restricted','elapsed_s')}
                      | {key:[{k:v for k,v in fit.items() if k != 'starts'} for fit in f[key]]
                         for key in ('fits','restricted')} for f in result['folds']]
    return frozen


def heldout(result):
    scored=[]
    for row in result['plots']:
        fold_result=next((f for f in result['folds'] if f['omitted_family']==row['family']),None)
        if fold_result is None: continue
        scores={'v1':row['v1']} | {f['variant']:f['scores'][row['attempt']]
                                  for f in [*fold_result['fits'],*fold_result['restricted']]}
        scored.append((row,{k:s.get('completion',s['feed']) for k,s in scores.items()}))
    return scored


def validation(result):
    scored=heldout(result)
    if not scored:return {}
    totals={}
    for variant in scored[0][1]:
        totals[variant]={}
        for category in ('artwork','calibration'):
            rows=[(row,metrics[variant]) for row,metrics in scored
                  if (row['family']=='calibration') == (category=='calibration')]
            if not rows:continue
            errors=[abs(m['error_percent']) for _,m in rows]
            families={row['family'] for row,_ in rows}
            totals[variant][category]=dict(count=len(rows),mean_absolute_percent=float(np.mean(errors)),
                median_absolute_percent=float(np.median(errors)),
                family_mean_absolute_percent=float(np.mean([
                    np.mean([abs(m['error_percent']) for r,m in rows if r['family']==family])
                    for family in families])))
    return totals


def report(output,result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    scored = heldout(result)
    if not scored: return
    fig,ax = plt.subplots(figsize=(11,8),constrained_layout=True)
    styles = [('v1','#172b30','o'),('refit','#999999','x'),('acceleration','#367db2','^'),
              ('carriage','#bc7925','s'),('both','#9c529c','D')]
    y = np.arange(len(scored))
    for i,(name,color,marker) in enumerate(styles):
        ax.scatter([m[name]['error_percent'] for _,m in scored],y+(i-2)*.12,
                   label=name,color=color,marker=marker,s=25)
    ax.axvline(0,color='#172b30',linewidth=.7)
    ax.set_yticks(y,[r['name'].replace(' · page 1.hpgl','').removesuffix('.hpgl')[:42]
                    +' · '+r['attempt'][:8] for r,_ in scored])
    ax.invert_yaxis();ax.grid(axis='x',alpha=.2)
    ax.set(xlabel='Duration error (%) · positive = overestimate',
           title='Each family excluded from its refit · previously examined data')
    ax.legend(loc='best',fontsize=9)
    fig.savefig(output/'comparison.png',dpi=160);fig.savefig(output/'comparison.svg');plt.close(fig)
    rows=[]
    restricted_rows=[]
    restricted_names=('v1','acceleration-only','pen-overhead-only','pen-overhead-and-carriage')
    csv_rows=[]
    for row,metrics in scored:
        cells=''.join(f'<td>{metrics[k]["error_s"]:+.1f}s ({metrics[k]["error_percent"]:+.2f}%)</td>'
                      for k,_,_ in styles)
        rows.append('<tr><td>'+html.escape(row['name'])+'</td><td>'+row['family']+'</td>'+cells+'</tr>')
        cells=''.join(f'<td>{metrics[k]["error_s"]:+.1f}s ({metrics[k]["error_percent"]:+.2f}%)</td>'
                      for k in restricted_names)
        restricted_rows.append('<tr><td>'+html.escape(row['name'])+'</td>'+cells+'</tr>')
        for variant,m in metrics.items():
            csv_rows.append(dict(attempt=row['attempt'],name=row['name'],family=row['family'],variant=variant,**m))
    with (output/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
    totals=validation(result)
    summary_rows=[]
    for variant,_,_ in styles:
        values=totals[variant]
        cells=''.join(f'<td>{values[k]["mean_absolute_percent"]:.2f}%</td>' if k in values
                      else '<td>—</td>' for k in ('artwork','calibration'))
        summary_rows.append('<tr><td>'+variant+'</td>'+cells+'</tr>')
    (output/'index.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>HP7585 timing extensions</title>
<style>body{{font:16px/1.5 system-ui;color:#172b30;max-width:1400px;margin:40px auto;padding:0 20px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #ddd}}img{{width:100%}}</style>
<h1>Acceleration and carriage-travel experiments</h1>
<p>Each entire drawing family is omitted from its refit. These plots have been examined previously, so this is exploratory validation, not fresh prospective evidence. V1 used Snoqualmie in its original calibration. Buffer and sender turnaround are fixed; all refits use the same equal-family objective. The carousel distance is a geometric proxy, not an observed mechanism path. No runtime changes.</p>
<h2>Mean absolute duration error</h2><p>Artwork and short calibration sheets are shown separately. The per-drawing measurements use OA/OS-confirmed completion where available; Snoqualmie and Ravenna use feeding duration only.</p>
<table><tr><th>Model</th><th>Artwork</th><th>Calibration</th></tr>{''.join(summary_rows)}</table>
<h2>Individual drawings</h2>
<table><tr><th>Drawing</th><th>Omitted family</th>{''.join('<th>'+k+'</th>' for k,_,_ in styles)}</tr>{''.join(rows)}</table>
<img src="comparison.png" alt="Signed duration errors for each held-out drawing and model">
<h2>Restricted follow-up fits</h2><p>These additional checks freeze unrelated v1 coefficients. Acceleration-only fits just acceleration; pen-overhead-only fits the existing fixed pen-change time; pen-overhead-and-carriage fits that overhead plus the distance term. They use the same excluded families and objective, and were added after inspecting the broader refits.</p>
<table><tr><th>Drawing</th>{''.join('<th>'+k+'</th>' for k in restricted_names)}</tr>{''.join(restricted_rows)}</table>
<p><a href="results.json">Full coefficients, folds, objective values and hashes</a> · <a href="comparison.csv">CSV</a> · <a href="snapshot.json">Compact frozen evidence</a> · <a href="comparison.svg">SVG chart</a></p>''')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',default='controller/docs/timing-validation-2026-11-06.json')
    parser.add_argument('--spool',default='controller/state/jobs/plots')
    parser.add_argument('--output',required=True)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--fold',choices=['all','pooled',*FAMILIES],default='all')
    run(parser.parse_args())
