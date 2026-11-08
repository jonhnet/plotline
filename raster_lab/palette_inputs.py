"""Capture local palette-study inputs without changing the original portfolio.

Run with the PDF environment's Python. The uploaded PDF remains authoritative;
extract its one image losslessly, alongside a reproducible synthetic ramp.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def capture(state, pdf):
    import pymupdf
    inputs = state / 'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as doc:
        images = doc[0].get_images(full=True)
        if len(images) != 1:
            raise ValueError('This challenge expects a single-image PDF; inspect other layouts explicitly')
        data = doc.extract_image(images[0][0])
    from io import BytesIO
    photo = Image.open(BytesIO(data['image'])).convert('RGB')
    # Rows: blue/violet, red/orange, grayscale; each fades from white toward
    # saturated color and then toward black. No lettering enters the screen.
    hue = np.linspace(150, 280, 1000)/360
    import colorsys
    ramps = []
    for hues in (hue, np.linspace(300, 390, 1000)/360, np.zeros(1000)):
        base = np.array([colorsys.hsv_to_rgb(h % 1, 1, 1) for h in hues])
        if len(ramps) == 2:
            base[:] = .5
        tone = np.linspace(0, 1, 200)[:, None, None]
        band = np.where(tone < .5, 1-2*tone*(1-base), 2*(1-tone)*base)
        ramps.append(band)
    chart = Image.fromarray(np.uint8(np.round(np.clip(np.concatenate(ramps), 0, 1)*255)))
    sources = json.loads((state / 'sources.json').read_text())
    selected = [s for s in sources['images'] if s['id'] in
                ('my-love', 'happy-shreksgiving', 'shrek-5-felicia', 'swamp-at-dawn', 'felicia-2020')]
    extra = []
    for id, title, artist, image in (
            ('img-0456', 'IMG_0456 · uploaded photograph', 'User-supplied PDF', photo),
            ('palette-ramps', 'Blue, violet, red and neutral ramps', 'Plotline diagnostic', chart)):
        output = BytesIO(); image.save(output, format='PNG'); content = output.getvalue()
        file = inputs / (id + '.png')
        if file.exists() and file.read_bytes() != content:
            raise ValueError('Preserved input differs; choose a new challenge ID: ' + id)
        if not file.exists():
            file.write_bytes(content)
        item = dict(id=id, title=title, artist=artist, file=file.name, pixels=list(image.size),
                    bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        if id == 'img-0456':
            item['source_pdf_sha256'] = hashlib.sha256(pdf.read_bytes()).hexdigest()
            item['source_image_xref'] = images[0][0]
        extra.append(item)
    manifest = dict(note='Palette study only; original eight sources and comments remain unchanged.',
                    images=extra+selected)
    path = state / 'palette-sources.json'
    content = json.dumps(manifest, indent=2)+'\n'
    if path.exists() and path.read_text() != content:
        raise ValueError('Palette source manifest differs; preserve it before changing inputs')
    if not path.exists():
        path.write_text(content)
    print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--pdf', type=Path, required=True)
    args = parser.parse_args()
    capture(args.state, args.pdf)


if __name__ == '__main__':
    main()
