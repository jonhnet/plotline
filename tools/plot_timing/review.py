"""Review retained real plots with a chronological holdout; never promote a model.

Reads only local captures. Incomplete, interrupted and recovery attempts are
listed explicitly, not silently treated as independent successful drawings.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from .evaluate import completion, duration, joint_fit, score
from .fit import predict
from .model import PARAMETERS, load_capture, simulate


def timestamp(value):
    return datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()


def eligibility(record):
    execution = record.get('execution') or {}
    if record.get('recovery'):
        return 'Recovery suffix; not an independent complete drawing'
    if (execution.get('state') != 'finished' or execution.get('cancelled')
            or execution.get('submitted_bytes') != record['bytes'] or not record['bytes']):
        return 'Incomplete, cancelled, or not executed'
    return None


def run(args):
    if timestamp(args.train_before) >= timestamp(args.before):
        raise ValueError('Training cutoff must precede the review cutoff')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    path = Path(args.model)
    model = json.loads(path.read_text())
    params = np.array([model['parameters'][n] for n in PARAMETERS])
    turnaround = model['host_turnaround_s']
    result = dict(schema='plotline.timing-review.v1', before=args.before,
                  train_before=args.train_before, model=str(path),
                  model_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  original_calibration_attempt=model['source']['attempt'],
                  plots=[], excluded=[], candidates=[])
    captures, curves, finishes = {}, {}, {}
    paths = sorted(Path(args.spool).glob('*/record.json'),
                   key=lambda p: json.loads(p.read_text())['created_at'])
    seen = set()
    for path in paths:
        record = json.loads(path.read_text())
        if record['created_at'] >= timestamp(args.before):
            continue
        aid = path.parent.name
        row = dict(attempt=aid, name=record['name'], created_at=record['created_at'],
                   bytes=record['bytes'])
        reason = eligibility(record)
        try:
            if reason:
                raise ValueError(reason)
            c = load_capture(path.parent)
            if (c.summary['injected_writes'] != c.summary['completion_queries']
                    or c.summary['acknowledged_prepared_bytes'] != len(c.program.source)):
                raise ValueError('Interrupted or incomplete trace')
            if c.program.sha256 in seen:
                raise ValueError('Repeated exact HPGL; not independent validation')
            finish = completion(path.parent)
            measured, curve = score(c, params, turnaround, finish)
            saved = record.get('estimate')
            if saved and saved['prepared_sha256'] != c.program.sha256:
                raise ValueError('Saved estimate/source mismatch')
        except (ValueError, AssertionError, FileNotFoundError) as error:
            row['reason'] = str(error)
            result['excluded'].append(row)
            continue
        seen.add(c.program.sha256)
        captures[aid], curves[aid], finishes[aid] = c, curve, finish
        gaps = c.entry[1:] - c.drain[:-1]
        # This diagnostic uses measured sender gaps; the forecast above does not.
        with_gaps, _ = simulate(c.program, params, c.gap, c.byte_rate)
        row.update(source_sha256=c.program.sha256, trace_sha256=c.trace_sha256,
                   summary=c.summary, completion_evidence=finish, v1=measured,
                   saved_estimate=saved,
                   saved_motion_difference_s=(saved['motion_seconds']-measured['predicted_motion_s']) if saved else None,
                   period='training' if record['created_at'] < timestamp(args.train_before) else 'holdout',
                   category='calibration' if record['name'].startswith('Pen calibration ') else 'artwork',
                   block_fit_eligible=bool(measured['sections']),
                   speeds_mm_s=sorted(set(c.program.speeds.tolist())),
                   sender_gap_median_s=float(np.median(gaps)) if len(gaps) else None,
                   sender_gap_max_s=float(gaps.max()) if len(gaps) else None,
                   measured_gap_feed_prediction_s=float(with_gaps[-1]),
                   geometry=dict(down_m=float(c.program.lengths[c.program.down].sum()/1000),
                                 up_m=float(c.program.lengths[~c.program.down].sum()/1000),
                                 vectors=len(c.program.lengths),
                                 pen_transitions=int(c.program.transitions.sum()),
                                 pen_selections=int(c.program.selections.sum())))
        result['plots'].append(row)
        m = measured.get('completion', measured['feed'])
        print(f'{aid[:8]} {record["name"]}: {m["error_s"]:+.2f}s / {m["error_percent"]:+.2f}%', flush=True)

    def save():
        (output/'results.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    save()
    training = [captures[p['attempt']] for p in result['plots']
                if p['period']=='training' and p['block_fit_eligible']]
    if len(training) < 3:
        raise ValueError('Need at least three older complete drawings for block refitting')
    if not any(p['period']=='holdout' for p in result['plots']):
        raise ValueError('Need newer plots held out from the fit')
    # Same section objective as the first review, with each drawing weighted
    # equally. Both variants are specified before looking at holdout scores.
    for label, free, fit_buffer in [('speed-and-vector-floor', (0,5), False),
                                    ('six-parameter-refit', (0,1,3,4,5), True)]:
        print('Fitting '+label, flush=True)
        fitted = joint_fit(training, params, free=free, fit_buffer=fit_buffer)
        fitted['name'] = label
        fitted['objective'] = 'Equal-drawing guarded calibration sections; acceleration and sender turnaround fixed'
        result['candidates'].append(fitted)
        save()

    # A separate diagnosis of the short calibration-sheet error. Fit only the
    # pen-change coefficient to the older calibration sheets, leaving the later
    # sheet and all artwork as checks. Completion includes OA/OS reply latency.
    calibration = [p for p in result['plots'] if p['period']=='training'
                   and p['category']=='calibration' and p['completion_evidence']]
    if len(calibration) >= 2:
        def residual(theta):
            candidate = params.copy(); candidate[4] = theta[0]
            return np.array([predict(candidate, captures[p['attempt']].program, turnaround,
                                     captures[p['attempt']].byte_rate)[1][-1]
                             - p['completion_evidence']['OS']['seconds'] for p in calibration])
        fit = least_squares(residual, [params[4]], bounds=(0.,15.))
        candidate = params.copy(); candidate[4] = fit.x[0]
        result['candidates'].append(dict(name='calibration-pen-change-only',
            objective='Older calibration-sheet completion seconds; all other coefficients fixed',
            parameters=dict(zip(PARAMETERS,candidate.tolist())),
            training_attempts=[p['attempt'] for p in calibration]))
    for candidate in result['candidates']:
        values = [candidate['parameters'][n] for n in PARAMETERS]
        candidate['scores'] = {aid:score(c, values, turnaround, finishes[aid])[0]
                               for aid,c in captures.items()}
        print(candidate['name'], json.dumps(candidate['parameters']), flush=True)
    save()
    # Small durable evidence is useful after raw captures expire under retention.
    snapshot = {k:result[k] for k in ('schema','before','train_before','model','model_sha256',
                                     'original_calibration_attempt','excluded')}
    snapshot['plots'] = [{k:p[k] for k in ('attempt','name','created_at','bytes','source_sha256',
        'trace_sha256','category','period','block_fit_eligible','saved_motion_difference_s')}
        | {'feed':p['v1']['feed'], 'completion':p['v1'].get('completion')}
        for p in result['plots']]
    snapshot['candidates'] = [{k:c[k] for k in ('name','objective','parameters','training_attempts')}
        | {'scores':{aid:{k:s[k] for k in ('feed','completion') if k in s}
                    for aid,s in c['scores'].items()}} for c in result['candidates']]
    (output/'snapshot.json').write_text(json.dumps(snapshot, indent=2, allow_nan=False)+'\n')
    report(output, result, captures, curves)
    print('Saved '+str(output/'index.html'), flush=True)


def report(output, result, captures, curves):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = result['plots']
    candidates = result['candidates']
    fig, axes = plt.subplots(len(rows), 2, figsize=(12, 2.5*len(rows)), squeeze=False,
                             constrained_layout=True)
    csv_rows = []
    for row, axes_row in zip(rows, axes):
        aid = row['attempt']; c = captures[aid]; curve = curves[aid]
        measured = row['v1'].get('completion', row['v1']['feed'])
        label = row['name'].replace(' · page 1.hpgl','').removesuffix('.hpgl')
        if len(label)>48: label=label[:45]+'…'
        axes_row[0].plot(c.packet_ends/1e6, c.drain/60, label='Recorded feed', color='#172b30')
        axes_row[0].plot(c.packet_ends/1e6, curve/60, label='Deployed v1', color='#db6b23', linestyle='--')
        axes_row[0].set(title=f'{label} · {aid[:8]}', ylabel='Minutes', xlabel='HPGL (MB)')
        axes_row[0].legend()
        axes_row[1].plot(c.drain/60, curve-c.drain, color='#db6b23')
        axes_row[1].axhline(0, color='#172b30', linewidth=.8)
        axes_row[1].set(title='Cumulative feed forecast error', ylabel='Seconds', xlabel='Elapsed minutes')
        for a in axes_row: a.grid(alpha=.2)
        csv_rows.append(dict(attempt=aid, name=row['name'], category=row['category'], period=row['period'],
                             measurement='OA/OS finish' if row['completion_evidence'] else 'feed', **measured))
    fig.savefig(output/'curves.png', dpi=130)
    fig.savefig(output/'curves.svg'); plt.close(fig)
    with (output/'plots.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0])); writer.writeheader(); writer.writerows(csv_rows)
    table, comparisons = [], []
    for row, data in zip(rows,csv_rows):
        label = html.escape(row['name'])+' <small>'+row['attempt'][:8]+'</small>'
        table.append(f'<tr><td>{label}</td><td>{data["measurement"]}</td><td>{duration(data["predicted_s"])}</td>'
                     f'<td>{duration(data["actual_s"])}</td><td>{data["error_s"]:+.1f}s ({data["error_percent"]:+.2f}%)</td></tr>')
        if row['period']=='holdout':
            cells = [f'{data["error_s"]:+.1f}s ({data["error_percent"]:+.2f}%)']
            for candidate in candidates:
                s = candidate['scores'][row['attempt']]; m = s.get('completion',s['feed'])
                cells.append(f'{m["error_s"]:+.1f}s ({m["error_percent"]:+.2f}%)')
            comparisons.append('<tr><td>'+label+'</td>'+''.join('<td>'+v+'</td>' for v in cells)+'</tr>')
    excluded = ''.join(f'<li>{html.escape(r["name"])} ({r["attempt"][:8]}): {html.escape(r["reason"])}</li>' for r in result['excluded'])
    (output/'index.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>HP7585 timing review</title>
<style>body{{font:16px/1.5 system-ui;color:#172b30;max-width:1300px;margin:40px auto;padding:0 20px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #ddd}}img{{width:100%}}small{{white-space:nowrap;color:#647576}}</style>
<h1>HP7585 timing review · captures before {html.escape(result['before'])}</h1>
<p>Exact retained HPGL/trace hashes verified. Positive errors mean an overestimate. Times start after preparation and initial device checks. OA/OS confirms completion and includes query/reply latency. Snoqualmie supplied the original model fit; other drawings test unchanged coefficients. Saved estimates and recomputed differences are in the JSON.</p>
<table><tr><th>Drawing</th><th>Measurement</th><th>Predicted</th><th>Recorded</th><th>Error</th></tr>{''.join(table)}</table>
<h2>Chronological holdout</h2><p>Candidate fitting uses only captures before {html.escape(result['train_before'])}. No later timing contributes to its fit. The two block refits use sufficiently long older drawings with the original guarded split. The pen-change-only diagnostic uses the older calibration sheets. Variants of one pattern are related examples, not independent families. These are comparisons, not automatic model promotion.</p>
<table><tr><th>Later drawing</th><th>Deployed v1</th>{''.join('<th>'+html.escape(c['name'])+'</th>' for c in candidates)}</tr>{''.join(comparisons)}</table>
<p><a href="results.json">Full results, fit parameters and hashes</a> · <a href="plots.csv">CSV</a> · <a href="curves.svg">SVG curves</a></p>
<h2>Recorded feed and residuals</h2><img src="curves.png" alt="Recorded and predicted feeding times, and cumulative forecast errors">
<details><summary>Excluded attempts ({len(result['excluded'])})</summary><ul>{excluded}</ul></details>''')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', default='controller/state/jobs/plots')
    parser.add_argument('--model', default='controller/models/hp7585-snoqualmie-v1.json')
    parser.add_argument('--before', required=True, help='Exclusive UTC date bound for the review')
    parser.add_argument('--train-before', required=True, help='Exclusive UTC date bound for fitting')
    parser.add_argument('--output', required=True)
    run(parser.parse_args())
