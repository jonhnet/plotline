# PDF boundary v1

Agreed direction from Jon, 2026-10-30. This is the shared contract for the
generic print system and artistic map maker. The JSON Schemas, reference
coordinate functions and fixtures are implemented here. The proposed HTTP
endpoints and generalized conversion policies below are implementation targets,
not claims that the current controller implements them.

## Ownership and data flow

The map maker owns geography, feature selection, labeling, projection, terrain
occlusion and artistic marks made in map space. It emits ordinary PDF and,
optionally, `plotline.print-intent/1` JSON. Explicit terrain-warped hatching is
ordinary stroke artwork. The map maker does not generate HPGL or drive hardware.

Plotline Studios owns physical paper profiles, carousel inventory, document palettes,
PDF conversion settings, exact pen previews, retained sources, revisions and
plotting. Plotline's converter owns PDF interpretation, logical occlusion,
per-ink physical paths, routing and independent decoded-footprint verification.
The same PDF upload path serves maps, mathematical patterns, mazes, desktop printing and manual uploads.
Authoring provenance accepts `map-studio`, `pattern-studio` and `maze-studio`; each preserves an
immutable recipe hash and an optional stable named-document ID. Pattern layers
bind every generated stroke to a named ink role, including equal colors at
different diameters. The source backlink does not authorize inventory changes
or physical plotting.

## Files and schema conventions

| Schema | Purpose |
| --- | --- |
| `media-profile.schema.json` | Sheet dimensions, printable rectangle, content inset and optional measured device transform. |
| `palette.schema.json` | Document ink identities, display colors and measured diameters. |
| `print-intent.schema.json` | PDF hash, selected pages, placement, optional palette/bindings and conversion preferences. |
| `capabilities.schema.json` | Read-only paper profiles, available loaded inks and actually implemented policies. |
| `defs.schema.json` | Shared types. |

Schemas use JSON Schema 2020-12. IDs under `plotline.invalid` are identifiers,
not network resources. Validation resolves them locally, without downloading
schemas. The `schema` field is the protocol/version discriminator. Unknown
fields are rejected in v1; coordinate or policy meaning changes require a new
version. Values are finite JSON numbers. All lengths outside PDF are millimeters
unless explicitly named `pt` or `units`. Arrays are documented below.

A bare PDF is always valid input to the service. Optional metadata improves
defaults and attribution; it is not required for Inkscape or other applications.
Keep the original PDF bytes and print ticket. Normalize coordinates in memory.
Embedded font outlines and fonts are both allowed. Map output should initially
stay in the currently supported opaque vector subset; white fills and clips
are explicitly allowed. Do not impose a strokes-only constraint on map PDFs.

## Page and device coordinates

The normalized source page is the PDF's displayed CropBox with `/Rotate` and
`/UserUnit` already applied by the PDF engine. Record original MediaBox, CropBox,
rotation and unit information for diagnosis. Map-generated PDFs should use
MediaBox = CropBox, origin zero, rotation zero and ordinary 1/72-inch points.

Source and target sheet coordinates use millimeters, top-left origin, X right,
Y down. `source_size_mm` is `[width,height]` and must agree with the normalized
PDF page. `printable_rect_mm` is **`[x,y,width,height]`**, never max coordinates.
`content_inset_mm` is recommended authoring clearance inside that rectangle.
The example profile's rectangle plus 1 mm inset reproduces experiment 11.

`placement` transforms the whole source page into the target sheet:

1. Rotate clockwise by `rotation_deg` about the page's coordinate frame, then
   translate its rotated **page** bounds to top-left zero. For source W,H:
   0° `(x,y)`; 90° `(H-y,x)`; 180° `(W-x,H-y)`; 270° `(y,W-x)`.
2. Multiply by `scale`, which defaults to 1 in synthesized intent.
3. Add `offset_mm`, which defaults to `[0,0]`.

There is no ink-bounds centering. Changing target paper keeps placement/scale
unless the user explicitly changes them. C-sized art on B-sized paper therefore
shows its actual overflow. Explicit fit/center commands may calculate a new
placement from the selected **page**, never secretly rewrite it. Clipping and
finite pen footprints must use the resulting target printable area. Source
stroke dimensions scale with the artwork; physical pen diameters do not.

