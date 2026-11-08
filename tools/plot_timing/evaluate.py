"""Compare complete captures with v1; refit with whole drawings left out.

No device access, service changes, or automatic model promotion.
"""
from .model import read_retained
import argparse
import hashlib
import html
import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from .fit import metrics, predict, windows
from .model import PARAMETERS, load_capture, simulate


def completion(directory):
    """Only complete, recorded OA/OS replies count; keep all times on dev's clock."""
    active, reply = None, bytearray()
    result, queries = {}, []
    prepared_ack = None
    pending_kind = None
    for line in read_retained(directory / 'serial-timing.jsonl').decode().splitlines():
        event = json.loads(line)
        if event['event'] == 'write':
            pending_kind = event['kind']
            if pending_kind == 'completion-query':
                active = bytes(event['data']).decode('ascii').strip().rstrip(';')
                queries.append(active)
                reply.clear()
        elif event['event'] == 'reply':
            if event['op'] == 'write' and pending_kind == 'prepared':
                prepared_ack = event['host_elapsed_ns'] / 1e9
            if event['op'] == 'read' and active:
                reply.extend(event['result']['data'])
                value = bytes(reply).lstrip(b'\r\n')
                ends = [i for i,b in enumerate(value) if b in b'\r\n']
                if ends:
                    end = ends[0]
                    if any(b not in b'\r\n' for b in value[end:]):
                        raise ValueError('Extra completion reply')
                    result[active] = dict(seconds=event['host_elapsed_ns'] / 1e9,
                                          reply=value[:end].decode('ascii').strip())
                    active = None
    if queries:
        record = json.loads((directory / 'record.json').read_text())
        evidence = record.get('execution', {}).get('completion', {})
        if queries != ['OA','OS'] or evidence.get('method') != 'hpgl-oa-os' or not all(k in result for k in ('OA', 'OS')):
            raise ValueError('Completion queries lack confirmed OA/OS evidence')
        position = [int(v) for v in result['OA']['reply'].split(',')]
        status = int(result['OS']['reply'])
        if (len(position)!=3 or position[2]!=0 or any(abs(v)>32767 for v in position[:2])
                or not 0<=status<=255 or status&33 or not status&16
                or evidence.get('position')!=position[:2] or evidence.get('status')!=status):
            raise ValueError('Completion replies disagree with recorded confirmation')
        if prepared_ack is None or not prepared_ack<=result['OA']['seconds']<=result['OS']['seconds']:
            raise ValueError('Invalid completion timestamp order')
        result['final_prepared_ack_s'] = prepared_ack
        result['tail_to_oa_s'] = result['OA']['seconds'] - prepared_ack
        result['tail_to_os_s'] = result['OS']['seconds'] - prepared_ack
    return result


def difference(predicted, actual):
    return dict(actual_s=float(actual), predicted_s=float(predicted),
                error_s=float(predicted-actual), error_percent=float(100*(predicted/actual-1)))


def score(capture, parameters, turnaround, finish):
    incoming, done, packet_ids = predict(parameters, capture.program, turnaround, capture.byte_rate)
    if not np.array_equal(packet_ids, capture.packet_indices):
        raise ValueError('Feeder packetization changed')
    curve = incoming[packet_ids]
    sections = {}
    try:
        left, right, split = windows(capture)
    except ValueError as error:
        # Short jobs still have useful whole-plot evidence. They cannot supply
        # the original guarded calibration/selection/audit split.
        section_note = str(error)
    else:
        expected, actual = curve[right]-curve[left], capture.drain[right]-capture.drain[left]
        sections = {s:metrics(actual[split==s],expected[split==s])
                    for s in ('calibration','selection','audit')}
        section_note = None
    # Remaining-time error after each acknowledgement: total prediction error
    # minus cumulative delivery error. OS time is an observed reply, including
    # short query/serial latency; it is not a direct sensor of pen parking.
    result = dict(feed=difference(incoming[-1], capture.drain[-1]),
                  predicted_motion_s=float(done[-1]), predicted_tail_s=float(done[-1]-incoming[-1]),
                  sections=sections, section_note=section_note)
    if finish:
        result['completion'] = difference(done[-1], finish['OS']['seconds'])
        remaining_error = (done[-1]-curve) - (finish['OS']['seconds']-capture.drain)
        result['remaining_error_s'] = dict(median_absolute=float(np.median(abs(remaining_error))),
                                         p95_absolute=float(np.percentile(abs(remaining_error),95)),
                                         max_absolute=float(max(abs(remaining_error))))
    return result, curve


