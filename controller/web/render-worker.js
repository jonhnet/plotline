import { matrix } from "./geometry.js";
// No SVG DOM on the UI thread. Chunked paths keep rendering cancellable and
// allow offscreen geometry to be culled without altering exported vectors.
let layers = [],
  ready = false,
  rasterMix = false;
const pause = () => new Promise((r) => setTimeout(r, 0));
async function load(job, url, thumbnail = false) {
  rasterMix = !!job.pdf?.raster_preview;
  const response = await fetch(url);
  if (!response.ok) throw new Error("Could not fetch preview");
  const svg = await response.text();
  const [x0, y0, x1, y1] = job.summary.bounds,
    cx = (job.pdf || job.calibration) && !thumbnail ? 0 : (x0 + x1) / 2,
    cy = (job.pdf || job.calibration) && !thumbnail ? 0 : (y0 + y1) / 2,
    u = job.units_per_mm;
  const angle=((job.pdf?.registration || job.calibration?.registration)?.rotation || 0)*Math.PI/180,
    c=Math.round(Math.cos(angle)),s=Math.round(Math.sin(angle));
  const groups = svg.matchAll(
    /<g id="(pen-(\d+)|travel)"[^>]*><path[^>]* d="([^"]*)"/g,
  );
  let loaded = 0,
    lastYield = performance.now();
  for (const group of groups) {
    const layer = {
      id: group[1] === "travel" ? 0 : Number(group[2]),
      chunks: [],
    };
    let path = new Path2D(),
      bounds = [Infinity, Infinity, -Infinity, -Infinity],
      count = 0,
      last = null;
    const flush = () => {
      if (count) {
        layer.chunks.push({ path, bounds });
        path = new Path2D();
        bounds = [Infinity, Infinity, -Infinity, -Infinity];
        count = 0;
      }
    };
    for (const token of group[3].matchAll(
      /([ML])(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g,
    )) {
      const px = (Number(token[2]) - cx) / u,
        py = (Number(token[3]) - cy) / u,
        x=c*px+s*py, y=-s*px+c*py;
      if (!count && token[1] === "L" && last) {
        path.moveTo(...last);
        bounds = [last[0], last[1], last[0], last[1]];
      }
      if (token[1] === "M") path.moveTo(x, y);
      else path.lineTo(x, y);
      bounds = [
        Math.min(bounds[0], x),
        Math.min(bounds[1], y),
        Math.max(bounds[2], x),
        Math.max(bounds[3], y),
      ];
      last = [x, y];
      count++;
      loaded++;
      if (count >= 2048) {
        flush();
        if (performance.now() - lastYield > 12) {
          await pause();
          lastYield = performance.now();
        }
      }
    }
    flush();
    layers.push(layer);
  }
  if (job.summary.pens.some((p) => !layers.some((l) => l.id === p.id)))
    throw new Error("Saved SVG has missing ink layers");
  ready = true;
  return loaded;
}
async function render(spec) {
  const start = performance.now();
  const dpr = Math.min(
    2,
    spec.dpr || 1,
    4096 / Math.max(spec.width, spec.height),
  );
  const canvas = new OffscreenCanvas(
    Math.max(1, Math.round(spec.width * dpr)),
    Math.max(1, Math.round(spec.height * dpr)),
  );
  const output = canvas.getContext("2d");
  const inkCanvas = rasterMix ? new OffscreenCanvas(canvas.width,canvas.height) : null;
  let ctx = output;
  const m = matrix(spec.view, spec.width, spec.rotation, spec.facing),
    transform = new DOMMatrix(m);
  ctx.setTransform(...m.map((n) => n * dpr));
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const inv = transform.inverse(),
    corners = [
      [0, 0],
      [spec.width, 0],
      [0, spec.height],
      [spec.width, spec.height],
    ].map(([x, y]) => inv.transformPoint({ x, y }));
  const visible = [
    Math.min(...corners.map((p) => p.x)),
    Math.min(...corners.map((p) => p.y)),
    Math.max(...corners.map((p) => p.x)),
    Math.max(...corners.map((p) => p.y)),
  ];
  const scale = spec.width / spec.view[2];
  let lastYield = performance.now(),
    drawn = 0;
  for (const layer of layers) {
    if (layer.id === 0 ? !spec.travel : !spec.pens.includes(layer.id)) continue;
    if (rasterMix && layer.id !== 0) {
      ctx=inkCanvas.getContext('2d');ctx.reset();
      ctx.setTransform(...m.map(n=>n*dpr));ctx.lineCap='round';ctx.lineJoin='round';
    } else {ctx=output;ctx.setTransform(...m.map(n=>n*dpr));}
    ctx.strokeStyle = layer.id === 0 ? "#6e97ae" : spec.colors[layer.id];
    ctx.globalAlpha = layer.id === 0 ? 0.55 : 1;
    ctx.lineWidth =
      layer.id === 0 ? 0.6 / scale : (spec.widths?.[layer.id] ?? 0.8 / scale);
    const pad = ctx.lineWidth / 2 + 2 / scale;
    ctx.setLineDash(layer.id === 0 ? [3 / scale, 4 / scale] : []);
    for (const chunk of layer.chunks) {
      const b = chunk.bounds;
      if (
        b[2] < visible[0] - pad ||
        b[0] > visible[2] + pad ||
        b[3] < visible[1] - pad ||
        b[1] > visible[3] + pad
      )
        continue;
      ctx.stroke(chunk.path);
      drawn++;
      if (performance.now() - lastYield > 12) {
        await pause();
        lastYield = performance.now();
      }
    }
    if(rasterMix && layer.id!==0) {
      output.save();output.resetTransform();output.globalAlpha=1;
      output.globalCompositeOperation='multiply';output.drawImage(inkCanvas,0,0);output.restore();
    }
  }
  return { canvas, m, ms: performance.now() - start, chunks: drawn };
}
self.onmessage = async ({ data }) => {
  try {
    if (data.type === "load") {
      layers = [];
      ready = false;
      const points = await load(data.job, data.url, data.thumbnail);
      if (data.thumbnail) {
        const turn=((data.job.pdf?.registration || data.job.calibration?.registration)?.rotation || 0)%180;
        const w = Math.max(turn ? data.job.summary.height_mm : data.job.summary.width_mm, 1) * 1.08,
          h = Math.max(turn ? data.job.summary.width_mm : data.job.summary.height_mm, 1) * 1.08;
        const width = Math.max(w, h * 1.25),
          height = width / 1.25;
        const { canvas } = await render({
          width: 150,
          height: 120,
          dpr: 1,
          view: [-width / 2, -height / 2, width, height],
          rotation: 0,
          facing: "artwork",
          pens: data.job.summary.pens.map((p) => p.id),
          colors: Object.fromEntries(
            data.job.summary.pens.map((p) => [p.id, p.color]),
          ),
          widths: Object.fromEntries(
            data.job.summary.pens.map((p) => [p.id, p.width_mm]),
          ),
          travel: false,
        });
        self.postMessage({
          type: "thumbnail",
          blob: await canvas.convertToBlob({ type: "image/png" }),
        });
      } else self.postMessage({ type: "ready", points });
    } else if (data.type === "render" && ready) {
      const { canvas, m, ms, chunks } = await render(data.spec);
      const bitmap = canvas.transferToImageBitmap();
      self.postMessage(
        { type: "frame", bitmap, m, spec: data.spec, ms, chunks },
        [bitmap],
      );
    }
  } catch (error) {
    self.postMessage({ type: "error", message: error.message });
  }
};
