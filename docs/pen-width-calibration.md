# Pen width and color calibration

Implemented in the dev Plotline Studios service, 2026-11-04. The physical measurement
still needs validation with Jon's first real plot and scan. Synthetic scans are
software tests, not proof of scanner or pen accuracy.

## Operator workflow

Open **Calibrate pens** from HPGL Studio or PDF Studio, or go directly to
`http://dev.iac.jonh.net:9191/calibration`. All eight physical slots are editable:
click a cap, including an empty/unavailable one, to open the same pen editor as
the studios. Names accept K/R/G/B/C/M/Y as black/red/green/blue/cyan/magenta/yellow,
as well as full color names. Set the slot state, design color, diameter, speed
and force there; these edits save to the shared carousel with revision checking.
Changing a pen's design color or loaded state clears its previous measured
appearance when the editor is saved. Renaming an otherwise unchanged pen retains
the measurement. For a same-color replacement, clear the old diameter and measured
appearance when they are no longer applicable. Cancel leaves the inventory alone.

Check **Measure** for the desired samples and choose a **Registration pen**.
The registration pen draws the alignment marks, sheet ID and slot labels; it
may be a loaded pen whose measurement box is unchecked. **Auto** shows and uses
the darkest loaded design swatch (lowest RGB sum, lowest slot breaks ties).
An explicit selection overrides that choice. Empty/unavailable slots cannot be
used for either purpose. Newly loaded slots are automatically checked for
measurement. Optionally describe the paper, then **Create Letter landscape
sheet**. Unknown recorded widths are
allowed. **Open test plot** opens HPGL Studio in another tab. Load Letter with
its **11-inch edge across the carriage** (8½ inches along feed), then use its
ordinary Plot button. The 39 mm back margin is on a long edge. Creating a target
never sends commands to the plotter.

Scan the finished sheet on a flatbed in color, preferably at optical 1200 dpi,
without sharpening, resizing, deskewing or automatic color/contrast changes.
Upload a lossless PNG or single-page TIFF. Embedded ICC profiles are converted
to sRGB; an unprofiled scan is explicitly treated as sRGB. The original file is
retained byte for byte. Uploads are limited to 512 MiB and decoded scans to
200 megapixels, sufficient for a 1200 dpi Letter sheet.

The analyzer recognizes four box-and-cross fiducials, ordered by size, fits an
affine registration, and verifies a machine-readable sheet ID. Rotation and
scanner scale do not rely on DPI metadata. A failed registration offers A–D
manual cross selection and a retry using the same original scan. A wrong sheet
ID, cut-off marks, excessive distortion or unreadably faint reference pen is
reported instead of producing measurements for the wrong inks.

Review the mean width, 5th–95th percentile range, accepted/rejected samples,
previous and measured color swatches, magnified stroke crops, and the scan
registration overlay. Red marks show measured width edges; the green center
band shows the region used for color. Select **width**, **measured appearance**, or both for
each pen, then **Apply selected measurements**. Width and color can also be
applied in separate visits to the same result. Scan RGB is stored as optional
`measured_color` on the loaded pen; its `color` remains the design swatch used
for authoring and matching. HPGL Studio offers **Ink colors: Design / Measured
appearance** for its physical preview, falling back to design colors for
unmeasured pens. Calibration sheets instead open in **Design colors**, so old
measurements do not disguise the pens being calibrated. An explicit preview
color choice is retained on reload; previously opened calibration sheets adopt
the new default. No measurement changes inventory
automatically. Existing Map Studio document inks remain unchanged; **Refresh
carousel** makes the calibrated pens available for authoring. **Update map inks
from carousel** adopts design colors and measured widths and regenerates the
draft. It also recovers legacy defaults and old slot bindings through an
unambiguous loaded color match. Deliberate custom inks and ambiguous matches
need role assignment.

## What the target and analyzer do

- Native single-pass HPGL, generated directly from the target geometry, uses
  each selected slot's recorded speed and force. No PDF stroke thickening,
  multi-pass fill, or endpoint-hop optimization modifies the test strokes.
- Each pen has eight isolated strokes: two in each of four directions. Sixteen
  sample positions per stroke give 128 proposed measurements per pen, excluding
  endpoints and corners. At least 64 accepted samples spanning all directions
  are required before an inventory update is offered.
- Four fiducials, a readable six-digit number, a binary sheet identifier and row
  slot numbers use the chosen registration pen, recorded as `marker_slot` in
  the immutable target. Its paths are emitted even if it is not being measured.
  Existing sheets keep their original registration pen and carousel snapshot. The
  reference SVG is a viewer aid, not a document to send through PDF conversion.
- The landscape Letter page has explicit HP7585 registration for 8½ inches
  along feed and 11 across the carriage, with nominal 39/15/15/15 mm
  back/front/left/right margins. Four rows of two pens fit the shorter feed
  dimension. Both page placement and slot bindings are fixed. Calibration jobs
  preserve unknown widths as unknown; physical fitting reserves up to the
  supported 5 mm diameter for an unmeasured
  pen. Ordinary PDF jobs still require measured widths. Earlier portrait
  targets retain their original geometry and remain analyzable; create a new
  sheet to use the corrected loading orientation.
