// A replaceable MuPDF probe. JSON contains owned data, never engine handles.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import mupdf from 'mupdf';

function pathData(path) {
  const commands = [];
  path.walk({
    moveTo: (x, y) => commands.push({ op: 'move', p: [x, y] }),
    lineTo: (x, y) => commands.push({ op: 'line', p: [x, y] }),
    curveTo: (x1, y1, x2, y2, x3, y3) => commands.push({ op: 'cubic', p1: [x1, y1], p2: [x2, y2], p3: [x3, y3] }),
    closePath: () => commands.push({ op: 'close' }),
  });
  return commands;
}

function strokeData(stroke) {
  // This is the pinned 1.28.1 API; newer online docs describe different getters.
  return {
    width: stroke.getLineWidth(),
    cap: mupdf.StrokeState.LINE_CAP[stroke.getLineCap()],
    join: mupdf.StrokeState.LINE_JOIN[stroke.getLineJoin()],
    miter_limit: stroke.getMiterLimit(),
    dash_phase: stroke.getDashPhase(),
    dashes: stroke.getDashes() ?? [],
  };
}

export function extract(bytes, pageNumber = 1) {
  const doc = mupdf.Document.openDocument(bytes, 'application/pdf');
  if (!doc.isPDF() || doc.needsPassword()) throw new Error('Expected an unencrypted PDF');
  if (!Number.isInteger(pageNumber) || pageNumber < 1 || pageNumber > doc.countPages()) throw new Error('Page number out of range');
  const page = doc.loadPage(pageNumber - 1);
  const bounds = page.getBounds();
  const events = [];
  const unsupported = (feature, detail = '') => events.push({ kind: 'unsupported', feature, detail });
  const colorData = (cs, color, alpha) => {
    if (alpha !== 1) unsupported('paint alpha', String(alpha));
    if (cs.getName() === 'DeviceRGB') return [...color];
    if (cs.getName() === 'DeviceGray') return [color[0], color[0], color[0]];
    unsupported('color space', cs.getName());
    return [0, 0, 0];
  };
  // Overprint and transfer functions are not present in JS device callbacks.
  // Conservatively scan every PDF dictionary, including unused resources.
  const pdf = doc.asPDF();
  const scan = (obj, depth = 0) => {
    if (depth > 64) throw new Error('PDF object nesting exceeds probe limit');
    if (obj.isIndirect()) return; // Each indirect object is scanned below.
    if (obj.isDictionary() || obj.isStream()) {
      if (obj.get('OP').asBoolean() || obj.get('op').asBoolean()) unsupported('overprint');
      for (const key of ['TR', 'TR2']) {
        const value = obj.get(key);
        if (!value.isNull() && !['Identity', 'Default'].includes(value.asName())) unsupported('transfer function', key);
      }
      obj.forEach(value => scan(value, depth + 1));
    } else if (obj.isArray()) obj.forEach(value => scan(value, depth + 1));
  };
  for (let i = 1; i < pdf.countObjects(); i++) scan(pdf.newIndirect(i).resolve());
  const callbacks = {
    fillPath(path, evenOdd, matrix, cs, color, alpha) {
      const rgb = colorData(cs, color, alpha);
      events.push({ kind: 'fill', path: pathData(path), matrix: [...matrix], even_odd: evenOdd, color: rgb });
    },
    strokePath(path, stroke, matrix, cs, color, alpha) {
      const rgb = colorData(cs, color, alpha);
      events.push({ kind: 'stroke', path: pathData(path), matrix: [...matrix], style: strokeData(stroke), color: rgb });
    },
    clipPath(path, evenOdd, matrix) {
      events.push({ kind: 'clip_fill', path: pathData(path), matrix: [...matrix], even_odd: evenOdd });
    },
    clipStrokePath(path, stroke, matrix) {
      events.push({ kind: 'clip_stroke', path: pathData(path), matrix: [...matrix], style: strokeData(stroke) });
    },
    popClip() { events.push({ kind: 'pop_clip' }); },
    beginGroup(_box, _cs, _isolated, knockout, blend, alpha) {
      if (knockout || blend !== 'Normal' || alpha !== 1) unsupported('compositing group', JSON.stringify({ knockout, blend, alpha }));
    },
    endGroup() {},
    beginLayer() {}, endLayer() {},
    ignoreText() {}, // PDF invisible text contributes no paint or clip.
    beginTile() { unsupported('tiling pattern'); return 0; },
    endTile() {},
  };
  for (const name of ['fillText', 'strokeText', 'clipText', 'clipStrokeText', 'fillShade', 'fillImage', 'fillImageMask', 'clipImageMask', 'beginMask', 'endMask']) {
    callbacks[name] = () => unsupported(name);
  }
  const device = new mupdf.Device(callbacks);
  try {
    page.run(device, mupdf.Matrix.identity);
    device.close();
    return { version: 1, engine: 'MuPDF.js 1.28.1', units: 'pt', y_axis: 'down', bounds, events };
  } finally {
    device.destroy(); page.destroy(); doc.destroy();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [input, output, page = '1'] = process.argv.slice(2);
  if (!input || !output) throw new Error('Usage: node tools/pdf-events.mjs input.pdf output.json [page=1]');
  const result = extract(fs.readFileSync(input), Number(page));
  fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n');
  const counts = {};
  for (const event of result.events) {
    const key = event.kind === 'unsupported' ? `unsupported:${event.feature}` : event.kind;
    counts[key] = (counts[key] ?? 0) + 1;
  }
  console.log(JSON.stringify({ engine: result.engine, bounds: result.bounds, counts }));
}