Optional `orientation_deg` records a quarter-turn of the sheet relative to device
axes: HP7585 0/90/180/270 puts its wide feed margin at the artwork's
left/top/right/bottom. This is independent of source PDF placement rotation.
The media's printable rectangle and any explicit calibration must already use
that orientation. This does not depend on sheet aspect ratio: at 0/180 the sheet's
height runs across the platen, and at 90/270 its width does. Explicit HP7585
registration requires 11–36 inches across the platen. PDF Studio's new Letter
preset is 11×8.5 inches with orientation 90; new source-sized defaults choose the
loadable edge while preserving the PDF dimensions. Existing saved snapshots and
IPP's standard portrait page sizes are not rewritten.

A physical loading change keeps sheet dimensions and rotates the margins. A
quarter-turn loading change discards the previous device calibration because its
hard limits measured a different feed/platen arrangement; registration uses nominal
margins until the loaded plotter is checked. A half-turn swaps both pairs of margins and composes the
calibration with `(x,y) → (sheet_width-x,sheet_height-y)`; its device limits
remain unchanged. Without explicit calibration, Plotline Studios turns its nominal
registration. Profiles omitting the field retain the prior behavior.

Optional calibration maps target sheet millimeters to the centered device:
`sheet_to_device_units = [a,b,c,d,e,f]`, with
`X=a*x+c*y+e`, `Y=b*x+d*y+f`. `hard_clip_units` is instead
`[minX,minY,maxX,maxY]`. Preserve the specified units/mm and verify that the
matrix is a rigid orientation/reflection at that scale. Quantize only at HPGL
generation, and audit the complete finite-width footprint afterward.

The committed HP7585 example uses `[40,0,0,-40,-9116,5512]`, based on measured
paper offsets and a conservative reported window. It is a nominal physical
registration, not a claim of exact margin measurement. `uncertainty_mm: null`
means unquantified; the 1 mm authoring inset is a guard, not a statistical bound.
Profiles include revision and evidence. A changed calibration or loaded sheet
must update the preview/fit check. An uncalibrated profile can still support
map authoring, but requires a measured device transform before physical output.

## Inks, inventory and PDF bindings

A document ink has a stable ID, label, sRGB display color and **known positive
diameter**. Color is not its identity: fine-black and broad-black coexist.
Display RGB is an ink approximation, not a colorimetric guarantee. Exact PDF
white remains paper/occlusion and never requests an ink.

Optional `measured_color_srgb` carries a scanned material swatch independently
of nominal `color_srgb`. Null/absent means no scan. Raster selected-pen separation
and its stroke preview use the measured swatch when present; HPGL pen identity
and matching still use the nominal color. The swatch is frozen in each conversion
along with diameter. Live PDF slot links refresh it when calibration is applied
or cleared, so previews must be regenerated. Conventional CMY/CMYK separation
does not optimize mixtures for measured swatches.

The physical carousel is owned by Plotline Studios. Today's `GET /api/carousel`
returns `revision` and eight slots with label, state, color, width, speed and
force. `tools/print_contract.py capabilities` adapts this read-only response to
the shared schema, alongside a media profile. Only loaded colored pens appear
as available choices. Unknown diameters remain null and need measurement before
forming a valid conversion palette. The adapter never changes inventory.

The map UI copies selected inventory attributes into document inks. A `source`
field records carousel revision/slot as provenance; it does not bind the document
forever to that slot. The catalog's `inventory-REV-SLOT` IDs identify a snapshot
entry, not a persistent physical pen. The current inventory has no persistent
pen identity; adding one later must not change document ink semantics.
Saved maps retain their ink snapshot and remain editable while Plotline Studios is
offline. Refresh is explicit, showing differences. It must not silently recolor
saved art or change widths when somebody rearranges the carousel.
Conversion must accept a complete document palette even when the source revision
has changed, the original slots are empty, or matching physical pens are absent.
Those conditions do not change document inks or invalidate the PDF. Physical
plot preparation separately matches the planned inks against current inventory.

PDF Studio output assignments can explicitly opt into a slot link with
`source.follow_slot: true`. This is an editor dependency, distinct from the
immutable source document inks above. Before submitting a conversion, PDF Studio
resolves the chosen slot's current color and diameter into the output palette;
missing pens or unknown diameters require correction. The slot choice survives
Save/reopen, and carousel material changes make the editor preview stale without
becoming undoable recipe edits. Conversion workers **never** dereference the
link: the submitted material, source revision, and resulting geometry remain
frozen. Absent/false `follow_slot` retains the historical provenance semantics.

