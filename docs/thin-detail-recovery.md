# Thin details with broad pens

PDF Studio can preserve fine lines and lettering when the selected physical pen
cannot fit inside their original outlines. **Thin-detail thickening** is separate
from the existing stroke-width tolerance: the latter plans ordinary strokes;
the new allowance also handles narrow portions of filled glyphs and shapes.

New documents default to **0.75**; saved recipes keep their existing setting.
Generate a pen preview, then inspect **Highlight lost details**.
Zero retains previous conversion paths; 1 allows more growth. Values are extra
total thickness in pen diameters. With a 0.5 mm pen, 0.75 permits 0.375 mm total
growth, or 0.1875 mm on either side. Remaining losses may need a finer pen, larger
lettering or different source artwork. Heavy letters can touch: this is a
deliberate approximation, not a promise of font fidelity.

The checkbox paints animated red diagonal stripes around missing fragments,
including a missing crossbar in an otherwise surviving letter. Regions grow by
0.35 mm and merge, making small defects visible at page zoom. It works over the
source, logical paint and decoded pen views. The checkbox is a view preference,
does not rerun conversion, and never changes PDF/HPGL. Reduced-motion preferences
stop the stripes. Revisions with the older, overly sensitive detector remain
usable and offer **Update pen preview** to compute the focused diagnostic.

## Geometry and hard limits

1. Keep logical source coverage unchanged. Construct a separate pen allowance
   expanded by half the permitted total growth, then constrain it by the original
   clips, page/printable limits and later logical paints, including white masks.
2. Run existing path planning with its original width allowance. Intersect
   unrepresented coverage with the narrow parts that a strict pen cannot reach.
   Deliberate broad hatch gaps and omitted fills do not request recovery.
3. Reuse original stroke centerlines, including dashes, where available. For
   remaining filled details, sample polygon boundaries and use GEOS Voronoi
   edges between opposing boundary sites as an approximate medial graph. Remove
   sampling ribs and short attached terminal branches; retain stems and loops.
4. Restrict expensive medial work to neighborhoods of missing details. Components
   above 40,000 estimated samples or original boundary coordinates are skipped,
   leaving their losses in the diagnostic. Clip candidate centerlines to the
   allowed region eroded by the physical pen radius and rounding guard.
5. Independently decode HPGL and verify its finite-width footprints. Permitted
   growth outside source coverage is reported separately; original hard
   occlusion/placement checks still apply. Compare decoded ink with visible
   narrow fragments to make the loss mask.

Diagnostics ignore hidden/white paint, deliberately omitted fills, intentional
hatch gaps and numerical residue. They ignore discrepancies within half a pen
diameter of surviving ink, then prioritize substantial missing parts. Ordinary
fragments shorter than two pen diameters are omitted. Map Studio's explicitly
attributed forest hatching and hillshade use five diameters, so their short
texture remnants do not overwhelm the map. These are attention thresholds, not
new allowances for the converter: real small losses can be intentionally omitted
from this view. A whole missing short texture can therefore escape this check.
Native text and explicitly attributed map labels retain the finer 0.6-diameter
threshold; entirely missing glyphs and dots are always eligible. This is a
geometric heuristic, not an OCR, legibility or texture-completeness test.

Sparse fragment bounding boxes are recursively subdivided along the actual
missing geometry: a long diagonal or curved line must not highlight the whole
rectangle around it. Nearby rectangles are merged before rounding their corners,
avoiding a costly union of thousands of finely rounded islands. Its white alpha
mask is separate from the artwork;
animation and red color belong to the browser. The mask shares the exact review
coordinate frame, including paper outside the source page.

## API and retained artifacts

- Optional `policies.thin_detail_thickening`: number 0–1, absent means 0.
- `implemented.thin_detail_thickening` advertises support in capabilities.
- Each conversion page's `detail_loss` reports detector `version: 2`,
  `affected_paints`, local warning-box `zones` before merging, `recovered_paints`
  and `display_padding_mm`. PDF Studio displays the affected-mark count.
- `artifacts.detail_loss` points to transparent `loss.svg`, served alongside
  `source.svg`, `logical.svg` and `pen.svg`. Older conversions may omit it.
- Full reports retain per-paint `missing_detail_bounds` in sheet points and
  mark paints receiving recovered paths with `width_status: thin_detail_recovery`.