def joint_fit(captures, initial, free=(0, 1, 3, 4, 5), fit_buffer=True):
    """Equal-drawing calibration blocks; optionally freeze part of the v1 family."""
    free = list(free)
    lower = np.log(np.array([.2, 10., 1., .00001, .01, .00001, 128.])[free])
    upper = np.log(np.array([2., 1000., 1e9, .3, 15., .06, 4096.])[free])
    records = []
    for c in captures:
        left, right, split = windows(c)
        mask = split == 'calibration'
        records.append((c,left[mask],right[mask],(c.drain[right]-c.drain[left])[mask]))
    best, profile = None, []
    def candidate(buffer, start):
        nonlocal best
        base = np.array(start, dtype=float); base[6] = buffer
        def unpack(theta):
            p = base.copy(); p[free] = np.exp(theta)
            return p
        def residual(theta):
            p = unpack(theta); errors = []
            for c,left,right,actual in records:
                arrival,_ = simulate(c.program,p,c.gap,c.byte_rate)
                t = arrival[c.packet_indices]
                errors.append((t[right]-t[left]-actual)/np.sqrt(len(actual)))
            return np.concatenate(errors)
        fit = least_squares(residual,np.clip(np.log(base[free]),lower+1e-8,upper-1e-8),
                            bounds=(lower,upper),diff_step=1e-4,max_nfev=100,
                            ftol=1e-6,xtol=1e-6,gtol=1e-6)
        error = float(np.sum(fit.fun**2)/len(captures))
        profile.append(dict(buffer_bytes=buffer,calibration_rmse_s=error**.5))
        if best is None or error < best[0]: best = error,unpack(fit.x)
    if fit_buffer:
        for buffer in range(256,4097,256): candidate(buffer,initial)
        center = int(best[1][6])
        for buffer in range(max(128,center-192),min(4096,center+192)+1,32): candidate(buffer,best[1])
    else:
        candidate(initial[6],initial)
    return dict(parameters=dict(zip(PARAMETERS,best[1].tolist())),profile=profile,
                calibration_rmse_s=float(best[0]**.5),
                training_attempts=[c.directory.name for c in captures])


def run(args):
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    model_path = Path(args.model); model = json.loads(model_path.read_text())
    params = [model['parameters'][n] for n in PARAMETERS]
    captures = [load_capture(Path(p)) for p in args.attempts]
    if len(captures)<3: raise ValueError('Need at least three drawings for this comparison')
    if len({c.program.sha256 for c in captures}) != len(captures):
        raise ValueError('Repeated geometry is not independent whole-plot validation')
    result = dict(model=str(model_path),model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
                  method='Fixed model v1; then equal-weight joint calibration sections, fixed acceleration and sender turnaround; each entire omitted plot excluded from refitting',
                  plots=[],refits=[])
    finishes, curves = [], []
    for c in captures:
        if c.summary['injected_writes'] != c.summary['completion_queries'] or c.summary['acknowledged_prepared_bytes'] != len(c.program.source):
            raise ValueError('Only uninterrupted complete captures can be compared')
        record = json.loads((c.directory/'record.json').read_text())
        finish = completion(c.directory); finishes.append(finish)
        measured, curve = score(c,params,model['host_turnaround_s'],finish); curves.append(curve)
        saved = record.get('estimate')
        if saved:
            assert saved['prepared_sha256'] == c.program.sha256, 'Saved estimate/source mismatch'
            assert abs(saved['feed_seconds']-measured['feed']['predicted_s'])<1e-6, 'Saved estimate uses a different model'
            assert abs(saved['motion_seconds']-measured['predicted_motion_s'])<1e-6
        result['plots'].append(dict(attempt=c.directory.name,name=record['name'],created_at=record['created_at'],
                                    source_sha256=c.program.sha256,trace_sha256=c.trace_sha256,
                                    summary=c.summary,saved_estimate=saved,completion_evidence=finish,v1=measured))
        print(json.dumps({'attempt':c.directory.name,'v1':measured}),flush=True)
    for omitted in [*range(len(captures)),None]:
        training = [c for i,c in enumerate(captures) if i!=omitted]
        fitted = joint_fit(training,params)
        fitted['omitted_attempt'] = captures[omitted].directory.name if omitted is not None else None
        values = [fitted['parameters'][n] for n in PARAMETERS]
        fitted['scores'] = {c.directory.name:score(c,values,model['host_turnaround_s'],f)[0]
                            for c,f in zip(captures,finishes)}
        result['refits'].append(fitted)
        (output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'omitted':fitted['omitted_attempt'],'parameters':fitted['parameters'],
                          'feed_errors_percent':{k:v['feed']['error_percent'] for k,v in fitted['scores'].items()}}),flush=True)
    charts(output,captures,curves,result)
    print('Saved '+str(output/'index.html'))


