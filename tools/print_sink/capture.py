#!/usr/bin/env python3
"""Retain an IPP submission and inspect its PDF contents. Never invokes plotting."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import pymupdf

ROOT = Path(__file__).resolve().parents[2]


def discard_spool_copy(path, captures, metadata):
    """Drop only a verified duplicate transport PDF, after durable capture."""
    path, captures = Path(path), Path(captures)
    spool = captures.parent / 'spool'
    if (path.is_symlink() or path.parent.is_symlink() or spool.is_symlink()
            or path.parent.parent.resolve() != spool.resolve()
            or not path.parent.name.startswith('session-') or path.suffix != '.pdf'):
        return False
    saved = captures / metadata['id'] / 'source.pdf'
    if saved.is_symlink() or saved.parent.is_symlink():
        return False
    digest = metadata['sha256']
    if hashlib.sha256(saved.read_bytes()).hexdigest() != digest:
        return False
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        return False
    path.unlink()
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def clean_spool(state):
    """Startup migration: retain unmatched inputs and every capture/receipt."""
    captures = state / 'captures'
    by_digest = {}
    for record in captures.glob('*/capture.json'):
        if record.is_symlink() or record.parent.is_symlink():
            continue
        metadata = json.loads(record.read_text())
        if metadata.get('id') == record.parent.name:
            by_digest[metadata['sha256']] = metadata
    for path in (state / 'spool').glob('session-*/*.pdf'):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        metadata = by_digest.get(hashlib.sha256(path.read_bytes()).hexdigest())
        if metadata:
            discard_spool_copy(path, captures, metadata)


def inspect_pdf(path):
    with pymupdf.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError('Expected an unencrypted PDF')
        pages = []
        for page in doc:
            drawings = page.get_drawings()
            text = page.get_texttrace()
            paints = page.get_bboxlog()
            kinds = {entry[0] for entry in paints}
            image_boxes = [entry[1] for entry in paints if entry[0] == 'fill-image']
            # PyMuPDF's image-info helper can synthesize raster images for PDF
            # shadings. Count actual image paint operations, not those proxies.
            info = page.get_image_info() if image_boxes else []
            images = []
            for bounds in image_boxes:
                match = next((i for i in info if all(abs(a-b)<.001 for a,b in zip(i['bbox'],bounds))),{})
                images.append(dict(width=match.get('width'),height=match.get('height'),bbox_pt=list(bounds)))
            pages.append(dict(
                page_index=page.number,
                size_mm=[page.rect.width*25.4/72, page.rect.height*25.4/72],
                media_box_pt=list(page.mediabox), crop_box_pt=list(page.cropbox),
                rotation_deg=page.rotation, vector_paints=len(drawings),
                path_segments=sum(len(d['items']) for d in drawings),
                text_spans=len(text), glyphs=sum(len(t['chars']) for t in text),
                image_placements=len(images),
                images=images,
                paint_kinds=sorted(kinds),
                classification=('mixed' if images and (drawings or text) else
                                'raster-only' if images else
                                'vector/text' if drawings or text else 'empty/other')))
        return dict(pages=pages, note='Counts describe received PDF contents, not fidelity to the original application document.')


def capture(path, root=None, environment=None):
    environment = os.environ if environment is None else environment
    root = Path(environment.get('PLOTLINE_SINK_STATE', ROOT/'state/print-sink'))/'captures' if root is None else Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:12]
    directory = root/identity
    directory.mkdir()
    target = directory/'source.pdf'
    shutil.copyfile(path, target)
    with target.open('rb') as stream:
        os.fsync(stream.fileno())
    metadata = dict(id=identity, received_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    # Retain only document/job facts; never dump process environment.
                    ipp={k:environment[k] for k in [
                        'CONTENT_TYPE','IPP_JOB_ID','IPP_JOB_NAME','IPP_DOCUMENT_NAME',
                        'IPP_MEDIA','IPP_MEDIA_COL','IPP_ORIENTATION_REQUESTED',
                        'IPP_PRINT_SCALING','IPP_PRINTER_RESOLUTION','IPP_PRINT_COLOR_MODE']
                         if k in environment})
    if environment.get('PLOTLINE_SINK_INTAKE') == '1':
        metadata['destination'] = 'plotline'
    try:
        metadata['inspection'] = inspect_pdf(target)
    except Exception as error:
        # Keep the exact input even if inspection fails.
        metadata['inspection_error'] = str(error)
    metadata_path = directory/'capture.json'
    temporary = directory/'capture.json.tmp'
    with temporary.open('w') as stream:
        json.dump(metadata, stream, indent=2); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(metadata_path)
    for saved_directory in (directory, root):
        descriptor = os.open(saved_directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    discard_spool_copy(path, root, metadata)
    return directory, metadata


if __name__ == '__main__':
    if sys.argv[1:] == ['--clean-spool']:
        clean_spool(Path(os.environ.get('PLOTLINE_SINK_STATE', ROOT/'state/print-sink')))
        raise SystemExit(0)
    directory, result = capture(Path(sys.argv[1]))
    print(f'INFO: Captured PDF for review: {directory.name}', file=sys.stderr)
    if 'inspection' in result:
        for page in result['inspection']['pages']:
            print(f"INFO: Page {page['page_index']+1}: {page['classification']}; "
                  f"{page['vector_paints']} vector paints, {page['glyphs']} glyphs, "
                  f"{page['image_placements']} images", file=sys.stderr)
