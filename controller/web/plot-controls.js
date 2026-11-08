import {migrateStudioStorage} from '/studio.js';
import {paperSize,onPaperUnitChange} from '/paper-units.js';
import { plotProgress, duration } from "./progress.js";
import {recoveryPreview} from './recovery.js';

// The preview is the review. One click submits a durable, single-use request.
export function plotControls({ api, writeOptions, current, observed, isPlotDocument=()=>true, unavailable=()=>'', changed=()=>{} }) {
  const $ = (id) => document.getElementById(id);
  const pendingKey = "plotline.pending-plot";
  migrateStudioStorage(localStorage,pendingKey);
  const progress = plotProgress({api});
  let estimateKey = null, estimateTimer = null, estimateRequest = null, estimateData = null;
  function estimate(force = false) {
    const {job, setup} = current();
    const key = job && setup && !fitIssue ? JSON.stringify([job.id, setup]) : null;
    if (key === estimateKey && !force) return;
    estimateKey = key; clearTimeout(estimateTimer); estimateRequest?.abort();
    estimateData = null;
    if(!ownsActive()){progress.preview(null);$('idle-progress').hidden=false;}
    const label = $("plot-estimate"); label.title = "";
    $("estimate-retry").hidden = true;
    label.textContent = key ? "Estimating plot time…" : "";
    if (!key) return;
    const frozen = structuredClone(setup);
    estimateTimer = setTimeout(async () => {
      const controller = new AbortController(); estimateRequest = controller;
      try {
        const result = await api(`/api/jobs/${job.id}/estimate`, {method:"POST", ...writeOptions(frozen), signal:controller.signal}).then(r=>r.json());
        if (estimateKey !== key || controller.signal.aborted) return;
        estimateData = result;
        label.textContent = `Estimated plot time: ${result.motion_seconds < 60 ? "" : "about "}${duration(result.motion_seconds)}`;
        label.title = "For the selected paper and pens; no drawing commands are sent. " + (result.speed_extrapolation ? "Uses the Snoqualmie model at a speed outside its VS5 calibration." : "Snoqualmie timing model, including buffered final movement.");
        render();
      } catch (error) {
        if (estimateKey === key && !controller.signal.aborted) {
          label.textContent = `Plot time estimate unavailable: ${error.message}`;
          $("estimate-retry").hidden = !!ownsActive();
        }
      }
    }, 350);
  }
  $("estimate-retry").onclick = () => estimate(true);
  let state = { configured: null, connected: false }, fitIssue = "", busy = false,
    timer = null, polling = null, error = "", pending = null, lastObservation = "", submitting = false, statusVersion = 0;
  let checking=false, paperCheck=null, shownJob=null;
  const settingsKey=()=>{const {job,setup}=current();return job&&setup?JSON.stringify([job.id,setup]):null;};
  const paperIssue=()=>paperCheck?.error || (paperCheck?.key===settingsKey()?paperCheck.fit_error:'') || '';
  try { pending = JSON.parse(localStorage.getItem(pendingKey)); } catch {}
  const messages = {
    connecting: "Starting…", starting: "Starting…", feeding: "Feeding the plotter", pausing: "Pause requested…",
    paused: "Paused", cancelling: "Cancel requested…",
    finishing: "Finishing… waiting for the plotter",
    completion_unknown: "Completion could not be confirmed. Check the plotter before continuing.",
    submitted: "All data sent. The plotter may still be drawing.",
    cancelled: "Cancel sent. Wait for the pen to park.",
    finished: "Plot finished · confirmed", reset_acknowledged: "Reset confirmed · ready",
    uncertain: "Serial delivery is uncertain. Reset the plotter before continuing.",
    interrupted: "Plot interrupted. Reset the plotter before continuing.",
  };
  const execution = () => state.device?.execution;
  const localBusy = () => ["uploading", "starting", "uncertain"].includes(state.submission?.phase);
  const hasActive = () => localBusy() || !!(execution()&&!['finished','reset_acknowledged'].includes(execution().state));
  const ownsActive = () => hasActive() && isPlotDocument(state.submission) && state.submission?.job_id===current().job?.id;
  const recovery = recoveryPreview({api,start:async(job,id,request)=>{
    if(job!==current().job?.id)throw Error('The selected document changed. Reopen its recorded plot.');
    if(hasActive()||busy||pending)throw Error('Finish or resolve the current plot before resuming another.');
    if(!state.connected)throw Error('Stain is unavailable. Refresh status before resuming.');
    await send({job_id:job,recovery_of:id,request:{...request,attempt:uuid()}},true);
  }});
  $('plot-recover').onclick=()=>{if(!$('plot-recover').disabled)recovery.open(current().job,state.submission?.job_id===current().job.id?state.submission.id:null);};
  function remember(value) {
    if (value) localStorage.setItem(pendingKey, JSON.stringify(value));
    else localStorage.removeItem(pendingKey);
    pending = value;
  }
  function problem() {
    if(checking)return 'Checking plotter and paper…';
    if(unavailable())return unavailable();
    if (pending) return "Checking whether your Plot request was accepted. Use Refresh status if needed.";
    if (state.configured===null) return "Reading plotter status…";
    if (!state.configured) return "This server is configured for preview only.";
    if (!state.connected) return "Stain is unavailable. Refresh its status before plotting.";
    if (!state.device.plotting_enabled) return "Plotting is disabled in Plotline Studios.";
    if (!state.device.serial_present) return "The serial adapter is disconnected.";
    if (localBusy()) return "A plot is already starting or needs reconciliation.";
    const e = execution();
    if (e?.state === "finishing") return e.cancelled?'Waiting for the plotter to park the pen.':`Waiting for the plotter to finish ${state.submission?.name || "the current plot"}.`;
    if (e?.state === "completion_unknown") return e.cancelled?"Parking after cancellation was not confirmed. Open Plotter to resolve this.":"All drawing data was sent, but the plotter's completion reply was not confirmed. Open Plotter to resolve this.";
    if (e && ["submitted", "cancelled"].includes(e.state))
      return `Check that ${state.submission?.name || "the previous plot"} has stopped, then choose “Pen parked · mark finished” in Plotter.`;
    if (e && !["finished", "reset_acknowledged"].includes(e.state))
      return "Finish or resolve the current plot before starting another.";
    return fitIssue || paperIssue();
  }
  function render() {
    const e = execution(), local = state.submission, issue = problem();
    const failed=local?.phase==='failed'&&local.job_id===current().job?.id;
    const active=hasActive(),own=ownsActive();
    const starting = submitting || (own && local?.phase === "starting" && e?.state === "connecting");
    $("plot-open").textContent = starting ? "Starting…" : "Plot";
    $("plot-open").disabled = !current().job || !!issue || busy;
    $('plot-recover').disabled=!current().job||!state.configured||active||busy||!!pending||checking;
    $("plot-issue").textContent = busy ? "" : issue;
    const shownError=error||(failed?`Plot was not started: ${local.error}`:'');
    $("plot-error").textContent = shownError;
    $("plot-error").hidden = !shownError;
    $("plot-retry").hidden = !pending || busy;
    $("plot-panel").hidden = !own;
    $("mode-label").textContent = own ? 'Active plot' : 'Plot preview';
    $("mode-label").title = state.configured ? (state.connected ? 'Stain connected' : 'Stain offline') : 'Preview only';
    $('check-paper').hidden=!!active;
    $('check-paper').disabled=checking||busy||!state.configured||!state.connected||localBusy()||!!(e&&!['finished','reset_acknowledged'].includes(e.state));
    $('check-paper').textContent=checking?'Checking…':'Check plotter & paper';
    const checked=paperCheck?.observation;
    $('paper-check-result').textContent=checking?'Querying the plotter.':paperCheck?.error|| (checked?
      `Plotter responded · drawable area ${paperSize([checked.limits[2]-checked.limits[0],checked.limits[3]-checked.limits[1]].map(n=>n/checked.units_per_mm))} · ${new Date(checked.observed_at).toLocaleString()}. ${paperCheck.key&&paperCheck.key===settingsKey()?(paperCheck.fit_error||'This drawing fits the measured area.'):'Check again to verify this drawing and its current settings.'}`:
      'Stain status does not confirm plotter power or loaded paper. Check plotter & paper before plotting.');
    $('paper-check-result').hidden=!!active||(!checking&&!!paperIssue());
    let message = state.connected ? "Ready to plot" : (state.error || "Connecting to stain…");
    if (e) message = messages[e.state] || e.state;
    if (e?.state === "finished" && e.completion) message = "Plot finished · confirmed by plotter";
    if (e?.cancelled && e.state==='finishing') message = 'Cancelling… waiting for the pen to park';
    if (e?.cancelled && e.state==='finished') message = e.completion?'Plot cancelled · pen parked':'Plot cancelled · no drawing sent';
    if (e?.state === "completion_unknown" && e.error) message = e.error;
    if (submitting) message = "Starting…";
    if (failed) message = `Plot was not started: ${local.error?.split("\n")[0]}`;
    if (local?.phase === "uncertain") message = `Plot interrupted; delivery is uncertain. ${local.error || ""} Reset the plotter before continuing.`;
    $("plot-status").textContent = message;
    const matching = own && e && (!localBusy() || e.attempt === local?.id);
    if(own)progress.update(matching ? local : null, matching ? e : null, state.server_time_ms);
    else progress.preview(estimateData?.progress);
    $('idle-progress').hidden=!$('plot-progress').hidden;
    $('plot-estimate').hidden=!!own;$('plot-time').hidden=!own;
    $('estimate-retry').hidden=!!own||!$('plot-estimate').textContent.startsWith('Plot time estimate unavailable:');
    changed({execution:e,submission:local,issue,previewIssue:fitIssue||unavailable(),message,starting,failed,active,ownsActive:own,statusError:!!paperIssue()&&issue===paperIssue()});
    for (const [id, states] of Object.entries({
      "plot-pause": ["feeding"], "plot-resume": ["paused", "pausing"],
      "plot-cancel": ["connecting", "starting", "feeding", "pausing", "paused"],
      "plot-complete": ["submitted", "cancelled"], "plot-reset": ["uncertain", "interrupted", "completion_unknown"],
    })) {
      $(id).hidden = !matching || !states.includes(e.state);
      $(id).disabled = busy || (!state.connected && !["plot-complete", "plot-reset"].includes(id));
    }
  }
  onPaperUnitChange(render);
  function accept(ticket) {
    statusVersion++;
    state.submission = ticket;
    state.device = { ...state.device, execution: ticket.execution };
    if (pending?.request.attempt === ticket.id) { remember(null); error = ""; }
  }
  function poll() {
    if (polling) return polling;
    clearTimeout(timer);
    const version = statusVersion;
    polling = (async () => {
      try {
        const next = await api("/api/plotter").then((r) => r.json());
        if (version !== statusVersion) return;
        state = next;
        if (pending) {
          if (state.submission?.id === pending.request.attempt) { remember(null); error = ""; }
          else {
            const result = await api(`/api/plots/${pending.request.attempt}`).then((r) => r.json());
            if (version !== statusVersion) return;
            if (result.submission) { remember(null); error = ""; }
            else error = "The server has not recorded your request. Retry will use the same request, so it cannot create a second copy.";
          }
        }
        const o = state.submission?.observation;
        if (o && o.observed_at > lastObservation) { lastObservation = o.observed_at; observed(o); }
      } catch (e) { if (version === statusVersion) state = { ...state, connected: false, error: e.message }; }
      finally {
        render(); polling = null;
        if (state.configured!==false) timer = setTimeout(poll, execution()?.state === "connecting" ? 300 : 3000);
      }
    })();
    return polling;
  }
  $("plot-refresh").onclick = poll;
  $('check-paper').onclick=async()=>{
    if($('check-paper').disabled)return;
    const {job,setup}=current(),key=settingsKey();checking=true;error='';render();
    try{
      const result=await api('/api/plotter/check-paper',{method:'POST',...writeOptions(job&&setup?{job_id:job.id,setup:structuredClone(setup)}:{})}).then(r=>r.json());
      paperCheck={...result,key};lastObservation=result.observation.observed_at;observed(result.observation);
    }catch(e){paperCheck={key,error:/No plotter reply|Plotter query failed/.test(e.message)?'Stain connected; plotter not responding. Check plotter power, Remote mode, and the serial connection.':`Plotter check failed: ${e.message}`};}
    finally{checking=false;render();}
  };
  // getRandomValues also works on the HTTP dev origin, unlike randomUUID.
  function uuid() {
    const a = crypto.getRandomValues(new Uint8Array(16));
    a[6] = (a[6] & 15) | 64; a[8] = (a[8] & 63) | 128;
    const h = [...a].map((n) => n.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
  }
  async function send(value, reportFailure=false) {
    if (busy) return;
    busy = true; submitting = true; statusVersion++; error = ""; render();
    try {
      // Set before sending, so reload or a lost HTTP response retains the ID.
      remember(value);
      const url=value.recovery_of?`/api/plots/${value.recovery_of}/recovery`:`/api/jobs/${value.job_id}/plot`;
      const ticket = await api(url, {method: "POST", ...writeOptions(value.request)}).then((r) => r.json());
      accept(ticket);
    } catch (e) {
      if (e.status >= 400 && e.status < 500) remember(null);
      error = pending ? `Could not confirm submission: ${e.message}. Checking its status…` : e.message;
      // An ambiguous response stays in the usual retry/status flow using this
      // same ID. Only a definite rejection permits editing a new request.
      if(reportFailure&&!pending)throw e;
    } finally {
      busy = false; submitting = false; render();
      // Let any older status request finish before fetching the accepted state.
      if (polling) await polling;
      await poll();
    }
  }
  $("plot-open").onclick = () => {
    const { job, setup } = current();
    if (!job || !setup || problem() || busy) return;
    send({job_id: job.id, request: {attempt: uuid(), setup: structuredClone(setup)}});
  };
  $("plot-retry").onclick = () => { if (pending) send(pending); };
  for (const [id, action] of Object.entries({
    "plot-pause": "pause", "plot-resume": "resume", "plot-cancel": "cancel",
    "plot-complete": "complete", "plot-reset": "acknowledge_reset",
  })) $(id).onclick = async () => {
    const e = execution(); if (!e || busy || !ownsActive()) return;
    busy = true; error = ""; render();
    try { await api(`/api/plotter/${action}`, {method:"POST", ...writeOptions({attempt:e.attempt})}); }
    catch (e) { error = e.message; }
    finally { busy = false; await poll(); }
  };
  return { update(issue) { if(shownJob!==current().job?.id){shownJob=current().job?.id;error='';}fitIssue = issue; render(); estimate(); }, poll };
}
