#!/usr/bin/env python3
"""Render a PDF page preview without modifying inspection or its recipe."""
import sys
from pathlib import Path
import pymupdf
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
from pdf_preview import render_page

with pymupdf.open(sys.argv[1]) as document:
    page = document[int(sys.argv[2])]
    sys.stdout.buffer.write(render_page(page).tobytes('png'))
