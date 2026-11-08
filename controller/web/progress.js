// Pen colors describe the frozen serial stream; the marker describes bytes sent.
export function duration(seconds) {
  if (seconds < 60) return "less than a minute";
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}
export function plotProgress({ api }) {
  const $ = (id) => document.getElementById(id), track = $("plot-progress"),
    canvas = $("plot-pen-bands");
  let attempt = null, total = 0, sent = 0, data = null, loading = null, activeData = null, mode = 'preview';
  let clockKey = null, remaining = null, executionState = "", sampled = 0;
  function drawTime() {
    const el = $("plot-time");
    if (mode !== 'active' || remaining === null || !["feeding", "pausing", "paused", "finishing"].includes(executionState)) { el.textContent = ""; return; }
    const running = ["feeding", "finishing"].includes(executionState);
    const seconds = remaining - (running ? (performance.now() - sampled) / 1000 : 0);
    el.textContent = seconds > 0
      ? `${running ? "" : "Paused · "}${seconds < 60 ? "Less than a minute" : `About ${duration(seconds)}`} remaining`
      : (executionState === "finishing" ? "Estimate elapsed · waiting for the plotter to confirm completion" : "Taking longer than estimated · still feeding");
  }
  setInterval(drawTime, 1000);
  const percent = () => Math.min(100, Math.max(0, 100 * sent / Math.max(1, total)));
  function sectionAt(byte) {
    if (!data || byte >= data.total_bytes) return null;
    // Lower-bound search also handles externally supplied, interleaved pens.
    let lo = 0, hi = data.sections.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (data.sections[mid].end_byte <= byte) lo = mid + 1;
      else hi = mid;
    }
    return data.sections[lo] || null;
  }
  function drawBands() {
    const width = Math.max(1, Math.round(track.clientWidth * devicePixelRatio));
    canvas.width = width; canvas.height = Math.max(1, Math.round(18 * devicePixelRatio));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#326e68"; ctx.fillRect(0, 0, width, canvas.height);
    if (data?.total_bytes) for (const section of data.sections) {
      ctx.fillStyle = section.color;
      const x = Math.round(width * section.start_byte / data.total_bytes),
        end = Math.round(width * section.end_byte / data.total_bytes);
      ctx.fillRect(x, 0, end - x, canvas.height);
    }
  }
  function describe() {
    const active = mode === 'active';
    track.dataset.mode = mode;
    track.setAttribute('role', active ? 'progressbar' : 'img');
    track.setAttribute('aria-label', active ? 'Plot data sent' : 'Planned pen sequence');
    $("plot-progress-unsent").hidden = !active;
    $("plot-progress-marker").hidden = !active;
    if (!active) {
      track.removeAttribute('aria-valuenow');
      track.removeAttribute('aria-valuetext');
      track.title = 'Planned pen order · section widths represent prepared data';
      return;
    }
    const p = percent(), section = sectionAt(sent);
    $("plot-progress-unsent").style.left = `${p}%`;
    $("plot-progress-marker").style.left = `${p}%`;
    track.setAttribute("aria-valuenow", p.toFixed(1));
    const text = `Data sent: ${Math.round(p)}% · ${(sent / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} KiB${section ? ` · ${section.label}` : ""}`;
    track.setAttribute("aria-valuetext", text);
    track.title = section ? `${section.label} · data sent, not physical pen position` : "Data sent; buffered movement may still be drawing";
  }
  async function load(id) {
    if (loading === id) return;
    loading = id;
    try {
      const result = await api(`/api/plots/${id}/progress`).then((r) => r.json());
      if (attempt !== id) return;
      if (result.total_bytes > 0) {
        activeData = result;
        if (mode === 'active' && result.total_bytes === total) { data = result; drawBands(); describe(); }
      }
    } catch { /* Keep the plain progress bar if colors are unavailable. */ }
    finally { if (loading === id) loading = null; }
  }
  track.onpointermove = (event) => {
    const box = track.getBoundingClientRect();
    const byte = Math.max(0, Math.min(total - 1, (event.clientX - box.left) / box.width * total));
    const section = sectionAt(byte);
    if (section) track.title = `${section.label} · ${(100 * section.start_byte / total).toFixed(1)}–${(100 * section.end_byte / total).toFixed(1)}% of data`;
  };
  track.onpointerleave = describe;
  new ResizeObserver(drawBands).observe(track);
  return {
    preview(value) {
      mode = 'preview';data = value;total = value?.total_bytes || 0;sent = 0;
      track.hidden = !total;drawBands();describe();drawTime();
    },
    update(ticket, execution, serverTime) {
      mode = 'active';
      const key = JSON.stringify([ticket?.id, execution?.state, execution?.timing, execution?.cancelled, serverTime]);
      if (key !== clockKey) {
        clockKey = key; executionState = execution?.state || ""; sampled = performance.now();
        const timing = execution?.cancelled ? null : execution?.timing;
        remaining = timing ? timing.seconds : null;
        if (timing && ["feeding", "finishing"].includes(executionState))
          remaining -= Math.max(0, (serverTime || timing.updated_at_ms) - timing.updated_at_ms) / 1000;
      }
      drawTime();
      if (attempt !== ticket?.id) {
        attempt = ticket?.id; activeData = null;
      }
      const redraw = data !== activeData;data = activeData;if (redraw) drawBands();
      total = execution?.total_bytes || 0;
      sent = Math.max(0, Math.min(total, execution?.submitted_bytes || 0));
      track.hidden = !total;
      describe();
      if (attempt && total && !data) load(attempt);
    },
  };
}
