# Raster images in PDF Studio

**Add document** accepts PDF, SVG, JPEG and PNG. SVG imports preserve vector
text/strokes and embedded image pixels through Inkscape's PDF export; see the
[SVG intake contract](../controller/README.md#setup-and-checks). JPEG and PNG
can also be uploaded directly. The original upload remains
downloadable byte for byte. A one-image PDF supplies the existing page-placement
boundary without resizing the image: ordinary JPEGs retain their compressed
stream; opaque PNGs retain their pixel grid. EXIF rotation/reflection is applied,
and transparent PNG pixels are composited onto white paper at native resolution.
Embedded density sets the initial physical size; missing/invalid density uses
300 pixels/inch. The source-size note shows pixels, density and physical size.
Choose the target sheet and **Fit page inside printable area** as usual.

New standalone images start with **Draw with pens**, the available pen palette,
100% fullness, and black recovery off. Those are editable defaults; existing PDFs
and saved recipes keep their choices. Image uploads accept one static JPEG/PNG,
up to the existing 64 MiB upload limit and 40 megapixels decoded; animated PNG
is rejected explicitly. Print-intent sidecars remain PDF-only.

Source previews use a 1600-pixel long edge; positioned source comparisons use
2200 pixels. Neither is capped by a fixed PDF DPI: a small page containing a
300-ppi image must not become a tiny thumbnail stretched across the viewport.
These bounded display rasters do not feed pen conversion. Existing unconverted
documents lazily regenerate the versioned preview without changing their saved
source, inspection or settings. Earlier immutable conversion previews retain
their original rendering until a new conversion is requested.

In PDF Studio, open **Images & effects → Raster images → Draw with pens**.
A dedicated **Images & effects** section sits alongside **Fills & strokes**,
always expanded. A fresh image PDF shows an inline prompt when
Raster images is still **Require a decision**. Preview reports the specific
choice needed, including for older saved failures that said only `raster image:`.
This interprets opaque PDF images locally; existing PDF paths and text keep their
vector route. Generate a preview, then use **Prepare plot →** to make an HPGL
Studio draft. Receiving, converting and preparing do not start physical plotting.

The controls are:

- **Screening:** independently enable AM/FM shading with longitudinal error
  diffusion in the lightest tones.
- **Screen inks:** Selected pens, Auto, K, CMY or CMYK. **Selected pens · optimized
  color** mixes one to eight actual inks, including extra blue/red or other
  non-CMY inks. Check loaded slots individually or use **Use loaded pens** to
  select the usable carousel. No inks are invented. Auto uses pixel color statistics, choosing
  K for monochrome and CMY for color. The completed preview reports the choice.
  It does not identify comics or choose a black treatment.
- **Ink fullness:** the fraction of reference shading retained away from edges.
  0% keeps edge emphasis; 100% shades throughout. This is not a coverage or
  travel percentage. The reviewed reference still uses 70% peak channel demand.
- **Edge falloff:** distance in decimal inches over which edge emphasis relaxes
  toward the interior fullness. Disabled at 100% fullness or with screening off.
- **Black treatment:** Off, Contrast strokes (ridge recovery), or Ink shapes
  (joined fill). Choose ink shapes explicitly for monochrome line-art comics.
  The neutral-mask experiment is not a user-facing option. Off disables recovered
  black marks; K/CMYK screening may still use black.
- **Channel pens:** for Auto/K/CMY/CMYK, assign each needed role to a document pen or a loaded slot.
  C can use turquoise. The displayed name, swatch and diameter remain the actual
  material. No inventory entries are fabricated or rewritten. Missing required
  assignments fail with an actionable error instead of silently substituting ink.
  Selected pens needs no CMY role assignments; a K-role assignment is needed only
  when black recovery is enabled. Screening and black recovery remain independent.

**Use loaded pens** selects the screening palette. When **Black treatment** is
Contrast strokes or Ink shapes, also choose **Black treatment pen** below it.
This is an explicit document-ink assignment, not a lookup for a pen named K or
Black. Any loaded pen name works; switching Black treatment to Off removes that
requirement for Selected pens and CMY. K/CMYK screening still needs its black
channel pen. Missing required assignments are explained beside the controls and
block Preview/Prepare before submitting work. API validation returns the same
operator guidance without exposing a Python traceback. Calibration updates to
a selected slot's width preserve its role assignment.

The current choices apply to all images on the converted page. A document can
retain these choices as a saved recipe, undo edits, restore after browser reload,
and rebuild an immutable conversion. Channel selections that follow carousel
slots refresh their color/diameter before submission, just like vector mappings.
The submitted intent freezes those properties. Each channel uses its own diameter.

Scanned colors travel separately as optional `measured_color_srgb` on document
inks. Selected pens optimizes mixtures using that swatch, falling back to
`color_srgb` when no scan is applied. Raster pen previews use the same frozen
swatches. Nominal colors still identify requested pens in HPGL and carousel
matching. Applying or clearing a scan on a selected slot makes the PDF preview
stale, including after reload or undo; earlier conversions keep their snapshot.
Auto/K/CMY/CMYK retain conventional channel separation rather than optimizing
mixtures against the swatches. Scanned colors improve the material estimate,
but do not calibrate the physical overprinting model.

Unequal diameters already compensate screening density automatically: geometric
ink area is approximately `diameter × line length`, so the same tone needs about
2.33 times as much line with a 0.30 mm pen as with a 0.70 mm pen. The renderer
scales row spacing and wave geometry with each channel's diameter; thin pens
draw more closely spaced lines. Selected pens additionally inverts a software
coverage ramp for each diameter, accounting for finite-width overlaps and
highlight gaps. The ramp and final paths share the same width-adjusted recipe.
Do not apply a second inverse-width multiplier to channel demand: that would
count the correction twice. Apply the calibration sheet's measured widths to
the carousel, then rebuild the PDF preview to use them. Existing prepared HPGL
keeps its paths and must be regenerated for the new diameters.

This is geometric coverage correction, not a measured optical tone response.
Scanned solid-line colors and widths can improve the material inputs; physical
screened tone patches would be needed to measure ink/paper response across tones.

The selected-pen checklist contains one row per usable loaded slot, with its
current name, color and diameter. Its count is relative to loaded pens, not the
eight-slot software limit; empty and broken slots do not appear. Document ink
snapshots are not additional carousel pens. Older raster selections that still
match a loaded material acquire a live slot link (including label-only renames).
Selections without a current match appear separately and block a new preview
until removed or replaced with **Use loaded pens**. Slot provenance alone never
turns an old green snapshot into a yellow pen. Multiple selected IDs for the same
slot collapse to one screening ink. These repairs affect editable recipes;
uploaded intents and previous conversions retain their original snapshots.

Color mixing remains an illustrative sRGB multiply model, without measured ink
spectra. Selected pens uses a cached 17³ RGB lookup table optimized in Oklab,
bounded by software-measured coverage of the actual AM/FM screen and pen widths.
This accounts for available swatches and hatch saturation, but does not predict
physical ink spectra. It trades lightness and saturation when the pens cannot
match the source. Oklab avoids the original CIELAB fit's large blue-to-purple
shift. The optimizer and coverage measurement run locally; no image leaves the
host. Progress reports both phases. Repeated strokes of the same ink share a preview
layer in both PDF Studio and the prepared HPGL viewer. CMYK shading separates a common K component; CMY plus recovered K instead
subtracts the recovered finite black footprint before screening the residual.
Neither mode promises inkjet color fidelity. Detail smaller than the selected
physical pen can still disappear.

The [palette-aware lab study](../raster_lab/README.md#arbitrary-ink-color-separation--round-11)
at `http://dev.iac.jonh.net:9194/?family=palette` retains the original CIELAB
columns under Earlier experiments and adds corrected Oklab comparisons. Studio's
Selected pens uses the corrected model. Existing Auto/K/CMY/CMYK recipes retain
their earlier separation behavior. With black recovery enabled, the selected-pen
mapper screens the residual after accounting for the recovered pen footprint.
If that same document ink is selected for screening, its paths share one group.

## PDF boundary

`PolicyDevice.fill_image` decodes callback images through MuPDF, including inline
images and repeated image objects. Color conversion uses MuPDF's rendering
parameters. Identical decoded pixels share a lossless PPM within one conversion.
The local distribution Python runs `raster_lab.pdf_render`; OpenCV's build is
checked to exclude proprietary Intel IPP and non-free algorithms. Paths return
as numeric NumPy arrays, without a second large JSON transport. Preview-mode
PPM/array intermediates are deleted after transfer; full diagnostic mode retains
them. The original PDF is always retained.

The PDF worker disables PyMuPDF 2.0's automatic ONNX layout/OCR helper imports;
native paint extraction and raster screening do not use that model. Worker
termination reports the exit signal instead of treating any preceding startup
warning as its cause. Diagnostic logs retain their original contents.

Page placement and the complete image affine transform are applied **before**
recovery/screening. Rotation, reflection, shear and nonuniform scaling resample
source pixels into an orthogonal physical plane. They never stretch a pen or
scale already-generated paths. Generation uses a frozen recipe, separate from
editable lab experiments, with 2048-pixel recovery / 1000-pixel screening limits.
Final placed dimensions, ink snapshots and settings are part of each conversion;
changing scale or diameter regenerates paths. Source decoding is deduplicated
within a page; generated paths are not reused across different placements.

An opaque white fill carries each image's PDF paint footprint and hides earlier
artwork even where screening leaves paper white. New `raster_paths` events carry
one channel's preplanned physical strokes and its image region. These channel
events do not occlude one another. Original PDF clips, the sheet's printable
region and later PDF paint constrain every channel. The Rust planner insets
that surviving region by the pen radius plus its usual numerical guard before
clipping centerlines. The existing continuity pass preserves deliberate raster
gaps instead of inventing new connectors.

Both planned and decoded HPGL strokes receive an independent finite-footprint
containment audit. Raster checks operate per stroke: containing every footprint
also contains their union, avoiding a costly union of a whole screen. Raster
reports say `coverage_kind: image_footprint`; union-area diagnostics are `null`,
and raster regions are excluded from solid-paint missing-detail summaries.
These checks prove clipping/placement properties, not photographic fidelity.
Mileage is attributed to image occurrence and channel. Logical preview displays
the image's pen interpretation; Original PDF retains the source-color reference.

Image alpha masks follow **Transparency**: **Stop for review** explains that
per-pixel transparency is independent of object opacity; **Treat as opaque**
uses the image's original RGB without its alpha mask; **Omit transparent paints**
omits that image. This applies to Draw with pens, Reserve blank space and Box
with an X. Omit images also skips masked images without requiring a transparency
decision. The mask still contributes a balanced clip-stack entry, so an image's
closing clip cannot accidentally remove an enclosing vector or page clip.
The source PDF, its mask and prior conversions are retained unchanged.

Stencil images, color-key transparency and general graphics soft masks remain
unsupported. Existing blend policies remain explicit. There is no whole-page raster
fallback, OCR, semantic model, external image service or physical plotting call.

## Setup and verification

Alongside the existing pinned PDF environment, install the distribution packages:

```sh
apt-get install --no-install-recommends python3-opencv python3-numpy python3-pil python3-cairo python3-skimage python3-shapely
python3 -c 'from raster_lab.engine import check_dependencies; check_dependencies()'
cargo build --release --locked
cargo build --release --locked --manifest-path controller/Cargo.toml
.venv/bin/python tools/make_raster_pdf_fixture.py
PYTHONPATH=tests .venv/bin/python -m unittest test_pdf_raster test_pdf_driver
python3 -m unittest discover -s tests -p test_pdf_raster_renderer.py
cargo test
node controller/scripts/pdf-raster-smoke.mjs
```

The browser check uses isolated state and no hardware connection. It exercises
role assignments, control persistence, undo, preview staleness, CMYK, all eight
selected pens, live diameter changes, unavailable slots, ink-only joined fill,
legacy renamed/swapped pen snapshots, duplicate slot aliases, and preparation
of an HPGL draft. The mixed PDF fixture checks
reused images with different transforms, a circular clip, earlier vector ink
behind opaque image white, and later white paint over image strokes. Additional
tests cover reflection/shear, final page scaling, differing channel widths,
missing materials, auto palette detection, intentional channel crossings and
preserving highlight gaps. Existing PDF and workspace regressions remain active.

Contract: `policies.raster = "dither"` requires `raster_settings` with `screening`,
`palette`, `fullness`, `edge_falloff_mm`, `black_treatment`, and `inks` (role to
logical document-ink ID). Capabilities advertise `dither`. Renderer sources are
included in the conversion fingerprint. Historical lab artifacts and comments
remain unchanged.

For `palette: "selected"`, `screen_inks` lists distinct logical document-ink IDs
(one to eight when screening is enabled). These are independent of carousel slot
numbers and of the optional recovery role in `inks.K`. Every ID must be defined
in the intent palette. Saved recipes follow selected slots; each submitted
conversion freezes their labels, colors and widths. The completed report lists
the selected materials and Oklab model. Material changes invalidate the preview;
unavailable linked slots block conversion until deselected or loaded again.

Development benchmark (11 × 17 inches, aspect preserved inside 0.75-inch margins,
all test pens 0.25 mm; turquoise #009f7c, provisional magenta/yellow): My Love
with full CMY + contrast strokes took 14.2 s, 16,004 physical paths and 393.4 m
of pen-down travel. The Visitor with joined ink shapes and screening off took
19.9 s, 16,928 paths and 19.95 m. These are conversion timings, not predicted
plotting durations. The per-stroke audit reduced the first case from 126 s while
retaining byte-identical HPGL. The test widths do not change the recorded
carousel diameters, and these files have not been physically plotted.
