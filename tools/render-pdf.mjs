import fs from 'node:fs';
import mupdf from 'mupdf';
const [input, output, page = '1', dpi = '216'] = process.argv.slice(2);
if (!input || !output) throw new Error('Usage: node tools/render-pdf.mjs input.pdf output.png [page=1] [dpi=216]');
try {
  const doc=mupdf.Document.openDocument(fs.readFileSync(input),'application/pdf');
  const p=doc.loadPage(Number(page)-1);
  const scale=Number(dpi)/72;
  if (!(scale>0 && Number.isFinite(scale))) throw new Error('Invalid DPI');
  const pixmap=p.toPixmap(mupdf.Matrix.scale(scale,scale),mupdf.ColorSpace.DeviceRGB,false);
  fs.writeFileSync(output,pixmap.asPNG());
  pixmap.destroy();p.destroy();doc.destroy();
} catch (error) {console.error(error.message);process.exitCode=1;}