For precise choices, `bindings` maps an exact PDF Optional Content Group name
to an ink ID. If nested groups match, the innermost binding wins. Duplicate names
or missing ink IDs are errors. Unbound marks use the chosen generic mapping
policy; source color and stroke width remain available to it. PDF white overrides
any binding and stays occlusion-only. Optional `feature_group` supports reporting.
Desktop pipelines may strip OCG names: the PDF still works through generic
mapping, while hash-bound stale metadata is rejected. No one must infer ink
identity from RGB alone. The fixture binds two black layers to distinct widths.

Slot changes with an equivalent pen need only a final slot substitution. A
material change offers either upstream rebuilding or explicit acceptance of the
selected pen on the existing paths. Acceptance records requested and actual
color/diameter plus the selected slot, and is checked against current inventory
before preparation. Changing those values invalidates the acceptance. The preview
uses actual selected pen footprints; original clipping/clearance guarantees do
not transfer to wider pens on unchanged paths. Paper-fit checks still apply.
Speed and force are physical execution settings and do not change source artwork.

### Shared pen workflow extension

`implemented.pen_workflow` advertises the following extensions. Old saved intents
remain valid and retain their width behavior. New clients using these fields need
an updated service; the three studios share `shared/web/inksets.js` and its CSS.

- Document palettes have no eight-ink schema limit. Eight remains the maximum
  distinct output pens used by one converted page. For larger palettes, unused
  inks are omitted and identical effective color/diameter pairs are combined
  before invoking the converter. More than eight remaining materials produces an
  actionable mapping error, never an implicit recoloring or dropped ink.
- Optional `ink_mapping` maps a source ID to an output `palette.inks[].id`.
  Bound source IDs are the original OCG binding ink IDs; unbound source IDs are
  stable `style-…` identities from inspection. A mapping can replace a bound ink
  absent from the effective palette. Several sources may share one output ink.
  Source IDs from other pages can remain in the intent when converting one page.
- Inspection pages include `source_styles` (kind, unquantized source RGB,
  effective page-space width in mm, display sRGB, stable ID and occurrence count).
  Width uses the CTM area scale for nonuniform strokes; it is a scalar summary,
  not a claim that an anisotropic source stroke has one constant width.
  White is excluded. `GET /api/v1/documents/{id}/source-styles` also supplies this
  census for older originals, caching it without changing source bytes.
- `policies.stroke_width_mode` is `preserve` or `pen`; omission means `preserve`.
  New PDF documents default to `preserve`; saved recipes keep their width policy.
  Selecting `pen` reinterprets nonwhite
  source strokes at the chosen physical diameter before occlusion, retaining
  source-space dashes, page-space centerlines and original clipping. It emits
  one traversal per visible run, with no width-building or contour fallback.
  White masks, fills and filled glyph outlines retain their own semantics.
- Optional `policies.overlap_strokes` defaults to false. When true, colored
  strokes do not cut other colored strokes, including different inks and
  repeated paths. Original clips, page margins, white strokes and filled paint
  (including images and white knockouts) still constrain them. Filled regions
  still obey normal paint order. This is independent of stroke-width treatment;
  it preserves crossings without inventing connections between separate paths.
  `implemented.overlap_strokes` advertises support. Existing intents keep their
  policy; selected Pattern families opt in on new exports.
- Optional `authoring` identifies `map-studio`, its HTTP(S) URL and immutable
  `recipe_hash`. Rebuild links carry `source_recipe` and `pen_substitutions`
  (JSON keyed by ink ID) back to that studio. A newer unsaved map is preserved in
  its existing tab; the submitted snapshot opens separately. PDF rebuild links
  identify the original document/conversion/page and carry the same substitutions.
  Neither link automatically converts or physically plots anything.

The carousel is immutable from Map Studio. PDF and HPGL share a physical-pen
editor that explicitly records loading, empty or unavailable slots. Source inks
in PDF Studio remain immutable beneath grouping and automatic mapping. HPGL
setups allow `pen_map` value 0 (unassigned) and many-to-one slot assignments;
included unassigned or unavailable inks still prevent preparation. The former
inventory-overwriting **Use document pens** action is removed.

## Conversion policies

Keep original paint order, clipping, source geometry and white coverage through
color approximation. Equal output colors do not erase original occlusion.
Choosing sparse or feathered representation for an opaque fill does not reveal
earlier paint through its intentional gaps. Explicit hatch strokes already in
the PDF are strokes and must not be hatched a second time.

