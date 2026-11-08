"""Fit small buffered-motion models with disjoint calibration/selection/audit blocks."""
from .model import read_retained
import argparse
import csv
import html
import json
from pathlib import Path
import time

import numpy as np
from scipy.optimize import least_squares

from .model import PARAMETERS, load_capture, parse, simulate, packetize


def windows(capture):
    left = np.arange(8, len(capture.packet_ends)-8, 8)
    right = left+8
    a, b = capture.packet_ends[left], capture.packet_ends[right]
    # Guard the boundary against the maximum fitted FIFO look-back, and use
    # contiguous 64-KiB blocks rather than randomly splitting adjacent samples.
    stable = ((a-4096)//65536 == (b+4096)//65536) & (a > 12000) & (b < len(capture.program.source)-12000)
    group = (a//65536)%5
    split = np.full(len(left), 'guard', dtype=object)
    split[stable & (group < 3)] = 'calibration'
    split[stable & (group == 3)] = 'selection'
    split[stable & (group == 4)] = 'audit'
    if any(np.count_nonzero(split == s) < 10 for s in ('calibration', 'selection', 'audit')):
        raise ValueError('This fit needs a longer complete trace for blocked validation')
    return left, right, split


def metrics(actual, prediction):
    delta = prediction-actual
    return dict(count=len(actual), mae_s=float(np.mean(abs(delta))),
                rmse_s=float(np.sqrt(np.mean(delta**2))),
                mean_error_s=float(np.mean(delta)),
                median_absolute_percent=float(np.median(abs(delta)/actual)*100),
                actual_total_s=float(actual.sum()), predicted_total_s=float(prediction.sum()))


def optimize(capture, left, right, mask, acceleration, buffer, initial=None, baseline=False):
    # Acceleration and capacity are profiled; the remaining smooth parameters
    # are bounded least squares. No timing from selection/audit enters this fit.
    free = [0, 1, 3, 4] if baseline else [0, 1, 3, 4, 5]
    initial = np.array(initial if initial is not None else [1., 200., acceleration, .03, 3., .018, buffer])
    initial[2], initial[6] = acceleration, buffer
    if baseline: initial[5] = 0.
    lower = np.log(np.array([.2, 10., 1., .00001, .01, .00001, 128.])[free])
    upper = np.log(np.array([2., 1000., 1e9, .3, 15., .06, 4096.])[free])
    expected = (capture.drain[right]-capture.drain[left])[mask]
    def unpack(theta):
        p = initial.copy(); p[free] = np.exp(theta)
        return p
    def residual(theta):
        incoming, _ = simulate(capture.program, unpack(theta), capture.gap, capture.byte_rate)
        packet = incoming[capture.packet_indices]
        return (packet[right]-packet[left])[mask]-expected
    start = np.clip(np.log(initial[free]), lower+1e-8, upper-1e-8)
    fit = least_squares(residual, start, bounds=(lower, upper), diff_step=1e-4,
                        max_nfev=100, ftol=1e-6, xtol=1e-6, gtol=1e-6)
    return unpack(fit.x), float(np.mean(fit.fun**2))


def fit_family(capture, left, right, split, acceleration, baseline=False):
    mask = split == 'calibration'; best = None; profile = []
    def candidate(buffer, initial=None):
        nonlocal best
        parameters, error = optimize(capture, left, right, mask, acceleration, buffer, initial, baseline)
        profile.append(dict(buffer_bytes=buffer, calibration_rmse_s=error**.5))
        if best is None or error < best[0]: best = (error, parameters)
    for buffer in range(256, 4097, 256): candidate(buffer)
    center = int(best[1][6])
    for buffer in range(max(128, center-192), min(4096, center+192)+1, 32):
        candidate(buffer, best[1])
    incoming, finish = simulate(capture.program, best[1], capture.gap, capture.byte_rate)
    packet = incoming[capture.packet_indices]
    observed = capture.drain[right]-capture.drain[left]
    predicted = packet[right]-packet[left]
    return dict(family='distance_and_lifts' if baseline else 'vector_floor_and_motion',
                parameters=dict(zip(PARAMETERS, best[1].tolist())),
                calibration=metrics(observed[split=='calibration'], predicted[split=='calibration']),
                selection=metrics(observed[split=='selection'], predicted[split=='selection']),
                profile=profile)


def predict(parameters, program, turnaround, byte_rate):
    packet_indices, gaps = packetize(program, turnaround)
    incoming, done = simulate(program, parameters, gaps, byte_rate)
    return incoming, done, packet_indices


def graph(output, capture, forecast, candidates, left, right, split):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(3, 1, figsize=(11, 10), gridspec_kw={'height_ratios':[2,1,1.2]}, constrained_layout=True)
    x = capture.packet_ends/1e6
    ax[0].plot(x, capture.drain/60, label='Observed feed', color='#172b30', linewidth=2)
    ax[0].plot(x, forecast/60, label='Model forecast', color='#db6b23', linestyle='--', linewidth=1.8)
    ax[0].set(ylabel='Minutes', title='HP7585 · recorded feed and model forecast')
    ax[0].legend(loc='upper left')
    for ink in capture.header['metadata']['inks']:
        slot = ink['slot']; positions = capture.program.ends[capture.program.slots == slot]
        if len(positions):
            lo, hi = positions[0]/1e6, positions[-1]/1e6
            ax[0].axvspan(lo, hi, color=ink['loaded']['color'], alpha=.09)
            ax[0].text((lo+hi)/2, 1., ink['loaded']['label'], ha='center', fontsize=9)
    ax[1].plot(x, forecast-capture.drain, color='#db6b23', linewidth=1)
    ax[1].axhline(0, color='#172b30', linewidth=.8)
    ax[1].set(xlabel='Prepared HPGL (MB)', ylabel='Forecast error (s)')
    obs = capture.drain[right]-capture.drain[left]
    est = forecast[right]-forecast[left]
    for name,color in [('calibration','#8c9699'),('selection','#326e68'),('audit','#9b4f9f')]:
        mask=split==name;ax[2].scatter(obs[mask],est[mask],s=16,alpha=.6,label=name,color=color)
    maximum=max(obs.max(),est.max())*1.05
    ax[2].plot([0,maximum],[0,maximum],color='#172b30',linewidth=.8)
    ax[2].set(xlabel='Observed section duration (s)',ylabel='Predicted section duration (s)')
    ax[2].legend()
    for axis in ax:axis.grid(alpha=.18)
    fig.savefig(output/'fit.png',dpi=180);fig.savefig(output/'fit.svg');plt.close(fig)
    fig, ax=plt.subplots(figsize=(8,4),constrained_layout=True)
    for c in candidates:
        if c['family']=='distance_and_lifts':continue
        pairs=sorted((r['buffer_bytes'],r['calibration_rmse_s']) for r in c['profile'])
        acceleration=c['parameters']['acceleration_mm_s2']
        label='No acceleration correction' if acceleration >= 1e8 else f"a = {acceleration:g} mm/s²"
        ax.plot(*np.array(pairs).T,label=label)
    ax.set(xlabel='Effective buffer capacity (bytes)',ylabel='Calibration RMS error (s)',title='Buffer and acceleration profile · other parameters refitted')
    ax.legend(fontsize=8,ncol=2);ax.grid(alpha=.2);fig.savefig(output/'profiles.png',dpi=150);plt.close(fig)


def calibrate(args):
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    capture=load_capture(args.attempt)
    if capture.summary['injected_writes'] != capture.summary['completion_queries'] or capture.summary['acknowledged_prepared_bytes'] != len(capture.program.source):
        raise ValueError('Calibration requires an uninterrupted full trace')
    left,right,split=windows(capture);candidates=[]
    # Model family/acceleration selected only on the selection blocks. The
    # audit blocks are reported after selection, not used to rank candidates.
    for acceleration,baseline in [(1e9,True),(2000,False),(5000,False),(10000,False),(30000,False),(1e9,False)]:
        start=time.monotonic()
        result=fit_family(capture,left,right,split,acceleration,baseline)
        candidates.append(result)
        print(json.dumps({'family':result['family'],'acceleration':acceleration,'selection':result['selection'],'parameters':result['parameters'],'seconds':time.monotonic()-start}),flush=True)
    chosen=min(candidates,key=lambda c:c['selection']['rmse_s'])
    params=np.array([chosen['parameters'][n] for n in PARAMETERS])
    # Fixed host turnaround is measured on calibration blocks only. A future
    # forecast does not use the completed plot's observed timing as an input.
    boundary_gaps=capture.entry[1:]-capture.drain[:-1]
    gmask=(capture.packet_ends[1:]//65536)%5<3
    turnaround=float(np.median(boundary_gaps[gmask]))
    incoming,done,packet_indices=predict(params,capture.program,turnaround,capture.byte_rate)
    if not np.array_equal(packet_indices,capture.packet_indices):raise ValueError('Packetization differs from recorded feeder')
    # Model arrival is a serial pacing approximation to USB retirement, so
    # absolute phase at startup is approximate. No fitted time-axis alignment.
    forecast=incoming[packet_indices]
    observed=capture.drain[right]-capture.drain[left];estimate=forecast[right]-forecast[left]
    scores={s:metrics(observed[split==s],estimate[split==s]) for s in ('calibration','selection','audit')}
    baseline=candidates[0]
    baseline_forecast,_,_=predict([baseline['parameters'][n] for n in PARAMETERS],capture.program,turnaround,capture.byte_rate)
    baseline_packet=baseline_forecast[capture.packet_indices]
    baseline_delta=baseline_packet[right]-baseline_packet[left]
    baseline_audit=metrics(observed[split=='audit'],baseline_delta[split=='audit'])
    # Also assess whole guarded 64-KiB audit blocks: these are disjoint and
    # show the practical error on roughly minute-long stretches.
    block_metrics=[]
    for region in np.unique(capture.packet_ends//65536):
        if region%5!=4:continue
        ids=np.flatnonzero((capture.packet_ends>=region*65536+4096)&(capture.packet_ends<(region+1)*65536-4096))
        if len(ids)<2:continue
        a,b=ids[0],ids[-1];actual=float(capture.drain[b]-capture.drain[a]);pred=float(forecast[b]-forecast[a])
        block_metrics.append(dict(start_byte=int(capture.packet_ends[a]),end_byte=int(capture.packet_ends[b]),actual_s=actual,predicted_s=pred,error_s=pred-actual,error_percent=100*(pred-actual)/actual))
    sensitivity=[]
    for group in range(3):
        mask=(split=='calibration')&((capture.packet_ends[left]//65536)%3!=group)
        values,_=optimize(capture,left,right,mask,params[2],params[6],params,chosen['family']=='distance_and_lifts')
        a,d,_=predict(values,capture.program,turnaround,capture.byte_rate)
        sensitivity.append(dict(excluded_calibration_group=group,parameters=dict(zip(PARAMETERS,values.tolist())),feed_s=float(a[-1])))
    result=dict(schema='plotline.timing-model.v1',model=chosen['family'],parameters=chosen['parameters'],
                fitted_parameter_count=(5 if chosen['family']=='distance_and_lifts' else 6)+int(params[2]<1e8),
                acceleration_model='disabled by selection; numeric 1e9 is a limiting-case sentinel, not a measured acceleration' if params[2]>=1e8 else 'geometric surrogate; not a measured hardware g limit',
                serial_byte_rate=capture.byte_rate,host_turnaround_s=turnaround,
                source={'attempt':capture.summary['attempt'],'prepared_sha256':capture.program.sha256,'trace_sha256':capture.trace_sha256,'bytes':len(capture.program.source),'commands':len(capture.program.ends),'samples':capture.summary['samples']},
                validation={'method':'64 KiB blocks: residue 0/1/2 calibration, 3 selection, 4 audit; 4 KiB guards; no physical-finish observation',
                            'section_scores':scores,'audit_blocks':block_metrics,'distance_lift_baseline_audit':baseline_audit,
                            'observed_feed_s':float(capture.drain[-1]),'predicted_feed_s':float(incoming[-1]),
                            'predicted_motion_finish_s':float(done[-1]),'predicted_buffered_tail_s':float(done[-1]-incoming[-1]),
                            'sensitivity':sensitivity},
                limitations=['One drawing at VS5; other speeds/media not validated.','Acceleration is a geometric surrogate and trades off with cycle time and speed.','Only three between-color pen changes; no audited change transition.','Initial carriage position is unknown; first move distance omitted.','Lift/lower split is not identifiable; equal halves of a combined cycle assumed.','Motion finish is predicted, not measured; no automatic completion or ETA deployment.'])
    if args.check_prefix:
        prefix=load_capture(args.check_prefix)
        if prefix.program.sha256!=capture.program.sha256:raise ValueError('Prefix comparison is for this same drawing')
        pi,pd,pack=predict(params,prefix.program,turnaround,prefix.byte_rate)
        expected=pi[prefix.packet_indices];actual=prefix.drain
        result['prefix_check']=dict(attempt=prefix.summary['attempt'],prepared_bytes=int(prefix.packet_ends[-1]),observed_feed_s=float(actual[-1]),predicted_feed_s=float(expected[-1]),error_s=float(expected[-1]-actual[-1]),note='Separate cancelled run of identical geometry; cancellation command excluded. No refitting.')
    (output/'model.json').write_text(json.dumps(result,indent=2)+'\n')
    (output/'candidates.json').write_text(json.dumps(candidates,indent=2)+'\n')
    with (output/'prediction.csv').open('w',newline='') as f:
        entry_forecast=np.r_[0.,forecast[:-1]+turnaround]
        writer=csv.writer(f);writer.writerow(['prepared_byte','observed_entry_s','forecast_entry_s','observed_host_queue_empty_s','forecast_queue_boundary_s'])
        writer.writerows(zip(capture.packet_ends,capture.entry,entry_forecast,capture.drain,forecast))
    with (output/'sections.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['start_byte','end_byte','split','observed_s','forecast_s'])
        writer.writerows(zip(capture.packet_ends[left],capture.packet_ends[right],split,observed,estimate))
    graph(output,capture,forecast,candidates,left,right,split)
    def duration(seconds):
        minutes, remainder = divmod(round(seconds), 60)
        return f'{minutes}:{remainder:02d}'
    rows=''.join(f'<tr><td>{html.escape(name)}</td><td>{value:.5g}</td></tr>' for name,value in chosen['parameters'].items() if name!='acceleration_mm_s2')
    blocks=''.join(f'<tr><td>{b["start_byte"]:,}–{b["end_byte"]:,}</td><td>{b["actual_s"]:.1f}</td><td>{b["predicted_s"]:.1f}</td><td>{b["error_percent"]:+.1f}%</td></tr>' for b in block_metrics)
    (output/'index.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>HP7585 timing model</title>
<style>body{{font:17px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#172b30}}img{{width:100%}}table{{border-collapse:collapse}}td,th{{padding:7px 18px 7px 0;text-align:left;border-bottom:1px solid #ddd}}code{{font-size:.85em}}</style>
<h1>HP7585 timing model</h1><p>Recorded feed: <strong>{duration(capture.drain[-1])}</strong> · Forecast: <strong>{duration(incoming[-1])}</strong> · Difference: <strong>{incoming[-1]-capture.drain[-1]:+.1f} seconds</strong>.</p>
<p>{len(capture.program.ends):,} commands; {capture.summary['samples']:,} Pi observations. Parameters fit to separate blocks of this one drawing. The forecast uses a fixed {turnaround*1000:.0f} ms sender turnaround, not the recorded duration as an input.</p>
<img src="fit.png" alt="Observed and forecast feed, timing residuals, and section validation">
<h2>Held-out contiguous stretches</h2><table><tr><th>Prepared bytes</th><th>Recorded (s)</th><th>Forecast (s)</th><th>Error</th></tr>{blocks}</table>
<p>Audit short-section mean absolute error: {scores['audit']['mae_s']:.2f} s; simple distance/lift baseline: {baseline_audit['mae_s']:.2f} s. These are internal checks on one drawing, not an accuracy guarantee for a different print.</p>
<h2>Effective parameters</h2><table>{rows}</table><p>{html.escape(result['acceleration_model'])}. Lift and lower are fitted together. Pen-change and pen-up estimates are weakly constrained. The predicted {done[-1]-incoming[-1]:.1f} s buffered tail has no observed finish timestamp.</p>
<p><a href="model.json">Model and diagnostics</a> · <a href="prediction.csv">Feed curve CSV</a> · <a href="sections.csv">Validation sections CSV</a> · <a href="profiles.png">Parameter profiles</a></p>
<p>Source SHA-256: <code>{capture.program.sha256}</code>. No physical plot or service change was made for this analysis.</p>''')
    print(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    fit=sub.add_parser('fit');fit.add_argument('attempt');fit.add_argument('--output',required=True);fit.add_argument('--check-prefix')
    pred=sub.add_parser('predict');pred.add_argument('prepared_hpgl');pred.add_argument('--model',required=True)
    args=p.parse_args()
    if args.command=='fit':calibrate(args)
    else:
        model=json.loads(Path(args.model).read_text());program=parse(read_retained(args.prepared_hpgl))
        params=[model['parameters'][n] for n in PARAMETERS]
        incoming,done,_=predict(params,program,model['host_turnaround_s'],model['serial_byte_rate'])
        speeds=sorted(set(program.speeds[program.down]/10))
        print(json.dumps({'prepared_sha256':program.sha256,'feed_seconds':float(incoming[-1]),'predicted_motion_seconds':float(done[-1]),'calibrated_at_vs_cm_s':5,'requested_speeds_cm_s':speeds,'speed_extrapolation':any(v!=5 for v in speeds),'limits':model['limitations']},indent=2))


if __name__=='__main__':main()
