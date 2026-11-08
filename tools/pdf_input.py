"""Wrap retained JPEG/PNG input in a one-image PDF without resizing its pixels."""
import io
import math
from pathlib import Path

from PIL import Image, ImageOps
import pymupdf


def image_pdf(source, destination):
    raw = Path(source).read_bytes()
    with Image.open(io.BytesIO(raw)) as original:
        if original.format not in ('JPEG', 'PNG'):
            raise ValueError('Choose a PDF, JPEG or PNG image')
        if getattr(original, 'n_frames', 1) != 1:
            raise ValueError('Animated PNG is not supported; export a single frame')
        if original.width * original.height > 40_000_000:
            raise ValueError('Image exceeds the 40 megapixel import limit; original retained')
        dpi = original.info.get('dpi', (300, 300))
        if not isinstance(dpi, (list, tuple)) or len(dpi) != 2:
            dpi = (300, 300)
        dpi = [float(v) if isinstance(v, (float, int)) and math.isfinite(v) and 10 <= v <= 2400 else 300. for v in dpi]
        orientation = original.getexif().get(274, 1)
        image = ImageOps.exif_transpose(original)
        if orientation in (5, 6, 7, 8): dpi.reverse()
        transparent = 'A' in image.getbands() or 'transparency' in image.info
        if transparent:
            rgba = image.convert('RGBA')
            image = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
        # The ordinary JPEG case embeds the original compressed stream exactly.
        # Orientation/alpha adjustments use lossless PNG at the native grid.
        payload = raw
        if orientation not in (None, 1) or transparent:
            if image.mode == 'CMYK': image = image.convert('RGB')
            stream = io.BytesIO(); image.save(stream, format='PNG'); payload = stream.getvalue()
        width, height = image.size
    with pymupdf.open() as doc:
        page = doc.new_page(width=width*72/dpi[0], height=height*72/dpi[1])
        page.insert_image(page.rect, stream=payload, keep_proportion=False)
        temporary = Path(destination).with_suffix('.tmp')
        temporary.write_bytes(doc.tobytes(garbage=4, deflate=True, no_new_id=True))
        temporary.replace(destination)
    return dict(pixels=[width, height], resolution_ppi=dpi,
                exif_orientation=orientation, transparency_on_white=transparent)