Optional `policies.thin_detail_thickening` permits extra **total thickness** for
narrow visible details, measured in pen diameters, from 0 to 1. Missing or zero
preserves the previous paths. Unlike `stroke_width_tolerance`, this also recovers
thin parts of filled glyphs/shapes. For a 0.5 mm pen, 0.75 allows up to 0.375 mm
total growth (0.1875 mm per side). Original clips, printable bounds and later
logical paint remain hard limits. Original PDF and logical paint stay unchanged;
permitted excess is measured separately from containment violations. This is
approximate centerline recovery, so lettering may grow or touch, and some details
still cannot be represented. Explicitly omitted fills remain omitted.
`implemented.thin_detail_thickening` advertises support.

Conversion pages include optional `detail_loss` counts and an
`artifacts.detail_loss` URL for a transparent SVG mask in the same coordinates
as the source/logical/pen previews. It marks missing visible details found against
decoded pen footprints, including partial glyph loss. Detector `version: 2`
prioritizes substantial gaps, excluding intentional hatch gaps, hidden paint,
ordinary edge trimming and short texture remnants. Sparse fragment bounds are
subdivided to follow the missing geometry, then grow by 0.35 mm and merge for
display; `zones` counts warning boxes before merging. This mask is diagnostic
only, never artwork or HPGL. Older revisions remain valid; PDF Studio offers
refresh for absent or older diagnostics. See [thresholds, limits and checks](../../docs/thin-detail-recovery.md).

Fill modes are `none` (no ink), `outline`, `hatch`, `crosshatch` and `feathered`.
`spacing_mm` is centerline spacing at the final physical scale. `angle_deg` is
clockwise from sheet +X; crosshatch adds 90°. Feathering uses crosshatching:
for distance D, the first D/2 inward from the fill boundary uses spacing S, the
next D/2 uses 2S, and deeper interior receives no hatch. Use one page-anchored
lattice, retaining alternate lines in the half-density band. Hole boundaries
count. Measure distance on the original fill after its original clipping but
before later paint; a later label/cut must not acquire a new decorative border.
Intersect planned marks with final visibility and pen clearance. Feathering
adds no perimeter unless separately selected in a later contract version.

Raster policy names are:

- `reject`: retain source and report the unsupported operation.
- `omit`: remove image paint, revealing earlier artwork.
- `reserve`: retain opaque image occlusion with no pen marks.
- `placeholder`: reserve the opaque footprint and draw its outline and X.
- `dither`: approximate image color/tone with clipped, dithered pen strokes.

Reserve/placeholder/dither must use the image transform, original clipping and
paint order. The footprint may be a rotated/skewed quadrilateral. A transparent
image or image mask is not an opaque rectangle: apply an implemented explicit
mask policy or report unsupported. Dither is an agreed future option; the schema
accepting its name does not imply a working raster-to-pen algorithm. Sampling,
screening and mileage controls belong to the print-driver track.

Jon prefers simple handling of gradients/transparency over faithful simulation.
`gradients: omit` removes shading paint. `transparency: ignore-alpha` treats
ordinary partially transparent paint, including image alpha masks, as opaque;
`omit` removes affected paint. Image-mask clips retain balanced stack entries
under either policy, preserving surrounding vector/page clips.
`blend_modes: normal` uses ordinary opaque overpainting. `soft_masks` remains
reject/omit until deliberately implemented; correct stack handling is mandatory.
No full-page rasterization fallback. Unsupported choices return a specific
diagnostic. These policies modify appearance deliberately and must be visible
in preview and an omission/change report. Preserve a source preview separately
from policy-normalized logical paint and physical pen ink. Interpretation tests
must compare against the stated policy-normalized reference when policies alter
source semantics; they must not pretend the original PDF is pixel-identical.

## HTTP boundary

Current service responsibilities, implemented handoff and remaining contract additions
are described in [Printing workflows](../../docs/printing-workflows.md).

Plotline Studios implements these endpoints; MapStudio uses its HTTP client adapter
and retains capability snapshots for offline authoring:

- `GET /api/v1/capabilities` → the capabilities schema. Media/calibration profiles
  and inventory revisions are snapshots; `implemented` lists actual support.
- `POST /api/v1/documents` with multipart fields `document` (PDF) and optional
  `intent` (JSON) → `202` with `{id, status: "received", status_url, review_url}`.
  It durably retains the source/intent before acknowledging. No plotting. Optional
  `Idempotency-Key` safely deduplicates exact PDF/intent submissions;
  `GET /api/v1/submissions/{key}` recovers their receipts. See
  [the submission contract](submission.md) for capability negotiation and retries.
- `GET /api/v1/documents/{id}` → `{id, source_sha256, status, review_url,
  conversions: [...]}`. Document status is received/inspecting/ready/unsupported/error.