- The artifact phase starts before loss-region construction. Both preview and
  full modes retain verification and the mask.

## Reproduce and validate

Use the native and contract dependencies from the root README, plus Playwright
from the controller setup. The fixture uses system DejaVu Sans
(`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`, Debian `fonts-dejavu-core`).
It creates an embedded-font PDF with 8–11 pt labels, a 0.15 mm line, a partially
representable H and a later white cut; the requested pen is 0.5 mm.

```sh
cargo build --release --locked
cargo build --release --locked --manifest-path controller/Cargo.toml
cargo test --locked
.venv/bin/python -m unittest discover -s tests -p test_thin_detail.py
node controller/scripts/thin-detail-smoke.mjs
```

The browser script starts its own service with no device connection and retains
screenshots/results under ignored `build/thin-browser-*`. Generate just the
fixture with `.venv/bin/python tools/make_thin_fixture.py build/thin-labels`.

Validated on 2026-11-02:

- Five Rust regressions cover one-pass narrow strokes, I/O glyph stems and loops,
  partial H loss, intentional hatch gaps, clipping, later white cuts, hidden
  paint and invalid settings. The complete Rust suite has 56 passing tests.
- Three real-PDF tests recover more than 90% of glyphs at allowance 1, preserve
  source/logical SVG bytes, and independently buffer Python-decoded HPGL to check
  white cuts and printable bounds. The existing 17 PDF worker tests also pass.
- The browser fixture drops from **46 missing fragments across 43 paints** at
  zero to **3 fragments across 2 paints** at 0.75, with 42 paints receiving
  recovery. Actual screenshots verify animation and reduced motion, alignment
  across views/zoom, persisted settings, old-preview refresh and no conversion
  when toggling the overlay.
- A retained map with **138,133 paints and eight inks** keeps byte-identical
  HPGL at zero. At allowance 1, the optimized converter takes **32.56 seconds**
  wall time, including geometry, verification, mask construction and writing
  artifacts, with **2.40 GiB** peak RSS. Planning takes **11.60 seconds**;
  recovery is additional work, not a free preview option. This is one standalone
  run, not end-to-end HTTP latency or a controlled timing comparison.
- That large run recovers 1,061 paints, emits 53,904 decoded paths, and checks all
  138,133 paints. Maximum footprint excess beyond hard allowed geometry is
  5.35e-11 pt², below the existing 1e-7 pt² numerical threshold; required coverage
  has no deficit. Many fine clipped fragments remain, as expected with hard
  occlusion boundaries. Evidence is in ignored `build/thin-large-optimized` and
  `build/thin-large-optimized.log`; original inputs/revisions are unchanged.

### Focused diagnostic correction

The initial overlay was not useful on the actual map: **53,397** of its **57,849**
flagged paints were short forest hatch remnants. The 0.7 mm expansion, and large
bounding rectangles around curved remnants, made the warning cover **59.85%**
of the sheet. Those initial large-map checks verified geometry safety but did
not establish the diagnostic's usefulness. The policy above supersedes that
initial detector; the earlier recovery/containment results still apply.

Replaying retained revision `e5056fe8-b8dd-4c29-b882-31ad09b869d6` with exactly
its existing settings now flags **1,328 paints**, mostly missing road casings
and longer boundary/contour portions. Warning coverage falls to **7.18%** of the
sheet, measured with the same SVG frame, MuPDF rasterization and alpha >127.
Restored park/place/road labels remain unflagged. HPGL and decoded pen SVG match
the previous output byte for byte: neither text recovery nor print output changes.

Three new Rust regressions cover ordinary edge shortfall versus a missing middle,
texture/label priorities (including a wholly missing dot), and a diagonal whose
warning must follow the line rather than its page-sized bounds. The full suite
now has 59 passing tests. Real-font checks and both browser suites pass; the
browser regression also checks refresh of an existing version-1 loss overlay.
Evidence is in ignored `build/loss-audit-before.json`, `build/loss-audit-after.json`,
`build/loss-map-replay/`, `build/loss-map-after.png` and `build/loss-*.log`.

The controller's 40 tests and its existing PDF browser smoke test pass. All
validation is offline or uses isolated preview-only services; no physical plot
is started.
