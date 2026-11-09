# Plotline Studios server

A Rust service and phone-friendly browser UI for the HP 7585 workflow. The
prototype at **http://dev.iac.jonh.net:9191/hpgl** imports HPGL, previews layouts,
and downloads prepared HPGL with rotation and carousel assignments. **Plot**
captures the selected layout and starts feeding it from Plotline Studios through
stain's serial byte bridge. Stain and the plotter may remain off while previewing
or exporting.
Without a configured SSH connection, the server runs in preview-only mode.

The controller is a Rust crate inside Plotline Studios. PDF Studio invokes
the offline MuPDF/GEOS converter through a bounded worker queue. The original controller source
remains preserved under `archealogy/delivery-02-web-controller/`.

## Studio navigation

The root **[Plotline](http://dev.iac.jonh.net:9191/)** page links to `/map`, `/patterns`, `/pdf`
and `/hpgl`. Shared `studio-navigation.js` provides tab reuse, the studio
switcher, source links, titles and favicons; `studio.css` owns the header themes.
Map composition runs in a helper process: `/map/*` proxies to `PLOTLINE_MAP_UPSTREAM`
(default `http://127.0.0.1:9193`), which must be a loopback HTTP origin. Origin
and Host are preserved so Map's write-origin checks remain effective. A stopped
Map service does not prevent PDF/HPGL use. See [navigation, source ancestry and
validation](../docs/studio-navigation.md).

## HPGL Studio

The `/hpgl` page uses the shared studio tabs, Open dialog with thumbnails,
Save/Save As/Revert, undo history, preview gestures and download/details trays.
It has a full-width fixed preview with floating Fit page, Facing plotter,
appearance, pen-up, Undo and Redo controls. Facing plotter is an inspection toggle;
it never rotates the physical output or marks the recipe changed.

A bottom drawer starts on **Pens**; the **Plotter** button switches it to device
feedback and paper controls. Both panes occupy the same compact space; click the
selected drawer button again to close it for a larger preview. Pen tools sit beside
the tray. **Choose inks…** optionally omits document inks without changing the
physical carousel or the pen rows; Apply is undoable, and Cancel leaves them alone. Raw HPGL retains sheet/margin/rotation controls;
PDF and calibration jobs retain their fixed page registration. The controller
uses exact floating-point JSON round trips so saving/reopening computed margins
cannot look like a paper edit. Registration and fixed layout comparisons remain
strict; operator pen choices are independent. The plain **Plot** button stays at
bottom right and submits the current working choices once.
Progress and remaining time live inside **Plotter**, which opens automatically
when a plot starts. Only the active drawing shows live progress and Pause/Cancel.
Other documents show **Plot preview**, their estimated duration and planned pen
bands, even while the device is busy. **Show active plot** returns to the running
document, whose tab carries a small animated pen (stationary when paused and
respecting reduced-motion preferences). A missing active-document tab is restored
without taking over the selected preview. Opening, editing, saving or closing a
document does not change the frozen serial program. Plot remains disabled until
the previous run is finished or resolved. The colored bars represent prepared
byte ranges, not proportional time or physical pen position. Downloads and
source/report details are in trays.

**Save** stores a named recipe of paper choices, included inks, carousel mappings,
and accepted substitutions. **Save As** references the same retained HPGL;
neither saves physical carousel contents or alters an active plot. Physical pen
edits remain shared inventory edits. Layout edits no longer silently PUT the
legacy `setup.json`: an asterisk denotes unsaved changes, Revert restores saved
choices (or the initial imported setup), and closing offers Save/Discard/Cancel.
Tabs and unsaved choices survive browser reloads. Explicit Save persists them on
the server with optimistic revision checks; stale saves cannot overwrite a winner.

`GET/POST /api/jobs/{job}/recipes` lists/saves recipes; an update includes `id` and
`expected_revision`, while omitting them creates a copy. `GET
/api/jobs/{job}/recipes/{recipe}` reads one. `GET /api/jobs?library=true` includes
recipes and retains previously prepared PDF jobs that have named HPGL recipes.
`/hpgl?recipe={recipe}#{job}` links to saved choices; existing `/#job` links continue
to work. Records and immutable numbered snapshots live at
`<job>/recipes/<recipe>/`, separate from the preserved imported `setup.json`.
Recipe saves never generate HPGL or send device commands.

## PDF Studio

Open **[PDF Studio](http://dev.iac.jonh.net:9191/pdf)**. **Open…** shows a searchable
library of PDFs and saved recipes with page thumbnails; **+** adds a PDF and its
optional v1 print-intent JSON. Each open document has a tab. **Save** saves its
named conversion recipe (all page placements, inks, mappings and policies), while
**Save As…** creates another recipe referencing the same retained PDF. Neither
operation converts, prints, nor rewrites the original PDF. A tab's **\*** marks
unsaved recipe edits. **Undo**, **Redo** and **Revert** work on that recipe; Revert
restores its last saved choices and can itself be undone. Closing a changed tab
offers Save, Discard or Cancel. Open tabs and unsaved recipes survive reloads in
browser storage; explicit Save makes a recipe durable on the server.

**Replace source…** above Page & paper opens a revised PDF, SVG or image in a new
tab with the current working settings, including unsaved edits. It copies paper
placement, output pens and conversion policies after inspection, binds the intent
to the new file, and requires a fresh pen preview. Existing source styles keep
their assignments; changed widths use the closest same-color source style's
assignment, preferring the same stroke/fill kind. New colors use the closest
output pen. Only layer bindings present in the replacement survive. Changed page
sizes/counts and new colors are called out for review; scale/offsets are retained,
and added pages inherit the first page's placement. Source-sized paper follows
the new PDF page. Earlier source bytes, saved recipes and plots remain available;
Save stores a separate recipe for the replacement. No JSON export/import is needed.

The Studios share document tabs/dialogs, layout CSS and progress rendering in
`shared/web/studio.js` and `studio.css`. Keep shared structure there;
app styles own their particular controls and preview content. Both studios have a
scrolling left settings panel, preview tools floating at top right, one fixed-height
progress bar and elapsed/status line. PDF Studio has **Open plot ↗** at bottom
right; Map Studio keeps its own preparation flow.
All four preview toolbars are built by `renderPreviewControls`: a − / Fit / +
row, then an Undo/Redo pair, with the same vertical layout and keyboard
tooltips. Each page's `#preview-tools` markup contains only its deliberate
extensions (map editing, PDF review mode/loss, HPGL view/colors/travel, or Pattern
margin guides). Zoom and Fit only inspect the preview; they never edit a recipe
or request a new map/pattern. Existing history adapters retain their own undo
semantics. Keep common markup, order and styling out of the individual Studios.
Mileage, warnings and technical reports live in the details tray; downloads have
their own tray. **Edit pens…** opens a wide drawer below the preview.

**Preview pen strokes** enables when the current preview needs updating. It freezes
the submitted settings; edits and tab switches made during conversion stay intact.
Conversion runs are internal immutable artifacts, not user-visible saved documents.
**Original PDF**, **Logical paint** and **Pen footprints** select viewport views.
Drag pans and wheel/pinch zooms around the pointer; **Fit page** resets inspection.
These gestures, view choices and display units never change the recipe or request
conversion. Map Studio adds **Edit map** and temporary Shift gestures for composition.

The original view is a bounded-resolution raster for display; extraction and
conversion retain vector geometry. Pen previews are zoomable SVGs from decoded
HPGL with each ink's diameter. Mileage is attributed to inks and PDF layers.

When a broad pen loses fine lettering, use **Thin-detail thickening** under
**Fills & strokes**. Start at **0.75**, then **Preview pen strokes**. Zero keeps
the previous paths; 1 permits more growth. The value measures extra total width
in pen diameters and also applies to thin parts of filled letters. Lettering can
become heavier or touch, but original clips, white knockouts and printable bounds
remain hard limits. The original PDF and logical paint are unchanged.

**Highlight lost details** overlays animated red stripes on expanded regions
around remaining losses. It works in all three views and at every zoom, never
changes print data and requires no reconversion when toggled. Reduced-motion
preferences stop the animation. Until a supported pen preview is ready, the
checkbox and label are dimmed, with a visible explanation to generate/update the
preview or wait for it to finish. Older revisions offer **Update pen preview** to
compute the focused diagnostic. It prioritizes substantial missing strokes and
letter parts, ignoring ordinary edge trimming and short texture remnants. The
overlay is an attention aid, not a legibility or texture-completeness test. See
[recovery behavior, limits and tests](../docs/thin-detail-recovery.md).

**Preview pen strokes** also registers the completed HPGL as the current draft.
The final progress step says **Finishing preview: registering plot**. **Open plot ↗**
is always a navigation button: it enables only when that exact preview and its
registered plot match the selected page and current settings. Completion stays in
PDF Studio; opening HPGL Studio requires that click. Editing a setting disables
Open plot until the new preview is ready. Server-side conversion and registration
continue across browser reloads or tab switches. Registration failures preserve the
preview and offer **Retry plot preparation**, reusing its exact HPGL. Empty pages
finish with **No drawable pen paths** and keep Open plot disabled.

Opening an older, unregistered preview does not silently replace the current
draft: **Preview pen strokes** registers its retained HPGL when requested, or
rebuilds first if its diagnostic preview expired or needs updating.
HPGL Studio opens in another tab. Its **Change PDF conversion** link returns to the exact conversion and
page. Updating replaces that page's current draft; earlier conversion/job bytes
and plot receipts remain immutable. Page size, placement and ink diameter changes
belong in PDF studio. Equivalent physical pens may be remapped without conversion;
a color/diameter mismatch offers explicit acceptance on unchanged paths or an upstream rebuild. PDF jobs preserve
page placement, including asymmetric margins, through the final serial commands.
Legacy imported HPGL retains its existing centered layout.

New IPP submissions stop at PDF Studio after inspection. The user chooses inks,
fill/effect policies and intended placement, clicks **Preview pen strokes**, then
**Open plot ↗** when ready. Loaded pens with known diameters seed the initial
palette. Blank/fully omitted pages create no physical plot. Each nonempty page
becomes a separate draft; every physical sheet needs an explicit **Plot** click.
MapStudio supplies complete pen-aware intent and automatically advances through
conversion to HPGL Studio. The service boundary and the two flows are documented
in [Printing workflows](../docs/printing-workflows.md). Existing drafts and older
automatic-intake receipts remain available.

Paper dimensions and offsets default to **decimal inches**; page/sheet sizes
are shown to a tenth. A paper-unit toggle remembers inches or millimeters in
this browser. Pen diameters and hatch spacing remain in millimeters. The API
continues to use millimeters, and display-unit changes preserve exact placement
values rather than rounding the saved geometry.

**Turn sheet ↻** turns the sheet and its margins while preserving artwork
rotation, scale and offsets. **Paper loading** independently chooses the edge
across the platen and identifies the wide-margin edge. Loadings narrower than
11 inches or wider than 36 inches are disabled; Letter defaults to 11×8.5 inches
with its 11-inch edge across. End-for-end choices also allow the wide margin on
the opposite side. **Fit page inside printable area** remains an explicit action.
Paper changes invalidate the pen preview and participate in Save, Undo and Revert.
Existing recipes keep their paper snapshots; unsupported old loading choices get
a message before preparing a new plot. A different feed/platen arrangement uses
nominal registration instead of reusing the old loading's measured device limits.

`node controller/scripts/pdf-paper-smoke.mjs` checks paper and loading changes,
four HPGL registration directions, output clipping, Letter/E platen constraints,
recipe persistence and narrow layouts in isolated state. Set
`PLOTLINE_BROWSER=firefox` to check Firefox. No physical plot is started.

Page placement starts at scale 1, rotation 0 and top-left offset (0,0), unless an
uploaded intent specifies otherwise. **Scale × received PDF** explicitly means
an additional scale relative to the PDF's actual received page. The panel shows
received, placed, sheet and printable dimensions. If retained IPP captures match
the exact PDF hash, the document endpoint returns their requested paper sizes as
read-only provenance and the UI displays mismatches. Multiple matching tickets
are retained rather than arbitrarily selecting one. Ticket dimensions never
silently resize a document. For Jon's Inkscape pattern, the ticket requested
36.0×63.2 inches, but the received PDF was 8.5×11.0 inches with an upstream
0.158326 content transform; a further 0.9 scale is about 0.143 overall. Source CropBox, Rotate and UserUnit are
normalized by MuPDF. The selected source page and target sheet are displayed
separately; blue dashes show the printable rectangle. Conversion clips finite pen
footprints to both source page and target printable area. **Fit page** is an
explicit edit; changing paper alone does not shrink or center the drawing. The
UI converts one selected page per revision; the API accepts multiple selected
pages and all original pages remain available.

The paired pen display shows immutable source inks above the recorded carousel.
Ordinary PDFs start with known loaded pens; automatic mapping chooses perceptually
near colors, then nearby diameters. Grouping tames large source-style sets without
changing source values. Explicit PDF layer bindings and source-style mappings
carry stable identities, including multiple inks of the same color.

PDF Studio assignments follow their selected physical slots. Editing a used
slot's color or measured diameter updates the recipe's resolved output material
and makes its preview stale; the source artwork and previous conversions remain
unchanged. Unused slots and speed/force changes do not require a new preview.
Refresh, window focus, a visible-page poll, and submitting a conversion all fetch
the current carousel. Empty/unavailable slots or unknown diameters require a pen
or mapping correction before converting. Slot choice is a saved recipe edit;
current loaded material is an external dependency, so Undo keeps the actual pen.

New PDF output assignments explicitly record `source.follow_slot: true`.
Without that flag, inventory revision/slot remains historical provenance (as in
Map Studio source palettes). An old material-only assignment can acquire the
link when the slot it currently matches changes during this session. The UI
resolves links **before** submitting a conversion; the worker always consumes
frozen color/diameter inputs and never consults live inventory. For API intents
without explicit mapping, the legacy nearest squared-sRGB rule remains available.
HPGL Studio compares prepared pens to actual inventory and offers a reviewed
substitution or an upstream rebuild for differences.

Implemented area modes: no ink, outline, parallel hatch, crosshatch, and two-band
feathered crosshatch. Spacing/angle/distance are measured on the target sheet.
White and uninked opaque fills still occlude earlier paint. Text retains its
replaceable glyph-contour treatment; explicit PDF hatch strokes remain strokes.
Ordinary opaque images support reject/omit/reserve/placeholder and local pen rendering.
See [Raster images in PDF Studio](../docs/pdf-raster.md) for screening, black
treatments, channel-to-pen mapping, setup and boundary tests. Gradient omission,
alpha-as-opaque/omission and normal/omitted blend groups are explicit options.
Masked-image reservation, soft masks, text clips, knockout groups and transfer
functions remain unsupported. There is no whole-page
rasterization fallback. Reports list changed effects and visible paints with no
paths; sparse-fill coverage is distinct from PDF interpretation fidelity.

### Setup and checks

Use the [setup guide](../docs/GETTING_STARTED.md) for GEOS/Rust and native Python, then:

```sh
.venv/bin/pip install -r requirements-map.txt -r requirements-contracts.txt
sudo apt-get install inkscape fonts-dejavu-core
cargo build --release --locked
cargo build --release --locked --manifest-path controller/Cargo.toml
.venv/bin/python tools/make_boundary_fixture.py
.venv/bin/python -m unittest discover -s tests -p test_pdf_driver.py
.venv/bin/python -m unittest discover -s tests -p test_pdf_input.py
.venv/bin/python -m unittest discover -s tests -p test_svg_input.py
node controller/scripts/pdf-smoke.mjs
node controller/scripts/pdf-workspace-smoke.mjs
node controller/scripts/pdf-replacement-smoke.mjs
node controller/scripts/pdf-image-smoke.mjs
node controller/scripts/pdf-svg-smoke.mjs
node controller/scripts/navigation-smoke.mjs
```

PDF Studio's upload accepts PDF, SVG, JPEG and PNG in the `document` multipart field.
For SVG/images, the durable receipt retains `upload_sha256` and `original_format`;
`source_sha256` is assigned once inspection creates the normalized PDF.
Idempotent retries compare the original upload bytes, including after conversion.
`GET /api/v1/documents/{id}/original` downloads the original SVG/image (or original
PDF); `source.pdf` downloads the PDF used by the converter. SVG/image sidecars are
rejected because their PDF hash cannot name the original upload bytes. See
[image intake and preview policy](../docs/pdf-raster.md) for resolution, orientation,
transparency, sizing and defaults. Originals and wrapper PDFs are durable;
versioned `page-N-v2.png` previews remain rebuildable retention-cache files.

SVG import uses the installed Inkscape CLI (tested 1.2.2) to export the document
pages, retaining their physical dimensions and artwork placement. Ordinary text
and strokes stay vector; embedded JPEG/PNG images retain native pixel dimensions.
Inkscape multi-page documents are supported. SVG pixel units use 96 pixels/inch.
Only filtered objects that Inkscape cannot represent in PDF become rasters, at
300 dpi; this is separate from the Studio display-preview resolution.

SVGs must be self-contained: embed JPEG/PNG images in Inkscape before upload.
External resources/stylesheets, DTDs/entities, scripts, animation and embedded
HTML are rejected with guidance; originals remain downloadable even on failure.
The upload is never displayed as active SVG: previews come from the normalized
PDF and the original is an attachment. Defusedxml and Tinycss2 validate the
import copy; Inkscape gets a private temporary profile and a two-minute timeout.
SVG 2 `href` references are normalized to `xlink:href` for older Inkscape versions.
Source bytes are unchanged. Fonts resolve on the **server**, so the upload UI
and live-text source note advise converting laptop-only fonts to paths or
exporting a PDF with embedded fonts. The renderer version is retained in
`inspection.svg_input`. Mixed SVGs start with raster screening enabled; vector-only
SVGs use the ordinary PDF defaults. Preview registers an HPGL draft as usual.

The browser check uses the controller's pinned Playwright dependencies (`npm ci`
in `controller/`) and an isolated state directory with no device connection.
`node controller/scripts/check-paper-smoke.mjs` tests query-only paper checks,
power loss, blocked query writes, fit errors and visible startup failures against
an isolated fake serial bridge; it never contacts Stain. `plot-smoke.mjs` covers
the full simulated plotting lifecycle, and `pdf-raster-smoke.mjs` also checks
opening the latest version from a superseded HPGL tab.
`dev-server.py start/restart` now builds both Rust binaries. The worker locates the
project from the controller's build path; set `PLOTLINE_PDF_PROJECT` to the root
of a complete deployed checkout if relocating the binary. It needs `.venv/bin/python`,
`tools/`, `contracts/`, requirements manifests and `target/release/plotline` there.

### API and durable state

The v1 PDF/ink/paper contract in `contracts/v1/` is implemented through:
`GET /api/v1/capabilities`; multipart `POST /api/v1/documents` with `document` and
optional `intent`; document status and conversion submission; conversion status
with per-page artifact URLs. All mutations require `X-Plotline-Request: 1`.
Additional read routes list documents, download original bytes and fetch previews.
Source uploads are limited to 64 MiB and print intents to 32 MiB. The multipart
envelope allows both at once plus 64 KiB for framing. Conversion and draft JSON
requests accept the same 32 MiB intent size, including detailed map layer bindings.
Regression check: `.venv/bin/python controller/scripts/intent-size-smoke.py`
starts a device-free server and carries a 20,000-layer, 2.4 MiB intent through
upload, idempotent replay, saved recipes, conversion and HPGL draft preparation.
It also checks all four request types still enforce their upper size bounds.

Named recipes use `GET/POST /api/v1/documents/{id}/recipes` and
`GET /api/v1/documents/{id}/recipes/{recipe-id}`. POST accepts `{name, intent}`
for a new recipe or `{id, name, intent, expected_revision}` for an update. Names
are 1–160 characters, requests allow 32 MiB plus 64 KiB for recipe metadata, and
intents are validated against the retained PDF without conversion. A stale revision returns 409 rather
than replacing another editor's save. The document/list endpoints include recipe
summaries and source-page thumbnail URLs. Save As shares the source PDF.

Recipes live at `documents/<source-id>/recipes/<recipe-id>/record.json`; numbered
JSON snapshots retain previous saves. They are durable inputs, excluded from
preview cleanup, and covered by the existing document-state backup selection.
Existing conversion links still open their exact choices. Documents predating
named recipes start from their latest conversion or uploaded intent; Save adopts
those choices without altering the historical outputs.

Uploads optionally accept `Idempotency-Key` (a lowercase, hyphenated UUID).
`GET /api/v1/submissions/{key}` recovers the original receipt after a lost reply;
same-key/same-byte replays return the same document, while changed PDF or intent
bytes return 409. Support is advertised by `implemented.pdf_submission_idempotency`.
See the [submission contract](../contracts/v1/submission.md) for durable client
ordering, exact-byte hashing, compatibility and the isolated acceptance test.

A document is acknowledged only after its original bytes, optional intent and
metadata have been fsynced and atomically published. Inspection errors retain the
PDF; invalid optional intent is reported without losing a usable source preview.
PDF uploads are limited to 64 MiB and review currently supports up to 200 pages.
Conversion revisions retain the complete requested/effective intent, inventory
snapshot, source/settings/converter hashes, artifacts and errors. Changing settings
creates a new ID; earlier successful revisions remain intact. A worker-version
change while queued/running fails the revision rather than mislabeling its output.
Normal conversions retain prepared events, settings, HPGL, the three browser views,
a transparent loss mask and a compact `summary.json`. Artifact URLs include
`summary` and `detail_loss`; `report` is optional
for earlier or full-diagnostic revisions. The browser follows these returned URLs.
Every geometric check runs in both modes. Full diagnostic replay commands are in
[experiment 16](../docs/experiment-16.md#recovering-full-diagnostics).

Two inspection/conversion workers can run at once; status reads stay responsive.
A restart resumes pure inspection for IPP captures and keyed PDF uploads, and completes a finished
conversion's pending handoff. Interrupted conversions retain an error and the
previous draft; they can be regenerated. Physical execution is never replayed.

State is under the configured job root, in `documents/<uuid>/` and
`conversions/<uuid>/`; original HPGL UUID directories remain separate. These paths
are inside the existing workspace backup selection. Writes use fsync and atomic
rename; no database snapshot procedure is needed. Do not discard originals or
revision intents as rebuildable cache.

The [IPP service on 9192](../tools/print_sink/README.md) supplies the durable inbox.
Set `PLOTLINE_IPP_INBOX` to its `captures/` directory (the dev manager does this).
`intake/<capture-id>.json` receipts deduplicate restarts and offline capture.
`documents/<uuid>/print-ticket.json` retains capture metadata and selected IPP
attributes; PDF bytes remain the source of page geometry. `draft.json` atomically
points to current immutable HPGL jobs. Failed replacement keeps the old pointer.
Older captures from the capture-only experiment are not automatically replayed.

Additional APIs: `POST /api/v1/documents/{id}/draft` accepts an intent (or `{}` for
loaded-pen defaults); `POST /api/v1/conversions/{id}/publish` promotes a ready
revision idempotently. Both require `X-Plotline-Request: 1`. These operations
prepare drafts only. `GET /api/v1/documents?summary=true` provides intake/draft
status without full inspections. No shared v1 schema change was required.

## Pen calibration

**Calibrate pens** opens `/calibration`: create a landscape Letter test sheet
(11-inch edge across the carriage), open its HPGL preview, plot through the
normal Plot button, then upload a PNG/TIFF scan.
Click any of the eight slot caps to edit the shared physical carousel, including
empty slots. Check **Measure** for sample pens and choose the **Registration pen**
for alignment marks and the sheet ID. Auto uses the darkest loaded pen; an
explicit choice can use black even when only other pens are being measured.
The target preserves that choice and its carousel snapshot.
Calibration previews start in design colors; an explicit measured-color preview
choice is remembered. The shared pen editor clears stale measured appearance
when a slot's design color or loaded state changes.
The review shows effective widths and paper-normalized sRGB colors sampled from
the center 60% of each stroke. Measured appearance is separate from the design
color used for matching; calibration never recolors the authoring palette.
HPGL preview can show design colors or measured appearance. Width and
appearance updates are independent and explicit; existing map/document inks are unchanged. No target creation or scan
upload initiates physical plotting.

Install the separate analysis environment before starting the service:

```sh
python3 -m venv build/calibration-venv
build/calibration-venv/bin/pip install -r requirements-calibration.txt
```

Run these from the repository root. Targets, original scans and measurement
receipts are retained under the service state directory's `calibrations/`, which
is covered by the existing Plotline backup selection. See
[the calibration workflow](../docs/pen-width-calibration.md) for scanning guidance,
measurement limits, API, recovery behavior and reproducible validation. Real
pen/scanner accuracy awaits the first physical test sheet and scan.

## Use it

To print an existing job, select it, check **Paper & placement** and **Ink &
carousel**, then click **Plot**. The preview is the review: there is no second
confirmation dialog. The button immediately shows **Starting…** while the service
opens one connection, queries identity and the actual drawing window once,
applies the selected layout and pen settings, checks fit, and starts feeding
through that same connection. Connection/fit errors appear inline. Later layout
edits do not change the submitted plot. A carousel change during startup stops
the attempt before drawing; the operator can check the preview and click Plot again.

Each click has a persistent browser request ID, recorded with the frozen settings
before the service starts its worker. Duplicate submissions return that same
attempt, even after completion or a service restart. If an HTTP response is lost,
the browser checks status. An unrecorded request offers **Retry submission** with
the same ID and settings, including after a page reload; it never creates a second
copy implicitly. This request tracking is separate from uncertain serial delivery,
which still requires operator reset and never replays bytes.

The **Plotter** panel shows data sent and offers pause, resume and cancel.
Pause inserts `PU` at a complete command boundary and waits for buffered `OA`/`OS`
replies to confirm pen-up and drained movement before reporting **Paused**. It
remembers the previous pen state and restores pen-down only when resuming a
stroke that was down. Cancelling a paused plot never lowers the pen. The lift
still follows movement already queued in the plotter; use the front panel for
an immediate stop. Pause queries are recorded separately as `pause-query` so
those replies cannot be mistaken for final completion or uninterrupted timing.

Cancel sends `PU;SP0;` and uses the same buffered `OA`/`OS` barrier on the existing
connection. **Cancelling… waiting for the pen to park** stays exclusive until
confirmation; **Plot cancelled · pen parked** then enables recovery or another
plot automatically. The receipt keeps the partial byte count, marks
`execution.cancelled=true`, stores the confirmation and ends in `finished`.
Cancellation before any drawing bytes were sent ends without issuing parking
commands or inventing a hardware confirmation. Missing/malformed replies remain
unresolved; neither a timeout nor reload releases the plotter. Cancel queries are
traced separately as `cancel-query`, outside the immutable drawing byte count.

**Resume from here…** opens an offline recovery preview for this document's
previous attempts. Keep the original sheet registered. Select the recorded
attempt, then scrub the pen-colored bar to match the physical drawing: completed
strokes have full ink, remaining strokes are faded, and the next whole stroke is
orange. Arrow keys on the bar step one stroke; Shift steps ten. The stroke field
and adjacent arrow buttons offer exact positioning. Zoom and pan only inspect
the preview. Nothing opens the serial port until **Resume**.

The recovery preview, its progress bands and stroke numbers always describe the
**full original plot**, including when the selected attempt is itself a repair. The latest
attempt reopens at its saved original cutoff (for example, stroke 441 remains
441 after another pen failure). Older chains of shortened repairs are resolved
back to their original receipt without changing those records. New repair
receipts refer directly to that original and store an absolute stroke/byte
position; old client requests against a shortened attempt are translated once.

Recovery reads the exact archived prepared HPGL (plain or gzip), verifies its
hash and snaps to whole-stroke boundaries. It creates a separate durable attempt
containing a pen-up initialization, absolute coordinates, the recorded slot,
speed and force, and the byte-identical suffix beginning at the selected pen-up
position. It never regenerates, recenters, rescales, or applies today's recipe
or carousel settings to the archived drawing. Recorded slots must be loaded;
replacement pens use the recorded settings. The fresh loaded-window check still
runs before drawing, and normal exclusive ownership, request deduplication and
interruption handling apply. The original attempt remains unchanged.

**Pause before next pen change** defaults on for recovery. The feeder stops
before placing the next nonzero `SP` in any outgoing packet, lifts and confirms
pen-up, then waits. Resume permits that change and pauses again at a subsequent
change; Cancel parks the pen. Final `SP0` parking completes normally. Recovery
is manual: no automatic inference of where ink ran dry, no mid-stroke cuts and
no automatic replay after a disconnect. The separate preview avoids editing the
saved document while finding the cutoff.

New plots also capture Pi transmit-queue timestamps in `serial-timing.jsonl`
beside their exact prepared HPGL, for later plotting-time model fitting.
See [measurement semantics, inspection/export command and fitting plan](docs/plot-timing.md).
The adopted [Snoqualmie timing model](docs/timing-model-2026-11-03.md) shows an
estimated duration beside **Plot**, updating after layout, mapping or loaded-pen
settings change. Calculation runs on dev without contacting stain or delaying
the Plot button; no click on Plot is needed. A failed estimate shows its reason
and a **Retry estimate** button. During feeding, **time remaining** follows the delivered byte
offset and counts down between acknowledgements. Pause freezes the estimate;
resume reanchors it to delivery. Reload restores it from the saved attempt.
The estimate includes modeled buffered movement, but only OA/OS confirmation
finishes a normal plot. An expired estimate says it is waiting for confirmation.
Other speeds than VS5 are extrapolations (noted in the estimate's tooltip).
The pen bands and percentage still describe bytes, not estimated time.

The Rust service embeds the six fitted parameters; Python/Numba is only needed
for refitting. Each new attempt retains its exact-byte hash, estimate and packet
curve in `timing-model.json`; editing today's layout or inventory does not change
that record. `POST /api/jobs/{id}/estimate` accepts the same setup as export and
uses the selected paper. Only an explicit **Last measured drawing area** selection
uses the latest saved paper observation; a registered PDF always retains its
planned sheet. Starting the plot checks the freshly measured loaded sheet and
computes its frozen estimate again. The usual request header is
required. An estimate alone creates no attempt and sends nothing to hardware.
For an exact normalized serial file, the offline native command is:

```sh
controller/target/release/plotline-studios --estimate path/to/prepared.hpgl
```

The progress track is colored by the actual pen runs in the prepared program,
using the physical colors/names captured when Plot was clicked. Sent portions
are saturated, the remainder is faded, and a marker shows the sent-byte boundary.
Band widths match the byte-based percentage; they are not elapsed-time or
physical pen-position estimates. Setup and pen parking are neutral. Hover a band
for its slot/name and data range; the legend identifies the pens. Reloading or
editing the current inventory does not change a submitted plot's colors.

Plotline's current map files already group all paths for each pen: Seattle's
submitted proof uses slots 6 (purple), 2 (red), then 5 (green). The converter's
route optimizer groups by pen and orders paths within each group; it retains
the old route if the candidate fails its travel/pen-selection objective.
Plotline Studios preserves the imported sequence. Externally supplied files can
interleave pens, and their repeated runs appear in the track in that order.
Plotline Studios keeps feeding if the browser closes. If its connection to stain drops,
feeding stops after buffered movement; the service records the interruption
and never automatically replays bytes.
After the last drawing byte, **Finishing…** waits for a buffered `OA;` reply
on the same serial connection, followed by an `OS;` status check. A valid pen-up
position, no HP-GL error and an empty command buffer produce **Plot finished ·
confirmed by plotter**, enabling the next plot automatically. The data-sent bar
still measures bytes; reaching 100% does not itself unlock another plot.
Legacy submitted/cancelled attempts without confirmation retain **Pen parked · mark finished**; new cancellations confirm parking automatically.
Software pause/cancel can lag behind buffered movement; use the plotter's
front panel for an immediate stop. An interrupted or uncertain serial run
requires physically resetting the plotter before **I reset the plotter**.

The **Pens & mapping** drawer separates the physical inventory from each document's requests.
Click any of the eight numbered slots to edit its name, color, width, speed,
force, and state (loaded, empty, or unavailable). Typing a common color name,
such as purple, chooses its swatch; the color picker accepts custom colors.
These settings are shared across documents and browsers, and survive restarts.
They describe the operator's record of the pens, not automatic hardware detection.
Dev's recorded inventory started with 1 black (dry), 2 red, 3 broken, 4 hot pink,
5 green; Jon subsequently added purple in 6. Physical widths remain unconfirmed
until entered by the operator. Empty/unavailable slots cannot plot.

**Match loaded pens** leaves inventory alone and maps each included ink to its
closest usable pen by OKLab color distance, then width. Several inks may share
one slot. Unassigned inks stay visible. Select an outlined pair to inspect a
material difference, accept it on existing paths, or rebuild upstream. There is
no action that replaces recorded physical inventory with document requests.

The main preview resolves **document ink → mapping → physical slot**, using
that slot's color and width on the unchanged centerlines. Empty/unavailable slots
omit their ink and display a blocking message. The source requests remain visible
alongside the mapping. Library thumbnails retain the source's requested appearance.
The service uses the same physical settings for plotting and prepared downloads;
color/width metadata in downloads describes the mapped physical output.

Choose **Add HPGL**, select `.hpgl`/`.plt` files, and choose the source's
coordinate scale. The current Plotline workflow uses **40 units/mm**; the
historical controller used **1021 units/inch**. Neither choice establishes
physical calibration. Imports retain the exact original bytes and record a
SHA-256 digest. Unsupported drawing commands reject the whole import.

The preview offers zoom, pan (including touch/pinch), paper placement,
quarter-turn rotation, ink inclusion, carousel mapping and a source-travel
overlay. **Prepared HPGL** applies the layout at original physical scale;
**Original HPGL** still downloads the exact unmodified source. Layouts are
saved separately per job. Included inks each need a distinct physical slot
1–8. Moving an ink onto an occupied slot sends the displaced ink to the slot
you just freed (or another empty slot when repairing an older duplicate mapping).
Excluded inks keep their reservations too. This changes only document mappings;
it never moves or rewrites the physical pens in the numbered inventory.

**Paper & placement** starts with a planned 11×17-inch sheet. Choose a preset
or enter dimensions/margins. **Turn sheet** cycles through all four quarter-turn
orientations relative to the artwork, including the two opposite placements of
the wide margin. **Artwork** keeps the source drawing upright and turns the
sheet and margins around it. **Facing plotter** keeps the machine fixed and
shows the corresponding artwork rotation: +X toward you/down the screen, +Y
to your right. The wide margin remains at the machine's back; a half-turn puts
it at the bottom of the finished drawing instead of the top.

**Swap feed edge**, under **Sheet dimensions, margins & plot statistics**,
separately changes which paper dimension runs along the feed. Presets recognize
both feed orientations and retain their size name in all four rotations,
including when reopening a saved layout; only nonstandard dimensions show Custom.
ANSI C (17×22 in), D (22×34 in) and E (34×44 in) are included; these are
[engineering sizes](https://support.hp.com/my-en/document/bpp01676), not ARCH
sizes. Rotation is
always evaluated from the immutable original upload; repeated exports are
byte-identical at the same settings, and four quarter-turns restore the starting orientation.
The carriage schematic, operator label and artwork-top arrow explain the resulting
orientation. Both view buttons are directly above the preview. Plot statistics
are collapsed inside **Sheet dimensions, margins & plot statistics**. Changing
the viewpoint never changes exported coordinates.

The plotter senses loaded media, but HPGL `OH` reports the **hard-clip drawing
window**, not full sheet edges. `OP` reports P1/P2 scaling points and is not a
substitute for `OH`. **Last measured drawing area** uses an explicitly dated,
observation, draws that window, and leaves sheet edges unknown. Under **Plotter**,
**Check plotter & paper** requests `OI`/`OH` without initializing, moving, or
starting a plot. It shows the drawable width/height in decimal inches, the
measurement time, and whether the current drawing fits. An unanswered query
reports that Stain is connected but the plotter is not responding; it does not
update the saved measurement. Query writes and replies each have a five-second
timeout. Checks refuse to compete with an active or unresolved plot.
Status polling and **Refresh status** read Plotline Studios's execution state and the
bridge connection status; they do not establish plotter power or paper. Clicking
**Plot** refreshes the observation with one `OI` and one `OH` query, over the same
connection used for feeding. The service checks the prepared coordinates against
the current loaded window; a drawing that does not fit is rejected before any
movement commands. The preview receives the new observation through status polling.
The confirmed 11×17 sheet reads `[-7539,-5089,7539,5089]`:
**376.95 × 254.45 mm** at 40 units/mm; see the physical check below.

For planned sheets, nominal normal-mode margins default to 39 mm at the back (-X)
and 15 mm on the other three edges. These come from the recovered **HP-GL
Programmer's Reference Manual (1984), printed pages 3 and 7 (PDF pages 6 and
10)**; `OH`/`OP` are listed on printed page 24 (PDF page 27). The original is
preserved in `archealogy/hardware-manuals.tar.gz`; inspection copies belong under
ignored `research/controller/manuals/`. HP's [7580B/7585B brochure](https://lastin.dti.supsi.ch/VET/graphics/HP-7585B/5953-4156.pdf)
also documents media sensing. Margin mode and actual sheet placement must be
checked at calibration; the UI does not infer a full sheet from stale limits.

**Margin correction, 2026-10-30:** those manual values are not exact paper-edge
offsets. The model-specific [HP 7585B Service Manual, Table 1-2, printed page 1-2
(PDF page 6)](https://www.bitsavers.org/pdf/hp/plotter/07585-90002_Aug83.pdf#page=6)
specifies **+0/−5 mm** for each margin: 10–15 mm on three edges and 34–39 mm
on the fourth in normal mode. The recovered copy is
`research/controller/manuals/service-manual-07585-90002_Aug83.pdf`. The earlier
sheet-size calculation incorrectly treated the nominal margins as exact.

Jon confirmed that the loaded sheet was standard **11×17 inches**. Three fresh
`OH` replies agreed on `[-7539,-5089,7539,5089]`, a **376.95 × 254.45 mm**
drawing window. Against **431.8 × 279.4 mm** paper, this implies combined
feed-direction margins of **54.85 mm** and combined side margins of **24.95 mm**.
The latter averages 12.475 mm per side, within the documented range; the query
does not establish the separate left/right offsets. The feed total exceeds
the nominal 54 mm by 0.85 mm, still to be checked physically. Keep nominal
planning margins distinct from measured drawing limits and confirmed sheet
dimensions; do not infer exact sheet edges by adding fixed nominal margins.
Raw queries are in `controller/state/diagnostics/2026-10-30/040250Z-loaded-paper.json`.

**Physical rectangle, 2026-10-30:** Jon requested a rectangle at those exact
reported limits, then a repeat in red after the black pen ran dry about 10 cm
into the outline. Both attempts used 5 cm/s and force 4 (34 g); red was slot 2.
He measured **back 39 mm, front 15 mm, left 14.8 mm, right 11 mm**. All four
fall within the service manual's normal-mode ranges. In particular, the side
margins are asymmetric, not two equal 12.475 mm offsets.

Subtracting those margins from nominal 431.8 × 279.4 mm paper gives a physical
rectangle of **377.8 × 253.6 mm**, versus **376.95 × 254.45 mm** commanded:
residuals **+0.85 mm along feed and −0.85 mm across the carriage**. This is close
agreement, not a precision scale calibration. Jon subsequently measured the
rectangle directly across the carriage: **254 mm** with his ruler, **0.45 mm
(0.18%) below** the commanded 254.45 mm. He judged this sufficient for the proof;
his ruler cannot span the longer feed dimension. Keep **40 units/mm** without
a scale correction. Actual sheet dimensions and direct rectangle length along
feed remain unmeasured, so do not assign the residuals to motor scale, paper
cutting or ruler/line-width uncertainty yet. The measured
margin differences place the drawing centre about 12 mm toward the front and
1.9 mm toward the right relative to the sheet centre. These are observations of
this sheet/load; nominal planning defaults and existing job layouts are unchanged.
Exact source, normalized commands, attempt receipts and measurements are under
`controller/state/calibration/2026-10-30-042850Z-margin-rectangle-red/`, with the
black attempt in the adjacent `2026-10-30-042448Z-margin-rectangle/` directory.
Known carousel contents for this trial: 1 black (dry), 2 red, 3 broken, 4 hot pink,
5 green; pen widths remain unconfirmed.

For legacy HPGL, full source ink bounds stay centered in the drawing window even when an
ink is excluded, preserving registration for separate pen passes. Oversized
layouts and duplicate included-ink slot assignments cannot be exported. There
is no fit-to-page scaling. Prepared output normalizes to bounded absolute
integer coordinates and emits explicit `VS` and `FS` **after every nonzero pen
selection**, including when returning to an ink used earlier. Width, speed and
force belong to the physical slot; reassignment uses the newly selected pen's settings. Valid speed
is 1–60 cm/s; force is 1–8, displayed with the corresponding 10–66 gram-force
values (`10 + 8×(force−1)`). The [HP 7585B specifications](https://lastin.dti.supsi.ch/VET/graphics/HP-7585B/5953-4156.pdf)
and [HP's firmware article, November 1981, p. 20](https://hpmemoryproject.org/timeline/marvin_patterson/HPJ1981-11A.pdf)
document these controls. Single-argument VS/FS apply to all pens; repeating them
after each selection guarantees the active pen gets its chosen values. The
program does not rely on the carousel's speed/force defaults.

Opening/importing a document never changes the physical inventory. Older per-job
`ink_settings` remain readable for compatibility but do not control browser
preview, export or plotting; physical slot settings take precedence on the service.
The standalone CLI planner, which has no carousel inventory, still uses source
settings with profile fallbacks. Old paper, rotation, ink inclusion and mapping
choices are preserved.

The inventory is stored atomically as `<state>/carousel.json`, separate from SSH
credentials. `GET /api/carousel` returns `{revision, slots}`; `PUT /api/carousel`
requires the current revision and the usual same-origin `X-Plotline-Request: 1`
header. Conflicting edits return 409 without overwriting either browser's last
saved inventory. Refreshing the carousel, reopening the page, or returning focus
to it loads the current inventory. A one-time migration imports old connection-file
labels/availability and recognized colors; unknown widths remain unknown. Subsequent
connection-file changes do not overwrite the editable inventory.

The loaded-pen editor has **Cancel** and **OK** in a fixed footer that remains
visible while its fields scroll. Cancel (or Escape) discards edits; the solid
OK button saves them. Recognized color names, including turquoise, update the
color swatch as they are entered. A pen name can still be descriptive text.

Browser exports/plot requests include `carousel_revision`; stale previews are
rejected. Clicking Plot freezes the complete physical snapshot. Changing a physical
slot during startup stops the attempt before drawing; later edits do not alter an
already running plot.
The original HPGL and source request metadata remain immutable.

The **browser download** contains color/width comments plus explicit motion
settings and ends pen-up with `SP0`. This is interchange HPGL for saving and
reimporting a prepared layout. It is not sent to hardware. The CLI planner's
**`prepared.hpgl`** and `Prepared.commands` remain the strict serial format:
one bounded command per line, no comments. The planner strips CO when preparing
an interchange file, and records ink settings in `plan.json`. Only normalized
device commands are prepared on dev and sent through the Pi's byte bridge.

The browser uses physical slot widths. Cap labels are rounded for readability;
the editor retains the full underlying value. Unknown diameters show **?** and
use a thin screen-width centerline. Physical width accepts 0.01–5 mm. A material
substitution on a PDF-derived job requires explicit acceptance of the exact
selected pen or upstream rebuilding. Acceptance keeps the original paths and
hatch spacing, so the preview can show marks closing gaps or entering previously
cleared areas. The cached source SVG
remains a centerline interchange representation, and source coordinates are
never changed by the width control. Layout dimensions describe full source ink
bounds after rotation;
included-ink mileage and path counts track the selected inks. Source pen-up
travel excludes the unknown initial carriage position and mechanical pen swaps;
the **Pen-up moves** overlay shows those moves from the original upload,
including excluded inks, rather than recomputing travel for the prepared job.
Y travel is measured in the source axes. Feature/policy attribution remains in
the converter's reports; this importer does not yet ingest those sidecars.

## Responsive rendering

The previous browser inserted the full SVG into the page and changed its
viewBox on every wheel event. Roosevelt alone has 428,097 decoded points.
The new preview parses the generated SVG into chunked `Path2D` geometry in a
module worker and draws it with `OffscreenCanvas`. The main thread has only a
small paper diagram and a bitmap. Pan/zoom transform that bitmap immediately
in an animation frame; after 140 ms without input, the worker refines the view.
There is at most one render in flight and only the latest requested view is
retained. Geometry outside the view is culled in chunks. Bitmaps are bounded
to 4096 pixels per side and at most 2× device pixel ratio. Old bitmaps are
consumed/closed, and changing jobs terminates the old worker. Library thumbnails
are small PNGs produced by one additional background worker at a time while the
Open dialog is visible. Inactive tabs retain choices, not live rendering workers.

This requires a browser supporting module workers, OffscreenCanvas 2D and
ImageBitmap rendering. There is no main-thread vector fallback. The parser
reads **our generated preview SVG**, not arbitrary uploaded SVG. A cached image
may look soft or have newly exposed blank edges during a gesture; it sharpens
when the gesture ends. No simplification is applied to exported HPGL.

Measured on dev in headless Chromium, 1440×1080, Playwright 1.64.1, 4× CDP
main-thread CPU throttling, the viewport scrolled fully into view, 60 synthetic
wheel inputs at nominal 16 ms intervals, using Roosevelt's 428,097 points:

| Measurement | Old SVG UI (`b9459e0`) | Worker/bitmap UI |
| --- | ---: | ---: |
| Time to process input sequence | 2454 ms | 997 ms |
| 95th-percentile frame interval | 33.3 ms | 16.8 ms |
| Longest frame interval | 266.7 ms | 16.8 ms |

These are one controlled comparison, not a frame-rate guarantee for another
browser or laptop; CDP throttles the UI thread, not all worker/GPU work. Raw
results are in ignored `build/controller/performance-{before,after}-visible.json`.
Run the non-mutating measurement against a running service with:

```sh
CPU_RATE=4 REPORT=build/controller/performance.json \
  node controller/scripts/measure-preview.mjs
# Optional: PLOTLINE_URL=http://127.0.0.1:9192
```

## Build and run

From `/root/plotline`, using Rust 1.94.1 or newer:

```sh
npm ci --ignore-scripts --no-audit --no-fund
cargo build --locked --release --manifest-path controller/Cargo.toml
controller/target/release/plotline-studios --import controller/samples/three-pens.hpgl
controller/target/release/plotline-studios --listen 127.0.0.1:9191
```

The server embeds its HTML, CSS, JS and generated Pattern Studio gallery PNGs in
the binary. The Cargo build runs the shared JavaScript generators under Node
20+ and rasterizes their examples with the root MuPDF npm dependency. Rebuild and restart after
any source/UI change. Preview mode requires only the Rust binary. Hardware mode
also requires OpenSSH on the server, plus the restricted SSH key
and pinned host key described in [the appliance runbook](appliance/README.md).
Node is used at build time and for browser tests; Python manages dev deployment and runs the Pi
bridge. The web runtime is Rust plus OpenSSH and never opens a local serial device.

Persistent dev service, listening on port 9191:

```sh
python3 controller/scripts/dev-server.py start
python3 controller/scripts/dev-server.py restart
python3 controller/scripts/dev-server.py restart --device-config controller/state/private/stain-connection.json
python3 controller/scripts/dev-server.py status
journalctl -u plotline-studios-dev -f
python3 controller/scripts/dev-server.py stop
```

`start`/`restart` build the locked release binary and install/start only the
`plotline-studios-dev` systemd unit on this machine. They refuse to proceed while a
plot is feeding, unresolved, or waiting for the operator to confirm the pen is
parked, and while PDF inspection/conversion work is queued or running. `stop`
uses the same guard and also disables boot startup. These checks read local
state and do not query the plotter.
The service has a private device namespace and write access only to its spool.
`--device-config` enables the SSH connection and records its absolute path in
ignored `controller/state/dev-device-config.json`, so ordinary restarts retain
it. Use `restart --preview-only` to remove that selection. Fresh checkouts
default to preview mode. This does not install on print_server or move IaC refs.
This is an unauthenticated private-development service; production access
control and an application installer remain future work.

The connection JSON contains `destination`, `identity_file`, `known_hosts_file`
and `plotting_enabled: true`. The enable setting belongs to Plotline Studios and defaults
false when absent. A legacy `carousel` object with `label`/`available` entries is
accepted for one-time inventory migration. Edit physical pens in the browser
thereafter; edits are saved immediately with no restart. Keep the connection
configuration and keys outside Git. Plotline Studios places SSH control sockets inside
the writable plot spool.

Import a new conversion without using the browser:

```sh
controller/target/release/plotline-studios \
  --import build/ravenna-letter/plot.hpgl \
  --name 'Ravenna letter.hpgl' --units-per-mm 40
```

### Sending generator output to dev Plotline Studios

From the Plotline checkout, upload the rebuilt Letter proof and give it the
planned HP7585 sheet layout:

```sh
python3 controller/scripts/import-job.py \
  build/ravenna-letter-hp7585/plot.hpgl \
  --name 'Ravenna — HP7585 Letter proof.hpgl' \
  --layout controller/layouts/hp7585-letter-portrait.json
```

The helper uses only Python's standard library and the HTTP API. Its default
server is `http://dev.iac.jonh.net:9191`; use `--server URL` for another instance.
It prints JSON with the job ID and direct preview `url`. Open that URL or refresh
the library. This uploads original HPGL for preview and preparation only; it
does not queue or send anything to stain or the plotter.

Rerunning with identical bytes and `--units-per-mm` (default **40**) reuses the
existing job, including its name and operator settings. Changed output creates
a new job and retains the earlier version. Matching is by SHA-256 and coordinate
scale, not filename. This is a client-side lookup, so concurrent first imports
of the same file can create duplicates.

`--layout` applies paper/orientation defaults only when creating a job. Add
`--update-layout` to deliberately reapply them to an existing job. Pen inclusion,
carousel assignments, widths, speed and force remain untouched; new jobs take
their ink defaults from the HPGL metadata. The optional layout JSON may contain
only `paper_x_mm`, `paper_y_mm`, `margins_mm`, `window`, `rotation` and `facing`.
Omit `--layout` for ordinary uploads that will be arranged in the UI.

The supplied Letter layout puts **279.4 mm along feed (X)** and **215.9 mm across
the carriage (Y)**, with margins **back, front, left, right = 39, 15, 15, 15 mm**.
The 90° artwork rotation gives an upright portrait view facing the plotter.
`window: planned` avoids treating a saved hardware observation as the loaded
paper. These are layout preferences, not a rescaling or a claim of measured fit;
the UI and prepared-file export perform the fit check. A failed layout save
leaves the uploaded source available and reports its URL; fix the JSON and retry
with `--update-layout`.

### HTTP API

Refresh the browser library to see CLI imports. The HTTP equivalent uses a raw
body, explicit scale and a custom upload header:

```sh
curl --fail-with-body \
  -H 'Content-Type: application/octet-stream' -H 'X-Plotline-Upload: 1' \
  --data-binary @build/ravenna-letter/plot.hpgl \
  'http://127.0.0.1:9191/api/jobs?name=Ravenna%20letter.hpgl&units_per_mm=40'
```

`GET /api/status` reports preview mode and `hardware_enabled: false` without a
device configuration, or controller mode with `hardware_enabled: true` when
configured. The latter does not assert that stain or its plotter is online.
`GET /api/jobs` lists metadata; `/api/jobs/{id}` returns one record.
`/api/jobs/{id}/preview.svg` and `/api/jobs/{id}/source.hpgl` return its preview
and original bytes. Uploads are limited to 64 MiB, eight million commands and
eight million coordinate pairs; parsing runs off the HTTP executor with two
bounded workers. Uploads require the custom header and same-origin browser
requests. There are no state-changing GET endpoints.

`GET /api/device` returns `live: false` and an optional saved observation.
`GET/PUT /api/jobs/{id}/setup` reads/saves the separate layout. A JSON
`POST /api/jobs/{id}/prepared.hpgl` with the same layout exports a prepared
file. Both writes require `X-Plotline-Request: 1`, `Content-Type:
application/json`, and a same-origin browser request. Geometry work remains
inside the existing two-worker bound.

Hardware routes, enabled only with `--device-config`:

- `GET /api/plotter`: connection, local execution and durable submission status.
  It does not send serial queries or start/retry plots. Physical inventory is
  available separately through `/api/carousel`.
- `POST /api/jobs/{id}/plot`: takes `{"attempt":"UUID","setup":{...}}`, validates
  and freezes the selected settings, durably records the attempt, and returns 202.
  A background worker opens the serial connection, queries `OI`/`OH`, generates
  and checks the commands, and feeds them over that connection. A duplicate UUID
  with identical job/settings returns the saved result without launching a worker;
  reusing it for different settings is rejected. It remains single-use after restart.
- `GET /api/plots/{attempt}/progress`: returns `total_bytes` and ordered `sections`
  (`slot`, `color`, `label`, `start_byte`, `end_byte`). It reads immutable prepared
  commands and the recorded physical pen metadata; it performs no serial I/O.
  The browser loads it once per attempt, independently of frequent status polls,
  and paints the bands on a small canvas with at most eight legend entries.
  Older attempts without recorded physical colors use neutral bands.
- `GET /api/jobs/{id}/attempts`: recorded attempts with prepared programs,
  newest first. `GET /api/plots/{attempt}/recovery` returns the immutable ticket,
  pen bands and ordered `[start_byte, slot]` stroke index for the full original,
  plus `selected_attempt` and its zero-based original `start_stroke`; `/program.hpgl` returns
  the hash-verified original device program. These reads never open serial.
- `POST /api/plots/{original}/recovery`: takes a new `attempt` UUID,
  `source_sha256`, zero-based `start_stroke`, and `pause_before_pen_change`.
  Returns 202 with the repair ticket; duplicate identical requests return the
  same attempt. Its `recovery` records the parent attempt/hash and starting
  stroke/byte. Only this explicit POST connects and begins drawing.
- `GET /api/plots/{attempt}`: returns `{"submission":...}` or null if unrecorded,
  for resolving an uncertain HTTP response. This reads local state only.
- `POST /api/plotter/{pause,resume,cancel,complete,acknowledge_reset}`: takes
  `{"attempt":"UUID"}` and acts only on the matching execution.
- The retired `plot-plan` and `/start` routes return 410 and ask old browser tabs
  to refresh. They cannot start a plot.

All hardware POSTs require the same custom request header and same-origin
browser checks. Device HTTP work has a separate two-operation bound; only one
feed worker can run. Layout and physical settings are frozen at click time;
actual window, commands and hashes are recorded before the first drawing byte.
Uploading to the library never executes a job. A service restart before feeding
marks the attempt failed; a restart after feeding may have begun marks it
interrupted. Neither case automatically resumes or replays commands.

All job semantics live in the Rust service (`plot.rs`); `serial.rs` carries byte
packets over persistent SSH. Stain knows only port ownership, read/write bytes,
and hardware flow control. It has no HPGL validation, job spool, checksums,
execution records, paper decisions, or automatic pen commands. Explicit pause
and cancel are generated by Plotline Studios at complete command boundaries. On link
loss, a packet may be partly delivered; the service blocks another plot until
the operator resets the hardware and acknowledges that reset.

## Interpreter contract

Supported: `IN`, `PA`, `PR`, `PU`, `PD`, `SP0..8`, quoted `CO` comments, and
single-integer `VS1..60` / `FS1..8` settings. `VS`/`FS` initialize per-ink
controls when constant during that ink's drawing; they do not affect geometry.
The shared converter/previewer comment contract is:

```hpgl
CO"pen id=2 r=209 g=62 b=54 width_mm=0.3";
```

`id` is the source pen number (1–8), RGB values are 0–255, and optional
`width_mm` is a finite width in millimetres (0.01–5). Width is metadata about the
physical pen, not a native HP7585 width command. Same-color inks with different
IDs/widths remain distinct. Comments must precede that ink's drawing. Unknown
comment fields remain ignorable for compatibility, but invalid recognized
fields reject the import. Color-only historical comments still work.

The converter now emits this metadata from its palette and configured pen
width, converting points to mm (`pt × 25.4 / 72`). Its independent decoder skips
the comments and still checks the exact quantized paths. No existing saved
original is rewritten or assigned a guessed width. Reimport older files if
they contain metadata/settings that predate their cached job summary.

These comments select preview colors. Without a palette, slots 1/2/3 are black/red/green;
the other five legacy slots are available for inspecting old jobs. These are
display colors, not installed-pen detection. No white pen is inferred.

Coordinates are comma-separated integer pairs, including negative values and
multiple pairs per command. Case-insensitive mnemonics and semicolon/newline/
adjacent-command separators are accepted. Mode-only PU/PD and coordinates on
PA/PR are interpreted. Initialization is required; an absolute pen-up move
must establish position before a relative move or drawing. Reinitialization
resets pen/mode state without inventing carriage travel. SP0 moves are travel,
not ink. Pen changes preserve the source's logical up/down state.

Scaling, clipping, line types, arcs, circles, native labels, fills, HPGL/2, and
device-control escapes are unsupported. The interpreter refuses them rather
than dropping them. There is no arbitrary passthrough-to-hardware mode.

## Validation

```sh
cargo fmt --manifest-path controller/Cargo.toml -- --check
cargo clippy --locked --manifest-path controller/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path controller/Cargo.toml
cargo build --locked --release --manifest-path controller/Cargo.toml
npm ci --prefix controller
controller/node_modules/.bin/playwright install chromium
npm test --prefix controller
node controller/scripts/carousel-save-smoke.mjs
node controller/scripts/recovery-smoke.mjs
# Optional Firefox coverage (after playwright install firefox):
PLOTLINE_BROWSER=firefox node controller/scripts/recovery-smoke.mjs
python3 controller/scripts/check-corpus.py
```

Rust tests cover coordinate modes, multi-point motion, initialization, unknown
position, palettes, legacy separators, unsupported commands, exact-byte storage,
restart discovery, import rejection, HTTP routes and cross-origin rejection.
The browser check uses a temporary spool/server. It checks actual raster colors,
physical export coordinates/slot numbers, non-cumulative rotation, actual raster
width changes, metadata round-trips, physical-slot VS/FS, many-to-one assignments,
editable eight-slot inventory, best color/width matching, read-only document
requests, removal of the inventory-overwrite shortcut, shared persistence,
stale-edit/export rejection,
C/D/E presets, collapsed statistics, exact original bytes, persisted
layouts, fit/conflict rejection, saved limits, cross-origin protection, zoom/pan
and phone layout. A deterministic two-million-point job exercises rendering,
rapid wheel input and switching jobs while a worker is still loading. If
Ravenna output exists it also renders that actual map. Screenshots are written
under ignored `build/controller/`. The corpus check is optional and compares
generated HPGL's path counts and pen-down distances against converter reports.

`recovery-smoke.mjs` checks full/faint/highlight pixels, exact keyboard stepping,
shared zoom controls, no serial I/O while scrubbing, the byte-identical recovered
suffix, confirmed pen-up before the next pen change, and cancellation without
lowering. `PLOTLINE_RECOVERY_ARCHIVE=/path/to/attempt` additionally copies a real
archive into the isolated test spool to measure preview/repaint latency; that
archive is only read and rendered, never fed. Rust tests cover compressed archive
integrity, settings restoration, idempotency, exclusivity, repeated pen changes,
delayed pen-up confirmation and malformed replies. All use fake transport.

The second browser suite uses a file-backed fake plotter with no SSH or serial
access. It checks one-click start with one connection/query pair, frozen settings,
duplicate submissions, lost HTTP replies and reload/retry recovery,
pause/reload/resume/cancel/finish, startup cancellation, paper fit, lost byte acknowledgements,
offline state, blocked slots, cross-origin rejection and phone layout. Killing
and restarting the server mid-stream never replays bytes. Rust tests separately
cover durable intents, controls and uncertain delivery. On 2026-10-30,
23 controller Rust tests, 17 appliance Python tests and both browser suites
pass, along with fmt and clippy. Real hardware completed the red rectangle and
the Ravenna map with the earlier spooler. Physical drawing through the new byte
bridge remains a separate operator action.

Carousel follow-up on 2026-10-30: all 25 controller Rust tests, both browser
suites, fmt and clippy pass. The tests verify inventory persistence, stale edits,
physical settings overriding document requests, frozen-review invalidation,
actual purple raster pixels, width coverage, physical metadata/VS/FS export,
matching, and document-request reset. Desktop/phone screenshots are
`build/controller/carousel-desktop.png` and `carousel-phone.png`.

Pen-progress follow-up: 27 Rust tests pass, including exact byte coverage and
repeated pen selections. The device browser suite checks actual band pixels,
marker position, physical-color snapshots, reload caching and desktop/phone
layout. Screenshots: `build/controller/pen-progress-desktop.png` and
`pen-progress-phone.png`. No hardware was contacted by these tests.

Direct-Plot follow-up: 26 Rust tests cover the combined submission/measurement/feed
path and single-use request IDs. The browser suites run against isolated spools
and the fake device. To build/test while the live service feeds a plot, leave its
binary untouched:

```sh
CARGO_TARGET_DIR=controller/target/direct-plot cargo build --locked --release --manifest-path controller/Cargo.toml
PLOTLINE_BINARY=/root/plotline/controller/target/direct-plot/release/plotline-studios npm test --prefix controller
node controller/scripts/carousel-save-smoke.mjs
python3 controller/scripts/test_dev_server.py
```

Verified on 2026-10-29: 16 controller Rust tests, 29 converter Rust tests, fmt,
clippy and the expanded desktop/phone browser check pass. The earlier corpus
check parsed all 26 generated HPGL jobs (including the approximately 44 MiB
dense-map regressions), with path counts and pen-down mileage matching
all 26 converter reports. The recovered `inkscape-geom/foo.plt` renders 39
paths across eight pens using the legacy scale. The archived `testpage.plt`
contains `SP9` and is deliberately rejected by the eight-slot contract; this
is a newly recorded input limitation, not an edit to the archaeology findings.
The controller delivery's 165 checksums, provenance, asset references and
static Python syntax checks still pass. No archived program was run.

## State and recovery

Default state: `/root/plotline/controller/state/jobs/` when launched from the
repo root. Use `--state /absolute/path` to override it. The spool-root
`carousel.json` holds the shared physical inventory and its revision; preserve
it with the job library. Each UUID job
directory contains immutable `source.hpgl`, `job.json`, and generated
`preview.svg`, plus optional mutable `setup.json` for its layout. Setup updates
validate first, sync a unique temporary file, rename atomically and sync the
directory; rejected updates leave the previous layout intact.
Imports write and sync a staging directory before an atomic rename. Incomplete
`.import-*` directories are ignored; preserve them for diagnosis after a crash.
The optional spool-root `device-observation.json` contains only `identity`,
`observed_at` (ISO timestamp), `units_per_mm`, and integer `limits` [xmin, ymin,
xmax, ymax]. Absence simply disables saved-window mode. The initial dev observation
was copied from the uniform 100-query replacement-dongle capture in
`controller/state/diagnostics/2026-10-29/dongle-2-usbmon.json`; its timestamp is
the capture file's completion time. On 2026-10-30, it was explicitly refreshed
from the three matching queries of the confirmed 11×17 sheet described above.
Clicking **Check plotter & paper** or **Plot** refreshes this observation;
ordinary status polling does not. `POST /api/plotter/check-paper` requires the
usual write header and accepts `{}` or `{job_id, setup}` for an exact drawing-fit
check. It creates no plot ticket and sends only identity/paper queries.

An older PDF-generated HPGL tab shows a version notice and **Open latest version**
when PDF Studio has prepared a replacement. This opens the current version while
retaining the older tab and its edits. **Open…** in the top bar also opens the
library's current version. Plot is disabled for an older version, with the same
check enforced on submission. Startup failures remain visible in the Plotter
panel and error banner even when no drawing was sent.

`plots/<attempt-uuid>/` contains immutable `prepared.hpgl` and `plan.json`, plus
the durable `record.json` submission state. `plots/active.json` identifies the
most recent submission; retain all these files with the job library. The same
UUID is a single-use **service** attempt ID. Each record now contains local
execution state and byte/command progress. A restart before feeding marks the
attempt failed; an active/paused feed becomes interrupted. Neither replays.
New successful attempts retain completion evidence in `execution.completion`:
query method `hpgl-oa-os`, returned position/status and confirmation timestamp.
Queries are recorded as `completion-query` writes in the serial timing trace;
they do not change the immutable drawing, its hash, byte totals or pen progress.
The `finishing` state retains exclusive ownership of the serial connection.
Browser refresh/polling cannot issue queries or start another drawing.

`OA` waits up to 30 minutes for queued work (including View-mode suspension),
then `OS` has a five-second response deadline. A missing, malformed, pen-down
or error reply, or a restart during that wait, leaves `completion_unknown`.
This distinguishes known drawing delivery from unconfirmed completion; it never
replays bytes or assumes that a timeout means success. Reset acknowledgement
is required before another attempt because an unanswered query can leave a
delayed reply on the serial connection. Legacy submitted/cancelled attempts retain
the operator-completion requirement. New cancellations use a separate `cancel-query`
barrier after `PU;SP0;`, retain their partial delivered-byte count, and finish
automatically only after confirmation. A restart during cancellation confirmation
leaves parking unresolved and never resends the drawing or parking commands.

Protocol reference: HP 7580B/7585B/7586B *Interfacing and Programming Manual*,
07580-90034 (November 1985), sections 9-2, 9-4, 9-7–8 and 13-30.
HP-GL output instructions are buffered in FIFO order; immediate device-control
queries such as `ESC . B`/`ESC . O` bypass that queue. Completion uses the
buffered position reply after the existing final pen-up/store commands, followed
by status validation. It does not infer completion from an immediate buffer-space
query. The isolated serial fixture tests the delay, framing, failures and state
transitions; physical end-of-motion timing still needs a check on the actual
HP7585 before deploying this change. The active plot/service is left untouched.
**Pen parked · mark finished** and **I reset the plotter** update local state;
they do not query or command the Pi. The one-time legacy execution import is
recorded in `plots/legacy-device-status.json`; the old Pi spool is never used
for new jobs or recovery.
There is no live database requiring a separate snapshot. Copy complete job
directories to restore, then restart or refresh. Preview cache regeneration
is currently done by reimporting the saved original at its recorded scale.

`controller/state/` is ignored by Git but included by the existing Plotline
workspace selection in `/root/mirror_manifest.json`. `target/` and
`node_modules/` are excluded by global backup rules. Registration does not
prove a scheduled backup has run. Source is in the existing private home repo,
`/mnt/dev-source/repos/plotline.git`; nothing is externally published.

## Path to the plotter

The [Pi appliance and fresh-card runbook](appliance/README.md) records the
architecture, actual stain inspection, reproducible OS image, restricted SSH
protocol, serial feeder, recovery rules and remaining hardware checks. Stain
turned out to be an original ARMv6 Model B Rev 2, so all geometry and preview
work belongs on dev (later a production guest). The service now owns the entire
plot lifecycle and holds one byte connection through stain. All physical policy
and enable/disable configuration live in Plotline Studios. The repeatable bridge
installer and card builder share the same source; no manual Pi edit is needed.

The `--prepare` CLI and browser prepared-file download remain offline operations.
Ravenna attempt `a626cc9b-0544-47ce-8457-d66a8ea62011` sent 90,450 commands;
Jon subsequently confirmed completion and powered off the plotter. That completed
record is preserved on dev during bridge migration. The new transport is checked
with PTYs, browser-level disconnect/restart tests, and read-only live queries;
a physical drawing through the new bridge remains a separate operator action.

### Earlier spooler checkpoints (superseded architecture)

Live web verification on 2026-10-30 prepared the current Ravenna Letter proof
(90,450 commands), using temporary browser-only assignments to working slots.
It left the saved layout and Pi execution unchanged. The fresh window was
`[-7533,-5088,7533,5088]` (376.65 × 254.4 mm), slightly smaller than the earlier
boundary rectangle; reviewing that exact-edge rectangle correctly rejected it.
The cause of this small reported-window change is unconfirmed. Evidence is in
`controller/state/diagnostics/2026-10-30/web-review.json`; no Start request was
issued. At that checkpoint, the installed agent SHA-256 was
`2cae67011431dcbb25a5f32d63a6e9f6ce86f6ad827df046f666ac14ff861793`, with the
previous installation retained on stain under
`/var/lib/squidprint/upgrades/20261030T053824Z-94b8a5dd-3303-4445-a9ae-6019a60da113/`.

The first browser Start attempt (`96c55970-7a44-46aa-bb64-90d4eb77195f`) then
revealed a performance mistake: validating 90,450 commands on stain took
41.45 seconds, longer than the RPC's 30-second deadline. The complete file
arrived but no Start request/receipt followed; the prior rectangle remained
the last execution. The first fix moved validation to dev, with no second scan at Start.
The original failed ticket remains preserved and never automatically retries.
The UI summarizes old timeout failures instead of exposing their traceback.
With the fix installed, the same 90,450-command file validates on dev in
**0.086 seconds**. Reusing its already-uploaded bytes, the SSH status/commit
exchange completes in **10.27 seconds**; this is not a new full-transfer timing.
At that checkpoint the Pi's last execution was still the red rectangle. Evidence is in
`controller/state/diagnostics/2026-10-30/dev-validation-fix.json`. That
agent SHA-256 was `ae7766bb988d49541bd1e3fa017f853bfadcc70e82d73cfe34c20bec9e9cf475`.
The 25 appliance tests include sender-side rejection before upload and binding
the stored result to the exact file and window; 21 Rust tests, clippy and the
plot-controls browser suite also pass. A live UI check confirms rounded widths
preserve full export precision, and the old traceback is summarized.

### Production and historical source

The earlier controller's behavior is reference material, not code imported
into the new process. No archaeology conclusions were silently rewritten.
Useful changes include multi-coordinate parsing, explicit units, persistent
source hashes, read-only GETs, no pickle deserialization, and no architecture
test that can accidentally activate a serial device. Archived LCD and Tenino
conversion code are not dependencies.

Production needs its own installer/readiness checks and integration using
IaC's `DevProject` contract, alongside plotter-previewer on print_server if
Jon chooses that guest. No production publication/bootstrap/rollback commands
exist yet for Plotline Studios; no deployment marker was created or advanced.

### Conversion progress

PDF studio shows conversion phases, per-phase item counts and elapsed time. These
counts are not an overall ETA. Conversion status GETs expose optional `progress`
and `elapsed_seconds` while queued/running; terminal status remains authoritative.
The converter log is written while the child runs. See
[experiment 13](../docs/experiment-13.md#progress-and-api) for fields and limits.

## Shared pen display

Map, PDF and HPGL studios use `shared/web/inksets.js` and `inksets.css`. See
[the accepted plan](../docs/pen-workflow-plan.md) and
[the contract extension](../contracts/v1/README.md#shared-pen-workflow-extension).

PDF source colors/widths are read-only. The tray always follows carousel order,
stacking inks assigned to each slot; exact members and individual mappings remain available. New ordinary
PDFs default to **Preserve document widths**, allowing multiple passes.
**Use pen width** selects one pass per visible source stroke. Explicit authored
intents and older revisions preserve their policy.

Select a document ink, then click its destination cap in the carousel row.
The page dims during assignment; Escape, Cancel or clicking outside cancels.
Stacks open one inline member list, with clickable whole rows and **Select all**.
**Map automatically** chooses the closest loaded color, then the closest diameter
when colors tie. It skips unavailable, empty, white and unmeasured pens. PDF
destinations are real pens; there is no custom conversion-ink editor. No mapping
action opens a dialog or submits conversion. Source identities stay unchanged and the preview becomes dirty after
an assignment. **Edit** separately records what is physically loaded.

In HPGL Studio, material differences offer inline **Use existing paths** or
**Rebuild upstream**. Rebuilding selects the source studio with
pending substitutions; it does not convert or plot automatically. Multiple inks
can share a physical slot. There is no inventory-overwrite shortcut.

The browser regression uses a non-secure HTTP hostname as well as localhost.
ID creation must work without `crypto.randomUUID`, which is unavailable on the
dev site's HTTP origin; the shared ID helper uses `crypto.getRandomValues`.

Additional isolated validation (no physical device):

```sh
.venv/bin/python -m unittest discover -s tests -p test_pen_workflow.py
node controller/scripts/pen-workflow-smoke.mjs
node controller/scripts/carousel-save-smoke.mjs
```

## Automatic storage retention

The service runs `controller/scripts/retention.py` at startup and every minute.
The versioned defaults are in `controller/retention-policy.json`; optional keys in
`<state>/storage-policy.json` override them. `storage-status.json` records the last
sweep, errors, skipped unfinished work and retained cache bytes. No cron job or Pi
change is required. Install the Python scripts and policy alongside the binary.

* Keep original PDFs, all authoring/print intents, settings, carousel inventory,
  calibration scans, job history, submission receipts and exact HPGL indefinitely.
  HPGL becomes `.hpgl.gz`, using gzip level 6; decoded bytes, hashes, offsets and
  HTTP download filenames stay unchanged. Readers accept both old plain files and
  archives. Each archive is decoded and SHA-256 verified, then durably published,
  before its original is removed. Unfinished/uncertain plot attempts stay untouched.
* Delete the allowlisted conversion geometry/events and duplicate diagnostic SVGs
  after conversion reaches a terminal state. Check the retained PDF's hash first.
  Keep summaries, recipes, source/converter fingerprints and error records. Legacy full
  reports first become compact summaries retaining metrics and provenance; their
  duplicate path geometry is then discarded. Unknown files are left alone.
* Preview cache: at most **512 MiB**, expiring after **seven days without access**,
  oldest access first. SVG caches are compressed too. The limit is enforced after
  each sweep, not during running conversions. PDF thumbnails and library SVGs
  regenerate on demand. PDF Studio shows the original PDF and **Rebuild preview**
  after a conversion preview expires. That creates a new revision with today's
  converter; it never overwrites the old recipe or exact HPGL. An old revision can
  still be published to HPGL Studio directly from its retained commands.
* Completed detailed timing traces: gzip, **90 days / 64 MiB**, oldest first.
  Keep their SHA-256 in `timing-archive.json` and retain plot records, completion
  evidence, prepared HPGL and model coefficients. The timing inspection/fitting
  tools accept archives transparently. Refitting requires a trace still within
  retention, or one explicitly copied to an external research archive.
* The IPP callback drops a raw transport PDF only after its durable captured copy
  is verified byte-identical. Startup clears verified older duplicates. Unmatched
  spools, captures and import receipts stay intact; those may be unique inputs.

Manual preview/apply (no hardware access):

```sh
python3 controller/scripts/retention.py --state controller/state/jobs
python3 controller/scripts/retention.py --state controller/state/jobs --apply
```

Dry runs report compression candidates without actually compressing them, so their
byte-cap eviction estimate is conservative. Running and prepared jobs, symlinks,
unknown directories and hidden staging inputs are excluded. Original input storage
is deliberately not capped; the bounded part is rebuildable cache and old telemetry.
MapStudio has its own equivalent policy on its branch; see `cartographer/README.md`.

Validation for the retention change: 54 Rust tests (including fake serial sends
from archived sources), six collector tests, eight print-contract/capture tests,
seven timing tests, and the PDF browser test including eviction, exact archived
HPGL publication and rebuilding a new preview. The broader plot browser script
hits an existing phone-width assertion at 390 px on both the previous binary and
this change; its earlier serial/completion scenarios pass. No physical plot is
started by these checks.

## Shared Studio notifications

HPGL, PDF and Map Studio, calibration, and navigation use `studioNotice` from
`shared/web/studio.js` and the matching `studio.css` rules. Every toast has a
**× / Dismiss message** button in the same compact corner panel. There is one
panel per page; new messages replace the old message. Long text scrolls without
hiding the close button. Dismissal changes neither the recipe nor the preview.
Repeated status renders leave a dismissed message hidden until the operation
clears its notice or produces a different message. Navigation retains its links
and buttons; temporary focus feedback expires after five seconds. A stale timer
or completed action cannot dismiss a newer message. Application status clearing
does not erase navigation actions. Inline progress and validation remain inline.

The Map branch mirrors the same shared files and adapts its optional expiry to
this helper; do not introduce a separate notification renderer in a Studio.

```sh
node controller/scripts/studio-notices-smoke.mjs
PLOTLINE_BROWSER=firefox node controller/scripts/studio-notices-smoke.mjs
node controller/scripts/studio-tabs-smoke.mjs
node controller/scripts/pen-workflow-smoke.mjs
PLOTLINE_MAP_PROJECT=/path/to/map/checkout PLOTLINE_MAP_PYTHON=/path/to/python \
  node controller/scripts/studio-navigation-smoke.mjs
```

These use isolated fixtures with no physical plotting. They cover dismissal,
repeated status updates, asynchronous replacement, action links, narrow screens,
the real loaded-pen mapping notice, and notices in each Studio.

The combined PDF preview/registration browser check runs without hardware:

```sh
node controller/scripts/pdf-preview-plot-smoke.mjs
```

It covers slow registration, reload recovery, settings edited in flight, registration
failure/retry without conversion, immutable old jobs, navigation only on an explicit
Open plot click, historical revisions, and empty pages. The PDF workspace and raster
browser checks also verify automatic draft registration after preview.

## Shared paper units

Every Studio and calibration uses one **Paper units** preference: decimal inches
by default, or millimeters. It follows the browser across all tabs on the shared
Plotline address and survives reloads. The old PDF-only preference migrates when
present. Paper sizes, measured drawable areas, margins, offsets, hatch spacing,
feather/edge distances and on-paper area thresholds use that preference. Pen
widths remain in millimeters; travel totals, geographic distances and technical
coordinate scales retain their own units.

`shared/web/paper-units.js` owns preference storage/events, formatting and numeric
field conversion. Use `paperSize`/`paperLength` for feedback. Mark numeric paper
fields with `data-paper-length` (and `data-paper-power="2"` for area), declare their
bounds/defaults in canonical mm, then call `bindPaperUnits`. Read/write them with
`readPaperField`/`writePaperField`, never by directly converting displayed strings.
Cached canonical values preserve measured precision across unit toggles and edits
to other fields. Unit selectors use `data-paper-unit-selector`; labels use
`data-paper-unit`. `onPaperUnitChange` repaints other summaries without changing
recipes, saving setups or requesting conversions. The shared module is mirrored
on Map Studio's branch; API/recipe/geometry units remain mm.

Validation: `node controller/scripts/paper-units-smoke.mjs` (also supports
`PLOTLINE_BROWSER=firefox`), plus the cross-Studio navigation, PDF, HPGL, paper-check,
calibration and Map paper browser checks. These use isolated fixtures, not hardware.

Maze Studio is served at `/mazes`; its documents, uploaded masks and immutable
exports live under the durable state `mazes/` directory. See
[the Maze Studio guide](../docs/maze-studio.md). Preparing a maze registers an
HPGL draft through the existing PDF/intent handoff; it never starts plotting.
