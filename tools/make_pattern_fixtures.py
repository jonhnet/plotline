#!/usr/bin/env python3
"""Small PDF semantics regressions for the full-map features."""
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parent.parent


def write_pdf(destination, content, patterns=None, colorspaces=""):
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=220)
        resources = []
        for name, specification, commands in patterns or []:
            xref = doc.get_new_xref()
            doc.update_object(xref, specification)
            doc.update_stream(xref, commands.encode())
            resources.append(f"/{name} {xref} 0 R")
        resource = doc.get_new_xref()
        doc.update_object(resource, f"<</Pattern <<{' '.join(resources)}>> {colorspaces}>>")
        doc.xref_set_key(page.xref, "Resources", f"{resource} 0 R")
        stream = doc.get_new_xref()
        doc.update_object(stream, "<<>>")
        doc.update_stream(stream, content.encode())
        page.set_contents(stream)
        doc.save(destination, deflate=True)


def make_fixtures(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    pattern = "<</Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 12 12] /XStep 15 /YStep 15 /Matrix [0.866 0.5 -0.5 0.866 3 4] /Resources <<>>>>"
    uncolored = "<</Type /Pattern /PatternType 1 /PaintType 2 /TilingType 1 /BBox [0 0 10 10] /XStep -13 /YStep 13 /Matrix [1 0.2 0.3 1 0 0] /Resources <<>>>>"
    write_pdf(destination / "patterns.pdf", """
0.9 0.9 0.9 rg 10 10 280 200 re f
q 20 20 120 180 re 55 75 50 60 re W* n
/Pattern cs /P scn 0 0 150 220 re f Q
q 160 20 120 180 re W n
/PC cs 0.7 0.2 0.1 /U scn 150 0 150 220 re f Q
1 1 1 rg 5 105 290 12 re f
""", [("P", pattern, "0.1 0.4 0.7 RG 1.2 w 1 J 2 2 m 10 10 l 2 10 l S"),
      ("U", uncolored, "1 1 4 4 re f 1.2 w 1 8 m 9 8 l S")],
      "/ColorSpace <</PC [/Pattern /DeviceRGB]>>")
    write_pdf(destination / "miter-limits.pdf", """
0.1 0.3 0.5 RG 8 w 0 J 0 j 2 M
20 30 m 130 30 l 30 35 l 60 65 l 20 65 l S
170 30 m 280 30 l 180 35 l 210 65 l 170 65 l h S
q 1.1 0.1 0.2 0.7 5 85 cm 0.6 0.2 0.1 RG [18 3] 1 d
20 30 m 130 30 l 30 35 l 60 65 l 20 65 l S Q
1 1 1 rg 65 15 6 180 re f
""")
    # Contents deliberately extend outside the cell BBox; repeated cells must
    # remain separated by the specified gap. This catches NO_TILING's lost clip.
    spill = "<</Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 10 10] /XStep 20 /YStep 20 /Resources <<>>>>"
    write_pdf(destination / "pattern-bbox.pdf",
              "/Pattern cs /P scn 20 20 260 180 re f", [("P", spill, "0.2 0.4 0.6 rg -5 -5 20 20 re f")])


if __name__ == "__main__":
    make_fixtures(ROOT / "build/fixtures")
    print("Created pattern and PDF miter-limit fixtures")