- `POST /api/v1/documents/{id}/conversions` with a complete print-intent snapshot
  → `202` with `{id, status: "queued", status_url}` for an immutable conversion.
- `GET /api/v1/conversions/{id}` → status queued/running/ready/unsupported/error,
  source/intent hashes, converter version, page results, warnings and artifact
  URLs. A ready page includes source/logical/pen previews, HPGL, metrics and
  decoded validation. Errors preserve source and earlier successful versions.

Bare-PDF imports synthesize intent through the review UI. Uploaded optional intent
is inspected but does not start a conversion; pen-aware clients explicitly submit
it to `/api/v1/documents/{id}/draft` after inspection. A conversion cache
key includes PDF bytes, selected pages, all effective settings, target/calibration
snapshot and converter version; identical PDF bytes with different settings are
not interchangeable jobs. A preview/report is bound to its conversion revision.
Existing HPGL import and physical plotting APIs remain available unchanged.
No API here depends on map recipes or geographic databases.

## Shared acceptance fixture

```sh
.venv/bin/pip install -r requirements-contracts.txt
.venv/bin/python tools/make_boundary_fixture.py
.venv/bin/python tools/print_contract.py validate build/pdf-boundary-v1/boundary.pdf build/pdf-boundary-v1/boundary.print-intent.json
.venv/bin/python tools/print_contract.py capabilities --output build/pdf-boundary-v1/capabilities.json
.venv/bin/python -m unittest discover -s tests -p test_print_contract.py
.venv/bin/python -m unittest discover -s tests -p test_capabilities_handoff.py
```

`boundary.pdf` contains a landscape B page and landscape C page, both targeted
at the B example profile. It includes asymmetric whitespace, a 100 mm ruler,
two black widths with distinct bindings, a white-core road, cleared text,
hollow outline, genuine PDF clipping, explicit hatch strokes and a generic fill.
Page 2 has an intentionally out-of-sheet marker. `expectations.json` records
independent page/device coordinates; no implementation may make the overflow
disappear through automatic scaling/centering.

`policy-probes.pdf` adds a small RGB image, alpha paints and an axial gradient.
The existing strict converter is expected to flag these; each future policy
needs its own explicit acceptance tests. `inkscape-capture.svg` is a self-contained
Letter-sized desktop test with vector paths, text, a 100 mm ruler and one bitmap.
Generated artifacts stay in `build/pdf-boundary-v1/`; the generator is committed.

Current checks validate schemas, PDF hashes/page sizes, inventory adaptation,
paper-to-device coordinates, distinct inks, fixture content and exact capture
bytes. Per-ink path planning, policy implementations and HTTP endpoints remain
work for the driver agent. See the two handoffs in `docs/handoffs/`.

The fixture rebuilds byte-for-byte in the pinned environment. Its first page
also passes the existing converter's footprint checks: 242 paint events and
866 decoded paths with the legacy single-width three-pen settings. That is a
baseline for the vector boundary, not a test of the proposed per-ink contract.
The policy probe extracts exactly one unsupported image, two alpha operations
and one shading. All 29 Python tests pass after adding the six boundary/capture
tests; no converter or controller behavior changed in this contract milestone.

### Design color and measured pen appearance

The loaded carousel API may include optional `measured_color` (six-digit RGB)
for each slot. Its `color` remains the stable design swatch. Available authoring
inks and conversion palettes use that design swatch; measured appearance is
for the physical preview and does not become a new document ink. Width
calibration still updates `width_mm`. Older conversions that used the measured
RGB remain compatible with the same loaded pen, subject to the usual width
checks. Missing appearance measurements fall back to the design color.

### Implemented raster rendering

`policies.raster: "dither"` now requires `raster_settings`: screening switch,
Selected/Auto/K/CMY/CMYK palette, interior fullness, physical edge falloff, black treatment
(off/lines/shapes), and channel-role to document-ink IDs. These are document
identities, not carousel slots. See [PDF raster boundary](../../docs/pdf-raster.md)
for physical placement, opaque coverage, overlapping channels and regression checks.

`palette: "selected"` adds `screen_inks`, an ordered list of one to eight unique
document-ink IDs when screening is enabled. The mapper optimizes mixtures of their
actual swatches and diameters in Oklab, using simulated screen coverage. It needs
no CMY role assignment. `inks.K` is still required when black recovery is enabled;
the same ink may also appear in `screen_inks`. Empty selections are allowed with
screening off. References must exist in the intent's palette; unused inks and
older explicit role-based recipes retain their meaning.
