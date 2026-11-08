import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import mupdf from 'mupdf';

export function makePdf(content, resources = {}, bounds = [0, 0, 300, 220]) {
  const doc = new mupdf.PDFDocument();
  try {
    doc.insertPage(-1, doc.addPage(bounds, 0, resources, content));
    const buffer = doc.saveToBuffer('compress');
    try { return Uint8Array.from(buffer.asUint8Array()); }
    finally { buffer.destroy(); }
  } finally { doc.destroy(); }
}

export const roadContent = `
% Everything is ordinary PDF paint: there is no road-specific input semantic.
1 J 1 j
0.12 0.32 0.46 RG 18 w 28 62 m 272 158 l S
1 1 1 RG 12 w 28 62 m 272 158 l S
0.58 0.23 0.13 RG 16 w 150 22 m 150 198 l S
1 1 1 RG 10 w 150 22 m 150 198 l S
% A later white sign cuts both surviving casing edges. The green sign is drawn last.
1 1 1 rg 195 112 32 30 re f
0.16 0.42 0.25 rg 201 118 20 18 re f
`;

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const out = process.argv[2] ?? 'build/fixtures';
  fs.mkdirSync(out, { recursive: true });
  const fixtures = {
    roads: roadContent,
    'clip-and-holes': `q 25 25 250 170 re 90 70 70 80 re W* n
      q 25 25 m 275 60 l 240 195 l h W n
      0.12 0.32 0.46 rg 0 0 300 220 re f Q Q`,
    'curves-and-dashes': `q 1.5 .2 .3 .7 10 20 cm
      0.2 0.4 0.2 RG 2 J 1 j 5 w [12 7] 4 d
      10 20 m 60 180 120 -40 160 200 c S Q`,
    'winding-rules': `0.2 0.4 0.7 rg 15 30 110 160 re 40 65 60 90 re f
      0.7 0.3 0.2 rg 175 30 110 160 re 200 65 60 90 re f*`,
  };
  for (const [name, content] of Object.entries(fixtures)) fs.writeFileSync(`${out}/${name}.pdf`, makePdf(content));
  const doc = mupdf.Document.openDocument(fs.readFileSync(`${out}/roads.pdf`), 'application/pdf');
  const page = doc.loadPage(0);
  const pixmap = page.toPixmap(mupdf.Matrix.scale(3, 3), mupdf.ColorSpace.DeviceRGB, false);
  fs.writeFileSync(`${out}/roads-reference.png`, pixmap.asPNG());
  pixmap.destroy(); page.destroy(); doc.destroy();
  console.log(`Wrote ${Object.keys(fixtures).length} PDFs and independent PDF raster to ${out}`);
}