- Width is estimated from interpolated half-contrast edges of perpendicular
  profiles. Outliers are rejected within each direction, so a broad nib's
  legitimate directional differences are not discarded as noise. Directional
  differences, poor pixel resolution and large spread are reported.
- Local blank paper supplies the white reference for each profile. The RGB
  samples are linearized, divided by that local white, and sampled between 20%
  and 80% of the detected stroke width: the center **60%** requested by Jon.
  A robust aggregate is encoded back to a six-digit sRGB display color.
- Sampling occurs in the original image; registration transforms positions
  rather than repeatedly resampling the whole scan. The smaller images are
  only for UI display. Reports retain individual measurement locations, width
  edges, center bands, local paper RGB, dependency versions, analyzer source
  hash and original scan SHA-256.

This estimates effective ink width on the recorded paper, not metal-tip diameter.
At 1200 dpi, 0.1 mm is only about 4.7 pixels wide. Scanner blur, threshold choice,
bleed and texture can bias all samples together; many samples reduce random
variation, not that systematic error. White-point normalization removes paper
cast but is not an absolute colorimetric characterization of an unprofiled
scanner. Fine pens should be checked against a second scan resolution or an
independent magnified measurement before trusting small numerical differences.

## Persistence and concurrency

Targets, scans, original HPGL, reports, progress, applications and before/after
inventory snapshots live in `controller/state/jobs/calibrations/`. These are
unique inputs/results, not build cache. The existing `/root/plotline` backup
manifest includes this entire path; `prepare.py inspect` verifies its selection.
A manifest selection is not evidence that a newly uploaded scan has already
completed a scheduled backup.

Each uploaded scan gets a new UUID. Completed reports remain immutable; failed
analysis can be retried. One scan analysis runs at a time, with visible queued
and measuring stages and a three-minute process limit. A service restart marks
unfinished analysis interrupted and offers retry rather than leaving a spinner.

The target snapshots the carousel and selected slots. Plot preparation rejects
changed slots or remapping. Applying measurements checks current inventory
revision and the tested pen's full recorded attributes. An earlier application
from the same scan is recognized, allowing width and color to be applied
separately. Each application journals complete before/after states before the
atomic inventory replacement, with a request-keyed receipt for safe retries.
The first startup after this color-model change migrates earlier applied scan
colors only where the current pen still exactly matches the retained application.
It restores the original design swatch, retains the scan RGB as measured
appearance, and preserves measured widths. Later manual pen edits are left alone.
The durable `color-appearance-migration.json` journals both inventory and latest
receipt updates; original application receipts remain intact.

Manual pen replacement or changes to its recorded speed/force require a new
sheet; slot numbers alone are not persistent pen identities.

## API and validation

`POST /api/calibrations` accepts `{slots, marker_slot?, revision, paper}` and creates only a
preview job. `slots` chooses measured pens; `marker_slot` chooses a loaded
registration pen independently. Null or omitted means Auto. Invalid/unavailable
registration slots are rejected before creating a target.
`GET /api/calibrations` lists retained sheets; `GET .../{id}` includes
scans. Upload scan bytes to `POST .../{id}/scans`, then poll
`GET .../{id}/scans/{scan}`. Failed scans accept `POST .../retry` with optional
four normalized image positions in `marks`. `POST .../apply` accepts current
`revision` plus `selections: [{slot, width, color}]`. All writes require the same
origin/custom-header protection as existing Plotline Studios writes.

Install the independent analysis environment and validate with:

```sh
python3 -m venv build/calibration-venv
build/calibration-venv/bin/pip install -r requirements-calibration.txt
build/calibration-venv/bin/python -m unittest discover -s tests -p test_pen_calibration.py
cargo test --manifest-path controller/Cargo.toml --locked
cargo build --release --manifest-path controller/Cargo.toml --locked
build/calibration-venv/bin/python tools/check_pen_calibration.py --binary controller/target/release/plotline-studios
```

`PLOTLINE_CALIBRATION_PYTHON` can select another compatible environment.
`PLOTLINE_PDF_PROJECT` identifies the source checkout for both analysis workers.
The end-to-end checker starts an isolated preview-only server and synthetic
carousel; it creates a target through the browser, uploads a simulated rotated
scan on tinted paper, checks widths/colors and explicit inventory application,
exports prepared HPGL, and checks preview placement. It never plots physically
or changes the real carousel. The first real sheet is the next measurement gate.

HPGL Studio compares calibration snapshots by field values, independent of JSON
key order. The library serializes keys differently from the individual-job and
carousel APIs; that must not produce a false changed-pens warning. Both the UI
and service still reject actual changes to the sheet's recorded pens.
