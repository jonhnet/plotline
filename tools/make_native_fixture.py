#!/usr/bin/env python3
"""Exercise native CMYK conversion, glyph transforms, white text and text strokes."""
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parent.parent


def main():
    content = b"""
0 0.7 0.9 0 k 20 140 260 60 re f
q 1 1 1 rg BT /hebo 36 Tf 1 0 0 1 35 155 Tm (MASK) Tj ET Q
q 0.1 0.3 0.5 rg 1 0.2 0.25 0.8 25 90 cm
BT /hebo 30 Tf 0 0 Td (Affine) Tj ET Q
q 0.1 0.3 0.5 RG 1 w 1 j
BT /hebo 30 Tf 1 Tr 1 0 0 1 25 35 Tm (Stroke) Tj ET Q
1 1 1 rg 98 24 5 100 re f
"""
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=220)
        page.insert_font(fontname="hebo")
        stream = doc.get_new_xref()
        doc.update_object(stream, "<<>>")
        doc.update_stream(stream, content)
        page.set_contents(stream)
        destination = ROOT / "build/fixtures/native-operations.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        doc.save(destination, deflate=True)
    print(f"Created {destination}")


if __name__ == "__main__":
    main()