def duration(seconds):
    minutes, seconds = divmod(round(seconds),60)
    return f'{minutes}:{seconds:02d}'


def charts(output,captures,curves,result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes = plt.subplots(len(captures),2,figsize=(12,3.3*len(captures)),constrained_layout=True)
    for row,c,curve,info in zip(axes,captures,curves,result['plots']):
        x = c.packet_ends/1e6
        row[0].plot(x,c.drain/60,label='Recorded feed',color='#172b30')
        row[0].plot(x,curve/60,label='Deployed v1',color='#db6b23',linestyle='--')
        row[0].set(title=info['name'],ylabel='Minutes',xlabel='Prepared HPGL (MB)')
        row[0].legend()
        row[1].plot(x,curve-c.drain,color='#db6b23')
        row[1].axhline(0,color='#172b30',linewidth=.8)
        row[1].set(title='Cumulative feed prediction error',ylabel='Seconds',xlabel='Prepared HPGL (MB)')
        for a in row:a.grid(alpha=.2)
    fig.savefig(output/'validation.png',dpi=170);fig.savefig(output/'validation.svg');plt.close(fig)
    rows=[]
    for p in result['plots']:
        s=p['v1'];m=s.get('completion',s['feed']);kind='OA/OS finish' if p['completion_evidence'] else 'Feeding only'
        origin='Saved before sending' if p['saved_estimate'] else 'Unchanged v1 applied afterward'
        rows.append(f'<tr><td>{html.escape(p["name"])}</td><td>{kind}</td><td>{duration(m["predicted_s"])}</td><td>{duration(m["actual_s"])}</td><td>{m["error_s"]:+.1f} s ({m["error_percent"]:+.2f}%)</td><td>{origin}</td></tr>')
    refits=[]
    for fit in result['refits']:
        aid=fit['omitted_attempt']
        if aid:
            p=next(p for p in result['plots'] if p['attempt']==aid)
            old=p['v1']['feed'];new=fit['scores'][aid]['feed']
            refits.append(f'<tr><td>{html.escape(p["name"])}</td><td>{old["error_percent"]:+.2f}%</td><td>{new["error_percent"]:+.2f}%</td></tr>')
    (output/'index.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>HP7585 timing validation · November 4</title>
<style>body{{font:17px/1.5 system-ui;color:#172b30;max-width:1200px;margin:40px auto;padding:0 20px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:9px;border-bottom:1px solid #ddd}}img{{width:100%}}</style>
<h1>Timing model · new-plot validation</h1><p>Unchanged deployed coefficients; exact HPGL hashes verified. The Snoqualmie capture supplied the original fit; Ravenna and Paine are independent drawings. Only Paine has a saved pre-plot estimate and automatic completion evidence.</p>
<table><tr><th>Drawing</th><th>Measurement</th><th>Predicted</th><th>Recorded</th><th>Error</th><th>Prediction provenance</th></tr>{''.join(rows)}</table>
<img src="validation.png" alt="Predicted and recorded feed curves with residuals for all three drawings">
<h2>Refit on the other two drawings</h2><p>Same six-parameter family; acceleration and sender turnaround fixed. Calibration sections weighted equally per training drawing. The omitted plot contributes no timings to its refit. Errors below compare feeding duration.</p>
<table><tr><th>Omitted drawing</th><th>Deployed v1 error</th><th>Refitted error</th></tr>{''.join(refits)}</table>
<p>The original model already used Snoqualmie, so its old error is a calibration comparison; Ravenna and Paine are the external checks. The all-three pooled fit in the JSON is an in-sample diagnostic, not another external test. OA/OS times include query/reply latency. No service or plotter changes were made.</p>
<p><a href="results.json">Full results, coefficients, profiles and hashes</a> · <a href="validation.svg">SVG chart</a></p>''')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('attempts',nargs='+')
    parser.add_argument('--model',default='controller/models/hp7585-snoqualmie-v1.json')
    parser.add_argument('--output',required=True)
    run(parser.parse_args())
