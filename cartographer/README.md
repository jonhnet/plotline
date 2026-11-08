# Plotline Map Studio

Private development instance: **http://dev.iac.jonh.net:9193/**.

The studio uses one pipeline: geographic viewport → cached OSM vector tiles →
shared feature styles → optional boundary overrides → optional terrain visibility
→ page composition → vector PDF and validated print intent. Neighborhood and
Waterways are style starting points; applying one keeps the location, paper,
camera and boundary. Azimuth rotates both flat and relief maps.

New maps enable Title, Compass Rose and Source Credits. Existing documents keep
their saved choices, and all three can be turned off in the map controls.

## Paper and margins

**Paper**, **Orientation**, and **Margins** are independent controls. Paper lists
US Letter/Legal/Tabloid, ANSI C–E, ARCH A–E plus E1, and ISO A4–A0; dimensions
follow [HP's paper-size reference](https://support.hp.com/us-en/document/bpp01676).
ARCH E's 48-inch side is now accepted by recipe validation.

New documents use HP7585 margins. The UI prefers matching Plotline Studios capability
profiles and has recorded/nominal snapshots for both orientations when offline.
Orientation has four choices: portrait with the wide margin at top or bottom,
and landscape with it at left or right. Switching paper or orientation preserves
geographic center, scale, page offset, camera, and feature choices. HP7585's unequal margins follow physical paper feed
and carriage directions: 11×17 portrait has margins left/top/right/bottom of
14.8/39.425/11/15.425 mm, plus the existing 1 mm pen clearance. Its map viewport
is 251.6×374.95 mm. Turning it over puts that wide margin at the bottom.
Portrait handoff uses a portrait source PDF and target sheet,
unit scale, zero PDF-placement rotation and no centering; Plotline Studios registers
that sheet to the device axes using the saved `orientation_deg`. A half-turn
rotates both the printable rectangle and any calibrated transform; it does not
mirror the artwork or rotate the map camera. HPGL Studio keeps the artwork
upright while showing the selected margin orientation. The numeric margin
summary is omitted; custom margin fields remain available when selected.

Saved documents keep their full media snapshots. The old “authoring margins”
profiles appear as **Custom** and stay unchanged until edited. Select **From
plotter** to adopt the corresponding HP7585 profile. **Refresh margins from
plotter** explicitly updates the active document's snapshot; refreshing carousel
inventory only refreshes available choices. Failed/offline refresh leaves saved
margins intact. Custom top/right/bottom/left values stay relative to the page,
retain inward calibrated placement when applicable, and report when they extend
beyond the plotter's printable area. Impossible margins are rejected before
replacing the last valid document state. No saved recipe schema change is needed.

Validation includes all 68 paper/orientation combinations, unchanged old recipes,
portrait PDF/intent placement, browser save/reload, refresh/offline behavior,
custom margin rollback, and narrow-screen layout:

```sh
build/cartographer-lite-venv/bin/python -m unittest discover -s tests -p test_cartographer_paper.py
build/cartographer-lite-venv/bin/python tools/check_cartographer_paper.py
build/cartographer-lite-venv/bin/python tools/check_cartographer_orientation.py --driver-root /root/plotline
```

The 2026-11-03 delivery passes 29 existing cartographer tests, four paper tests,
and the isolated browser check. An isolated Plotline Studios conversion/export confirms
the portrait registration `[0,40,40,0,-9116,-5664]`, physical sheet size
431.8×279.4 mm, and a valid prepared HPGL file. The actual Paine Field recipe
`9cfa68b2784d4a52b472b26efbd6a274` renders with plotter margins in the live browser
without changing its saved revision 4, camera, center, scale, or inks. Evidence
is in the `map-paper` worktree's ignored `build/portrait-handoff/`,
`build/live-paper-result.json`, and browser screenshots. No physical plot was sent.

The four-orientation follow-up passes 50 controller tests, five paper tests,
seven shared-contract tests, the 68-case Map Studio browser check, and isolated
PDF conversion/prepared-HPGL/browser checks at 0°, 90°, 180°, and 270°. Every
registered coordinate is checked against an independent transform, and the HPGL
browser's paper/window outlines put the wide margin on the selected edge. The
live Paine Field preview was checked with its wide margin at the bottom and no
numeric margin paragraph; saved revision 4 remains untouched. Evidence is in
`build/four-orientations-8qkwakdl/` and `build/live-bottom-margin-result.json` in
the `map-paper` worktree. Controller support is committed as `ecabc30`.

## Area textures

Under **Features & styling**, **Water treatment → Concentric outlines** fills
water with successive shoreline offsets. **Park treatment → Concentric
outlines** applies the same treatment to parks and woodland. Each has its own
spacing in millimeters at ground scale; Crosshatch remains available, and
water also retains No fill and Opaque fill. Existing saved water-fill choices
migrate without changing the stored revision. Inside/outside styles remain
independent, and their Water/Parks ink roles own every resulting stroke.

Rings are generated in ground coordinates and then draped, occluded and clipped
through the existing pipeline. They follow islands and split naturally as narrow
channels close. No connecting strokes cross the gaps between rings. Fetch/page
edges do not invent a shoreline; where an entire fetched area is open water,
there is no shoreline to echo. These are decorative offsets, not bathymetric
depth contours. Shoreline/echo choices remain separate from the area treatment;
echoes coinciding with fill rings are omitted to avoid drawing them twice.

The PDF contains explicit vector strokes, so Plotline Studios does not need a new
fill policy. Texture changes use the existing per-layer cache; changing water
spacing rebuilds Water fill (and coincident-echo suppression, when enabled).
Plotline Studios now provides [pen width and color calibration](../docs/pen-width-calibration.md)
at `/calibration`. After reviewing and applying scan measurements there, use
**Refresh carousel** here to make those inks available. Existing map inks keep
their saved colors and widths until you explicitly choose the measured pens.

```sh
build/cartographer-lite-venv/bin/python -m unittest discover -s tests -p test_cartographer_area_marks.py
build/cartographer-lite-venv/bin/python tools/check_cartographer_area_marks.py
```

## Shared studio navigation

The canonical dev address is **http://dev.iac.jonh.net:9191/map**, reached through
Plotline's controller proxy. The header switcher reuses PDF/HPGL studio tabs;
**Edit source** opens editable source documents or exact source versions separately.
The older port 9193 remains usable and offers **Move open documents** to copy its
browser workspace, including unsaved drafts, to the shared address. It preserves
the original workspace and existing shared-origin drafts. See
[studio navigation](../docs/studio-navigation.md) for routing, ancestry and tests.

## Run

Map Studio and the PDF converter can share a fresh environment. Removing the
unused layout/ML dependencies removes the previous Protobuf version conflict;
the tile decoder retains Protobuf 6. Do not install Lite over an existing full
PyMuPDF installation: that leaves overlapping packages and the unwanted ML
dependencies. Build/test a new environment and switch it when the Studios are
idle. Lite keeps the PDF API and MuPDF engine without the layout-model runtime.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-map.txt -r requirements-contracts.txt -r requirements-cartographer.txt
.venv/bin/python tools/build_cartographer_geometry.py
.venv/bin/python -m cartographer.service --host 0.0.0.0 --port 9193
```

The optional terrain visibility extension requires a current Rust toolchain
(tested with Rust 1.94). The build script installs it into the invoking venv and
links to the pinned Shapely wheel's GEOS 3.13.1, with no second geometry engine.
Rebuild after changing Shapely, the Rust sources or the venv. Without the
extension, Map Studio uses the equivalent Python/GEOS implementation.
`PLOTLINE_MAP_GEOMETRY=python` or `rust` explicitly selects a backend; `rust`
fails if the extension is unavailable. Native work defaults to four threads;
`PLOTLINE_MAP_GEOMETRY_THREADS=1` permits serial measurements (valid range 1–16).
The cache fingerprint includes native source and the installed binary.

Python 3.12, DejaVu Sans/Serif (`fonts-dejavu-core`), PyMuPDF Lite 2.0, GEOS/Shapely,
NumPy, Pillow and the established `mapbox-vector-tile` decoder are used. The UI
is local HTML/CSS/JavaScript with no CDN dependency. Uncached tiles and boundary
search require network access; no provider key is required.

All three studios now live in the same source tree. The dev host's persistent
`plotline-cartographer` unit runs from `/root/plotline`, using the shared `.venv`
(a link to `build/studios-venv`). It retains the original map library and cache
via explicit arguments:

```sh
/root/plotline/.venv/bin/python -m cartographer.service \
  --state /root/plotline/worktrees/artistic-cartographer/state/cartographer \
  --build /root/plotline/worktrees/artistic-cartographer/build/cartographer
```

That command documents the installed unit; do not launch a second process on its
port while it is running. The old worktree now supplies data paths only, not
application code. New installations can use the default paths in their checkout.

When map work is idle, `systemctl restart plotline-cartographer` reloads Python
changes and the composer fingerprint. Inspect it with
`journalctl -u plotline-cartographer --no-pager -n 30`. The unit is enabled across
reboots; this remains a development installation. The map worker and Studios
server can be restarted independently when their work is idle.

Options: `--state PATH`, `--build PATH`, `--studios URL` (server-side default
`http://127.0.0.1:9191`), `--studios-public URL` (default
`http://dev.iac.jonh.net:9191`). This private dev HTTP server requires same-origin
JSON writes and limits bodies to 8 MB; it does not provide public hosting or
production authentication.

## Authoring

- **Map furniture** controls the optional title, compass rose, scale/camera
  parameters, legend, source credits and Plotline signature. New maps enable
  title, compass rose and source credits;
  **Hide all furniture** clears them together. The name still identifies a
  saved recipe and PDF when its printed title is hidden. Existing recipes keep
  their saved choices, including the older furniture-off default.
  The viewport fills the complete usable plotting rectangle, retaining the
  media profile's existing safety inset. Furniture never reserves margins or
  changes map scale. Enabled titles have a 1.5 mm white glyph halo; the eight-point
  compass rose and swashed N have a white silhouette with the same clearance.
  The browser sketch and PDF use this page-space margin. The longer
  north point follows projected north in both 2D and relief. Other enabled
  annotations also overlay the map with white contrast paint. These remain
  ordinary vector PDF marks; white clears underlying ink without adding a pen.
  Geographic labels avoid enabled furniture so they do not collide with the
  title or compass; map linework still extends beneath the overlays.
- Drag to pan and scroll to zoom around the pointer. Coordinates are unrestricted
  by Seattle snapshots. Web Mercator excludes the polar caps; an oversized
  viewport is rejected with an explanation. Enter longitude/latitude directly,
  or use a boundary search result's **Go to** button. Go to fetches the place's
  full extent and fits it to the usable page with the current paper, margins and
  camera, updating scale and offset together. It uses automatic viewport coverage
  and leaves the styling boundary unchanged; **Select** still controls that.
  The move is one undoable edit. Failed or obsolete lookups leave the view intact.
- **Fill viewport automatically** fetches the inverse projected page rectangle,
  with a symbol halo and an elevation-dependent allowance for oblique views.
  **Clip to geographic bounds** keeps an explicit geographic crop. Camera and
  paper changes preserve the requested scale; **Fit boundary or bounds** is the
  explicit reframing operation. Scale is local to the map center, not a claim
  of uniform ground scale across a state-sized Mercator view.
- Choose the same roads, buildings, park treatment, shore echoes, labels,
  contours and ink assignments in every view. The initial palette has red,
  green and black; add blue or change an ink's color for water emphasis.
  Crosshatching uses continuous diagonal lines clipped to the park geometry;
  the hatch-spacing control sets their density.
  Contours paint above park/forest backgrounds, hatching and outlines, so land
  cover cannot erase them. Buildings, roads and cleared label backgrounds paint
  above contours in both flat and relief views.
- White-centered roads use two explicit edge strokes sized to their assigned
  document pen, including outside-boundary and bridge-role overrides. Road class
  controls the white center width. Each edge includes 0.06 mm of clearance for
  flattening and the HPGL grid; solid paths use at least the assigned diameter.
  Offsets happen after projection, retaining constant page width on terrain.
  White cores still clear intersections, and the original visibility/viewport
  clips remain hard limits. A changed road pen rebuilds road paint. This avoids
  the former fixed 0.141 mm casings disappearing with wider pens, and avoids
  asking the PDF converter to contour-trace both sides of each narrow edge.
  The road acceptance check converts straight, diagonal and bent bridge edges
  with 0.3/0.5/0.7 mm pens, then independently decodes HPGL to check continuity,
  white centers, viewport containment and single-pass mileage. On the Ravenna
  Park regression (recipe `4c100b2c…`), road mileage recovers from 2.82 m to
  20.46 m and bridge mileage from 0.03 m to 1.43 m. All decoded footprints stay
  within their allowed paint. Narrow fragments left by later paint or clipping
  can still be omitted: absent road objects total 0.66% of visible road area.
- Boundary search uses actual US Census city, county and state polygons.
  Selecting a boundary does not move the view. With a boundary selected, the
  style editor switches between independent inside/outside instances. The
  default outside instance is sparse and black. A GeoJSON Polygon,
  MultiPolygon, Feature, or single-feature FeatureCollection can supply another
  boundary. The named geometry and checksum are embedded in the recipe.
- Relief and contours are separate switches. Flat maps skip hidden-surface
  processing; contours can still use the elevation source. Relief uses an
  orthographic bare-earth heightfield. Bridge-tagged roads use inferred deck
  grades between their approaches instead of following valley floors. Connected
  spans share grades; intervening ground can raise a deck, and foreground hills
  still hide it. These are terrain-based estimates, not surveyed elevations or
  structural models. Building volumes are not modeled. See
  [bridge decks](../docs/cartographer-bridge-decks.md) for the method and limits.
  Lines have constant page widths, and visible centerlines join before stroking.
- Drag pans the preview; wheel/pinch zooms it around the pointer. These gestures
  and **Fit page** never change the recipe or request
  map rendering. Preview magnification is separate from the printed map scale.
  **Edit map** enables composition gestures; holding **Shift** temporarily does
  the same. Drag then performs the selected **Map edit action** (map center,
  position on paper, or camera orbit), and wheel/pinch changes geographic scale.
  The orange outline and editing label distinguish editing from inspection.
  Each gesture transforms the existing image provisionally, then requests one
  redraw on release (or after a wheel burst). **Undo/Redo**, including
  Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z, reverse complete edits; Escape cancels an
  unfinished gesture and exits Edit map. Undo history lasts for the open tab.
  Ctrl/trackpad pinch remains preview inspection unless Edit map is selected.
- Cached geometry responds immediately to composition changes. Each debounced job
  publishes fetched geometry before the resolved PDF preview. Relief publishes
  an updated sketch when finer terrain sampling finishes, before occlusion and
  drawing finish. The sketch omits
  labels, white paint, boundary style clipping and terrain occlusion; the
  **Rendering details** tray lists what is pending. The resolved preview includes
  those operations.
- One fixed-height progress strip serves preview drawing, optional PDF generation,
  and plot preparation: a bar, the current operation, and elapsed time. Completion,
  errors and retries do not resize the paper. The estimate never retreats within
  a request; the last 1% of preview work waits for the image to be visible.
  Detailed counts and diagnostics live in the Rendering details tray. **Retry**
  appears after a preview failure. Editing or leaving the page cancels obsolete
  previews automatically; there is no separate Stop control. Slow sketch transfers
  do not pause status polling. Errors remain visible without opening a tray.
  Restoring a document checks the saved print receipt's actual phase before
  suppressing a draft. Failed or completed preparations cannot cancel preview
  startup; active preparations resume monitoring without duplicating a render.
  Cached previews include the composer version and refresh after renderer
  changes. A restored image that fails or times out falls back to a new draft.
  Old prepared-plot images cannot replace a newly rendered authoring preview.
  Relief reports mesh facets, hatch generation, projected features and drawn
  lines as separate operations. Major phase changes publish immediately; inner
  descriptions and counts coalesce to four updates per second. A trailing publisher
  sends the latest operation even while the worker is in a long GEOS call. Progress
  uses atomic file replacement without disk synchronization; saved recipes and
  print receipts retain their durable writes.
- A draft uses simplified vectors; a proof uses finer vectors and terrain.
  Relief detail follows the visible page, independently of the larger fetch
  extent needed by an oblique camera. Changing the composition cancels obsolete work and
  invalidates export links. Only a current proof can be sent to Plotline Studios.

## Incremental composition

A completed export still has a key for the complete recipe, quality and composer
version. Beneath it, independent build nodes reuse work by the settings and
upstream content that affect it. The persisted artifacts are under
`build/cartographer/geometry/nodes/`; restarting the service retains this reuse.

| Edit | Work invalidated |
| --- | --- |
| Forest labels or label density | Label placement, final PDF/preview/bundle; map paint is reused |
| Title or furniture | Label collision/placement and final output; map paint is reused |
| Forest hatch spacing/treatment | Forest paint |
| Water texture/spacing | Water paint, plus shore echoes when their spacing depends on it; roads are reused |
| Sun direction/elevation | Slope illumination and hillshade paint |
| Contour interval | Contour paint |
| Feature ink assignment/color, with unchanged effective stroke widths | Final PDF colors/layer bindings, print intent, preview/bundle; cached paths are reused |
| Road/bridge ink diameter | Road casing geometry and paint; other layers are reused |
| Other ink diameters or inventory provenance | Print intent/bundle; authored map stroke geometry stays the same |
| Camera, extent, paper frame, scale or quality | Affected terrain, projection, clips and paint |
| Changed source geometry | Stages consuming that geometry |

Nodes cover clipped areas, water geometry, terrain depth audit, projected boundary,
illumination, visible mountain edges, and separate forest/hillshade/contour/
silhouette/building/road/water paint. Keys include upstream geometry hashes,
projection and mask identities, and relevant geometry settings/widths. Code/runtime
changes conservatively invalidate nodes through the existing composer version.
Draft and proof artifacts remain distinct.

Paint artifacts contain balanced, compressed vector path templates and explicit
feature-layer bindings. Ink colors and logical ink IDs are bound at replay from
the current feature role, independently for inside/outside styles. Only semantic
ink operators are deferred; literal white clearing stays white. Replay relocates
layer resources into the new document; no
stale PDF object numbers or fonts are copied. Original paint order, white
occlusion, finite-width clips and physical page placement are preserved. Labels
run fresh against the current furniture and shared label-collision state.
Publication uses a per-node process lock and atomic rename: cancelled/failed
computations never become reusable artifacts.

Progress reports “Building” or “Reusing” each stage. Composition notes summarize
reused/rebuilt stages; `provenance.json` and the job result retain node keys,
hit/miss, current load/build time and the original build duration. These counts
refer to the new drawing/geometry nodes, not every tile or final-export cache.
Changing a recipe never reuses the old print intent or bypasses revision guards.

Measured on the recent Cabin & Snoqualmie Pass draft: the earlier label-only
change took 53.1 s, including 47.2 s drawing. With a populated node cache, the
same edit took 6.9 s, reusing all ten nodes. Forest paint alone took 44.7 s to
build and was skipped. The first render still pays those costs (65.4 s in this
run, including a new terrain build); final PDF writing, preview rasterization
and packaging remain. Evidence: `build/incremental-review/timing.log`.

The 2026-11-05 Seattle edit changed concentric water from black to a new blue
ink. Previously, this unnecessarily rebuilt Water fill and Shore paint: 15.75 s,
eight stages reused and two rebuilt. With deferred ink binding it takes 3.35 s,
all ten stages reused (4.7× faster). Cold builds were 25.63 s before and 25.51 s
after. The final PDF, PNG preview and print intent are byte-identical to the
previous implementation. These are complete offline draft compositions,
including labels, PDF writing, rasterization and packaging, not just node times.

Frozen before/after bundles are retained under
`state/cartographer/benchmarks/incremental-color-2026-11-05/` in the serving
artistic-cartographer worktree. To reproduce, run `tools/benchmark_cartographer.py`
on `before.zip`, then `after.zip`, sharing a private `--cache` and using distinct
output directories. Compare commit `b99c391` with this change using separate
caches. Measurements and artifacts are in the map-incremental-color worktree's
`build/incremental-color/{baseline,late-ink}/{before,after}/`.

Regression checks compare cached and clean PDFs byte for byte and independently
decode colors/OCG bindings. They cover color edits, new ink identities sharing a
swatch, white road cores/backgrounds, boundary styles, terrain/hillshade,
water-spacing isolation, and road-width invalidation. The real browser edit
check accepts an optional frozen `recipe.json` as its second argument after the
service URL; use an isolated service/store with `--studios-public` set to that
test origin so the old-address migration banner does not cover its controls.
All 133 cartographer tests pass; the real-browser water-ink reassignment takes
3.6 s and reuses all ten stages, with the new logical ink in the exported intent.

## Sources, detail budgets and reproducibility

[OpenFreeMap](https://openfreemap.org/) serves OSM-derived tiles in the
[OpenMapTiles schema](https://openmaptiles.org/schema/). A recipe pins the
provider's dated tile template. Raw responses are cached by URL, written
atomically under a per-key lock, and recorded with SHA-256 hashes. Across this
checkout, at most four source requests run concurrently. A composition uses at
most 48 vector tiles for draft or 96 for proof and at most 50 MB of vector data.
The provider TileJSON is cached when first discovered; existing recipes keep
that revision. A retired provider revision requires the downloaded bundle or
cached input bytes; the app does not silently substitute a newer source.

Tile zoom follows physical scale and the tile budget. OSM source tiles are
already generalized; a proof is full detail at its selected source zoom, not
the complete OSM database at every scale. Automatic buildings appear at zoom
14; at wider extents the app reports their omission. **All available** buildings
requires zoom 14 for proof and refuses an extent exceeding the budget. Drafts
may lower detail with a notice. Automatic draft detail also steps down when
a zoom-14 view spans more than four tiles; neighborhood-scale drafts retain
buildings and proof detail is unchanged. Feature counts are also bounded (18,000 draft,
65,000 proof). Requests for more detail can be refused rather than hang the UI.

Clipped tile pieces are joined/dissolved before deriving symbols. A 0.001 m
coordinate grid welds numerical tile seams. Historical protected districts
are not interpreted as parks. Selected features retain IDs/categories in PDF
OCGs, independently of document ink color and pen diameter.
Area geometry is validated again after simplification and rotation into hatch
coordinates: valid pieces can overlap in an invalid MultiPolygon, and affine
roundoff can make a narrow hole cross itself. Forest and hillshade hatching
share the same repair before clipping rows or stripes. Repairs preserve filled
areas and dissolve overlaps, retaining real holes; `provenance.json` records
repairs made after simplification. Invalid components use GEOS make-valid, rather than
silently omitting a forest or discarding one lobe of a crossing polygon.

[Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) provides global
Terrarium elevation tiles. The decoder uses the published RGB height encoding
and bilinear pixel-center sampling across tile edges. Coverage/resolution
varies by source; missing requests fail explicitly. Heights and horizontal
coordinates share the same latitude scale correction. Each resolved relief
runs the existing independent frontmost-depth audit and checks that an automatic
viewport is filled by the mesh. Extremely low/exaggerated views can be refused
if their required footprint cannot be established within bounded retries.
The 33×33 draft / 65×65 proof grids establish coverage and the lightweight
interactive sketch; they determine neither flat contour nor relief detail.
Flat contours sample their own regular grid from finer source tiles, targeting
0.5 mm spacing in drafts and 0.2 mm in proofs at the printed scale. Budgets cap
these grids at 2 / 8 million samples, 4,096 / 8,192 samples per axis and 128 / 512
source tiles, with tile zoom capped at 14. Reached limits increase the spacing;
provenance records the actual spacing, source pixel size, grid and budget status.
ContourPy joins regular-grid isolines without smoothing invented terrain into
the old coarse grid. The contour elevation node is cached independently of
inks and contour intervals; its fetched tiles are included in the source bundle.
See [the Washington flat-contour regression](../docs/map-studio-flat-contours.md).

For relief, a stitched quadtree refines potential page
coverage, prioritizing exposed slopes. Elevation tile zoom follows paper scale
(target source pixels at most 1 mm in drafts, 0.4 mm in proofs, capped at zoom 14).
Refinement targets sampled vertical projection error of 0.8 / 0.25 mm and edges
of 6 / 3 mm, within 4,000 / 12,000 leaf cells and 256 / 768 detail tiles.
These are bounded refinement targets, not guaranteed global error bounds:
source resolution, coarse visibility estimates and budgets can limit detail.
The UI reports a reached mesh budget; provenance records mesh/source spacing,
sampled residual and all elevation inputs from every coverage and detail pass.
Shared edge vertices prevent cracks between coarse and fine cells. Contours,
feature draping and occlusion use the same refined surface.
The full fetched mesh participates in occlusion; decoration work uses only
visible facets intersecting the paper plus a 4 mm halo. Original park outlines
are cropped as lines, so this optimization does not invent park boundaries.
Centerline projection is separate from stroke masks: the studio builds masks
for individual visible runs, avoiding a discarded union of the entire hatch.
See [the measured relief performance investigation](../docs/cartographer-performance.md).
The [2026-11-02 Draft/Preparing baseline](../docs/map-studio-performance-01.md)
freezes the latest Cabin & Snoqualmie Pass map, separates rebuilds from cache
reuse, and records Python/library profiles before the next optimization round.
The [progress/runtime follow-up](../docs/map-studio-performance-02.md) records
the coarser transient reporting, PyMuPDF Lite migration and unchanged output checks.
The [geometry/Rust follow-up](../docs/map-studio-performance-03.md) records
visibility shortcuts, a bounded GEOS worker pool, shading culling and comparisons
against the same frozen map.
The [geometry-work reduction follow-up](../docs/map-studio-performance-04.md)
records local hatch strips, mesh boundary assembly, boundary-contact culling
and the faster depth check of every visible terrain face.
The [large Washington-sheet profile](../docs/map-studio-performance-05.md)
extends strip clipping to forest/water crosshatches and preserves continuous
strokes at crossings, reducing its full-detail rebuild from 203 to 132 seconds
with the same detail settings and validated ink coverage.

[US Census TIGERweb](https://www.census.gov/data/developers/data-sets/TIGERweb-map-service.html)
supplies the boundary snapshots. These are different boundaries from the frozen
Seattle council-district union used by the earlier regional proofs. Source URLs
and hashes travel with the recipe/provenance. OSM/OpenMapTiles attribution and
elevation attribution remain in PDF metadata and provenance when on-page
source credits are hidden.

Downloads include `map.pdf`, `recipe.json`, `provenance.json`, a hash-bound
`map.print-intent.json` when all ink diameters are known, and `map-bundle.zip`.
The bundle also contains the exact source tile bytes under `sources/` so that
rebuilding does not depend on a provider retaining old URLs:

```sh
build/cartographer-lite-venv/bin/python -m cartographer.replay \
  map-bundle.zip build/replayed-map
```

Replay verifies every source hash and refuses any missing input without network
fallback. Exact output bytes require the same composer/dependency versions;
provenance records the composer fingerprint and effective source selection.

## Recipes, inks and print handoff

Version 2 recipes store center, fetch extent, paper, independent camera,
inside/outside styles, optional boundary snapshot, palette and source template.
An optional `furniture` object records the six overlay choices; earlier version
2 recipes without it remain readable and use the same furniture-free defaults.
Version 1 recipes and browser drafts migrate on read; saved historical revisions
are not rewritten. Their former preset-specific frozen extent becomes an
automatically filled viewport. Preset selection is removed from rendering.

The compact header combines branding, document tabs and file actions; the active
tab supplies the document name and its `*` marks unsaved edits. Preview tools float
over the paper. **Show printable margins** in Paper & camera shades the area
outside the plotter's drawable rectangle; it is off by default.

The UI treats these recipes as named documents. **Save** updates the open document
in place; **Save As** creates a separate document, with an unused name such as
“Map (2).” Document names are separate from the optional printed map title.
Each save retains a durable revision with optimistic conflict detection. A second
browser window cannot silently overwrite a newer save; its edits remain available
for Save As. The underlying JSON recipe stays portable and unchanged by naming.
**Revert**, beside Save, restores this tab's last saved recipe and fits the page
without closing it or creating a saved revision. It is available when a saved
document has unsaved edits; **Undo** can recover those edits after reverting.
For preview-only zoom and pan, use **Fit page**.

**Open** shows recent documents with thumbnails and a name filter. Opening a map,
importing JSON or clicking **+** creates a workspace tab; selecting an already-open
document switches to its existing tab, preserving unsaved changes. A `*` marks
unsaved edits. Closing a dirty tab offers Save / Don’t save / Cancel; leaving the
browser warns about unsaved work. Ctrl/Cmd+S saves, Shift+Ctrl/Cmd+S saves a copy,
and Ctrl/Cmd+O opens the library.

Workspace tabs, edits, preview URLs and per-document preparation receipts survive
reload in browser session storage, with a local-storage recovery copy. The saved
document URL no longer overrides a newer local draft. Switching tabs reuses a
matching preview; stale preview/save responses cannot replace another document.
Imported recipes are validated before activation.

Thumbnails are bounded JPEGs (at most 384×288), saved under the document's durable
revision directory, separately from disposable render caches. Saving uses the
current preview or sketch immediately; if no preview exists yet, the library
shows a placeholder and attaches the completed preview to that exact revision.
A queued draft supplies missing thumbnails even after its browser tab closes;
it uses a separate preview session, reuses the composition cache, and resumes
after service restart. It yields to a thumbnail arriving from the browser.
Older saves gain thumbnails when opened and rendered. Thumbnail updates verify
the revision's recipe hash and never overwrite another revision's image.
`POST /api/recipes` accepts optional `name` and a small PNG/JPEG data-URL
`thumbnail`; lists return `thumbnail_url`. GET/POST
`/api/recipes/{id}/thumbnail` retrieve/update a revision's thumbnail.

Inks remain independent of carousel slots: copying inventory retains
its source snapshot, and an unknown width remains unknown. Multiple inks may
share RGB at different diameters. Unknown widths allow a source PDF but prevent
a conversion intent and submission.

PDF placement stays at the physical page origin, scale 1, rotation 0, offset 0.
White paint clears paper without requesting a white pen. Boundary masks apply
to the finite painted footprint after projection. The existing PDF, ink and
paper contract is unchanged. The primary **Prepare plot →** action saves the
current design, generates full map detail if needed, uploads the exact PDF and
complete intent, awaits inspection, requests `/draft`, follows conversion and
makes the resulting HPGL Studio preview available through **Open plot ↗**.
Preparation completes in MapStudio; Open plot and other studio links use a new
browser tab so the map workspace stays intact. There is no required PDF Studio
stop. Load the planned sheet and matching pens there, then click Plot.

The **Downloads** icon beside the source attribution and Prepare plot opens a tray
for PDF generation, PDF/bundle downloads, extra prepared pages and conversion
overrides. The adjacent **Rendering details** icon opens warnings, omitted labels,
feature counts and cache diagnostics. Trays overlay the preview without resizing
it. Offline operation still supports PDF generation.
Unknown ink diameters block plot preparation before expensive rendering. Existing
prepared plots reopen directly; editing the map invalidates their visible links.
The service never changes physical inventory or calls a physical Plot endpoint.

A draft already uses a vector PDF internally. Full-detail preparation computes
finer geometry and a larger terrain mesh. On Jon's hillshade example, 66.8 s total
included 29.6 s terrain visibility, 34.4 s drawing and 1.4 s PDF writing. Combining
the actions does not lower this quality or remove those costs.

`POST /api/prepare` accepts a recipe and optional explicit `retry`; it returns a
persisted preparation keyed by the complete recipe hash. Repeated requests and
lost responses reuse that task. `GET /api/preparations/{id}` combines rendering
status with the downstream print receipt, including Plotline Studios's real phase,
counts and page progress. The shared progress strip says **Preparing plot** plus
the current operation, then **Plot ready**, with a monotone work estimate rather
than a timing prediction. Background preview restoration cannot overwrite this
preparation status. The saved recipe
continues independently of ordinary preview cancellation, browser reload or later
edits. Each document retains its own preparation pointer and result links.

Preparations reuse previous exact-design submissions, including old two-step
receipts and completed work across renderer upgrades. Known failures retry the
failed stage with the same PDF/intent; a conversion retry does not render the map
again. Fresh edits create a distinct preparation. Older successful drafts and
newer choices made in PDF Studio retain their existing protection.
An explicit `rebuild: true` instead regenerates the source with the current
composer and updates the same preparation pointer. It leaves previous exports,
PDF documents and HPGL jobs intact. Repeated requests during an active preparation
reuse that work. Use this after a rendering correction; ordinary reopening and
conversion retries continue to reuse the original source.

See [Printing workflows and the pen-aware service boundary](../docs/printing-workflows.md)
for the two-service model, existing HTTP contract and proposed contract additions.

Both live `pdf_upload` and `pdf_handoff` flags are required. The latter is a
driver extension not yet listed in the original strict capabilities schema;
the client validates it separately while preserving validation of the v1 body.
Returned links/status URLs must belong to the configured internal/public
Plotline Studios origins, and redirects are rejected. Only the backend contacts the
driver API; browser links use the public origin.

Receipts pin source hash, complete intent, export/recipe hashes, document and
conversion IDs, phase, retry history and prior draft links. Old upload-only
receipts resume at inspection/draft creation, including after a composer cache
key changes when the old export's PDF hash and complete intent still match.
There is no blind retry of an ambiguously acknowledged POST: a lost draft
response can be reconciled against the selected conversion's exact authored
settings; an unknown upload directs the user to PDF settings. Known HTTP
rejections and conversion failures remain explicit. Document ink IDs, colors,
diameters and inventory provenance survive conversion unchanged even when the
carousel changes. Physical pen matching happens separately in Plotline Studios.
Earlier driver versions incorrectly rejected historical carousel snapshots;
existing failed receipts now offer **Retry preparation**, including after a
browser reload. `POST /api/print-tasks/{export-key}/retry` checks the receipt's
recipe hash and retries its uploaded document and saved intent without rendering
another PDF or stripping provenance. It preserves ambiguous-request recovery and
newer operator choices. Reading an old failure does not rewrite its receipt.
Actionable errors omit ANSI terminal escapes and Python tracebacks; full
diagnostics remain in the receipt and retry history. See the
[document-ink provenance correction](../docs/handoffs/document-ink-provenance.md).

The integration instructions and known hatch-fragmentation performance work are
in [the Print-button handoff](../docs/handoffs/cartographer-print-button.md).
This change wires the print workflow; it does not change hatch geometry or the
driver's visibility algorithm. Large map conversions can still take minutes.

## Terrain artwork

**Park & forest labels** controls woodland names independently of park paint and
other map labels. Turning it off retains the hatching. The general Labels choice
still controls the overall label density; None suppresses all geographic labels.

**Mountain silhouettes · 3D** draws the visible edges where a camera-facing slope
folds out of sight. It uses the existing terrain visibility masks, suppresses
ridges hidden by nearer mountains, and omits artificial fetch-area borders.
Joined centerlines retain constant page width; there are no triangle wireframes
or facet-clipped stroke widths. Silhouettes are inactive in 2D.

**Hillshade crosshatching** works independently of forests, contours and 3D.
Sun direction is clockwise from north (0° north, 90° east, 180° south); elevation
is above the horizon. Defaults are south at 45°, an artistic light rather than
an astronomical time/location calculation. The sun is shared across boundary
regions; enablement and hatch spacing belong to each region's style.

Illumination uses area-weighted vertex normals on the sampled surface. Two
interpolated darkness bands put one diagonal hatch direction on dim slopes and
a crossing direction on darker slopes. Thresholds are relative to level-ground
illumination, leaving flat ground clear. In 3D the lighting follows the displayed
height exaggeration and marks are draped and occluded by the same terrain mesh.
In 2D, lighting uses unexaggerated elevations and skips terrain visibility work.
This is slope shading, without cast shadows from neighboring mountains. Hatch
spacing is measured on the ground at the map's scale, like the forest treatment.
Rows retain their identity at crossings to avoid a fragmented crosshatch network.

Silhouettes and hillshade each have an independent **Feature inks** choice,
initially using an existing black ink (or the label ink if no black is present).
Existing contour ink assignments remain unchanged. All marks stay vector, carry
feature/ink bindings in the PDF intent, and are clipped to the map viewport and
inside/outside styling masks. Road centers, buildings and labels clear later.

Old recipes acquire these controls on read, without changing saved revisions.
New terrain effects default to off; forest labels retain their previous default.

## Validation and preserved proofs

```sh
build/cartographer-lite-venv/bin/python -m unittest discover -s tests -v
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer.cjs
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_progress.cjs http://127.0.0.1:9193
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_furniture.cjs http://127.0.0.1:9193
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_print.cjs http://127.0.0.1:9193
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_restore.cjs http://127.0.0.1:9193
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_terrain_marks.cjs http://127.0.0.1:9193
NODE_PATH=/root/plotline/controller/node_modules node tools/check_cartographer_cache.cjs http://127.0.0.1:9193
# Separate current driver checkout, with its documented native environment/binaries:
build/cartographer-lite-venv/bin/python tools/check_cartographer_print.py --driver-root /root/plotline
build/cartographer-lite-venv/bin/python tools/check_cartographer_roads.py --driver-root /root/plotline
# Isolated document store with controlled preview responses (no external sources):
build/cartographer-lite-venv/bin/python tools/check_cartographer_documents.py
NODE_PATH=/root/plotline/controller/node_modules build/cartographer-lite-venv/bin/python tools/check_cartographer_navigation.py
```

Unit tests cover camera/viewport inversion, global navigation, adaptive detail,
MVT seam dissolution, elevation sampling, boundary finite-width color clipping,
white cores, hollow buildings, vector-only PDFs, ink identities, version-one
migration, revision conflicts, cancellation and the print API. They run without
provider access. The browser acceptance uses live sources and an isolated state
folder; it covers Portland navigation/buildings/2D rotation and a combined blue
waterways + Washington boundary + relief composition, exports, save/reopen,
state-scale detail refusal and mobile layout. It saves screenshots and results
under a unique `build/cartographer-browser-*` directory. The separate progress
check uses controlled slow/failing responses in the browser against a running
studio; it verifies continued status polling during sketch transfers, real
counts, automatic cancellation on edits, Retry, failure cleanup, image-load recovery, coarse-to-refined
terrain sketch replacement and mobile layout. Terrain tests independently check
depth and constant widths across viewing directions, area holes, conforming
mesh edges, source resolution independent of coverage, and tile-edge sampling.
Flat contour tests cover paper-scale sampling and budgets, recovery of a peak
missed by the old grid, exact planar isolines, bilinear tile seams, and cached
composition with a separate lightweight sketch.
Furniture regressions check a map without printed annotations, full-area
coverage, vector white knockouts, and compass placement/orientation across all
camera quadrants. The furniture browser check verifies independent controls,
reload persistence, unchanged scale/viewport, PDF export and mobile layout.
Incremental-build regressions compare exact cached/fresh PDF bytes and intents,
require label-only edits to skip non-label painting, and cover sun/spacing/ink/
source/camera/paper/quality/code invalidation, resource relocation, boundary
styles, restart reuse and failed-node publication. The live browser cache check
changes forest labels, verifies zero drawing-node rebuilds and checks the reuse
summary.
Terrain-mark tests cover pyramid silhouettes in four camera directions, hidden
rear peaks, sun reversal, flat ground, latitude/exaggeration, continuous hatch
rows, forest-label independence and vector PDF/ink/page bounds in 2D and 3D.
The browser check covers legacy draft migration, independent controls, shared
sun versus boundary styles, reload persistence and mobile layout.
Print tests cover delayed inspection/publication, exact intent preservation,
old receipts, restart/reload, repeat clicks, failed/unsupported conversion,
stale inventory, ambiguous POST responses, blank/multiple pages and superseding
operator/recipe revisions. The Python acceptance starts an isolated Plotline Studios
without a device configuration and sends the shared two-page boundary fixture
through the map HTTP submit endpoint. It verifies identical source bytes,
complete intent (including whitespace, placement, media and distinct ink
bindings), and registered HP7585 drafts. Its retained evidence is under
`build/cartographer-print-*`; no test calls the physical Plot endpoint.

The frozen Letter, northwest Letter, 11×17 regional and calibrated relief proof
scripts, styles, data and converter tests remain unchanged. The earlier
`docs/cartographer-studio.md` records the first, preset-specific milestone;
`docs/cartographer-unified.md` records its replacement.

## Storage and development scope

Durable unique inputs: `state/cartographer/recipes/` (including boundary
snapshots and revision thumbnails), `inventory-snapshot.json`, `preparations/<recipe-hash>.json` saved preparation recipes, and `submissions/<export-key>.json` print
task receipts, including their pinned intent and acknowledged IDs. Do not delete them as
build output. Rebuildable caches, environments, task logs and exports live in
`build/`; download a bundle to retain a portable rendering and its exact sources.
An export awaiting its first upload must remain available under `build/exports`
(the service default is `build/cartographer/exports`). After receipt, Plotline Studios
retains the original PDF and the map task can resume from its durable IDs/intent.
No archived serial/controller code is used.

The private origin is `/mnt/dev-source/repos/plotline.git`. The workspace is
covered by the existing `/root/mirror_manifest.json` entry; worktree `build/`
and environments are excluded. Git pushes preserve source; they do not prove a
Borg/offsite backup has completed. Physical plotting, external publication and
production integration require separate instructions from Jon.

## Shared pen workflow

The sidebar's **Feature inks** summary shows each role in the current region with
its color dot. Clicking a role opens its assignment directly; diameters appear
in the drawer. **Edit inks…** opens the shared editor in a bottom drawer spanning the
workspace, with the preview still usable above it. Closing it restores preview
space without changing pan/zoom or the recipe. Escape cancels an active ink
assignment first; a second Escape closes the drawer. On narrow screens the drawer
temporarily replaces the sidebar below the preview.

A warning triangle beside **Edit inks…** and **Prepare plot** opens an affected
role directly. The attention strip above the editor lists unavailable inks,
different loaded diameters and unknown diameters across active boundary regions;
clicking one switches to that region and selects its role. It stays separate
from the horizontally scrolling carousel. An unavailable authored ink is advisory,
not permission to change the artwork or a new preparation blocker. Unknown
**emitted** diameters take priority, preserving the existing readiness rules.

The single **Feature inks** tray shows role names above the inks they use.
Select a role, then click a loaded or document ink to assign it. Exact loaded
matches use carousel columns; other document colors have **Not loaded** columns.
The carousel is read-only. Refreshing it preserves artwork, and the existing
inside/outside selectors beside styling and inks stay synchronized. Each region
has its own assignments. Refresh changes the loaded row, including newly measured
diameters; reselect a loaded pen to adopt its properties in the document.
**Update map inks from carousel** adopts changed colors and measured diameters
from the inks' recorded carousel slots in one action, across both boundary
styles. It marks the active document dirty, regenerates its draft, and clears
its old PDF/preparation pointer so **Prepare plot** uses the updated recipe.
Legacy built-in inks without provenance and inks whose old slot is empty can
recover a binding when exactly one loaded pen matches their design color. An
existing matching slot takes priority. Previously adopted scan RGB is recognized
as an alias of the pen's design swatch and returns to that design color. Custom
inks without provenance remain unchanged; ambiguous matches require explicit
assignment. Scan appearance is separate inventory metadata used by HPGL preview,
not a replacement for the map's design colors. The action is disabled when
there are no updates or Plotline Studios is offline. A retained plot using earlier
measurements is identified beside **Open plot** after refresh or reload.
At rest, unloaded-ink columns appear only when a displayed role uses them.
Other-region colors remain available as destinations while making an assignment;
an outside-only default black no longer leaves an empty column in the inside tray.

The shared scrim interaction keeps the selected source and destination row
visible. Escape or clicking outside cancels. **+** creates and assigns a custom
ink inline; cancelling creates nothing. Identical inks are reused, and edits
remove inks with no remaining role references in either region. There is no
unused-ink drawer or separate feature picker. More than eight document inks
remain supported; each physical page must resolve to at most eight output pens.

Every emitted PDF layer must resolve to an explicit role in its boundary region;
unrecognized categories fail instead of silently using Labels. Furniture uses
Labels; legend samples use their feature roles. Cached bindings are validated
before publication, and regression checks interpret every PDF stroke/fill to
verify its role and color. The export palette includes only emitted-layer inks.
Unused Buildings or inactive outside inks with unknown diameters do not block
preparation. A missing emitted diameter identifies its ink and roles and offers
the warning triangle to jump directly into assignment. Full-detail preparation
checks its own emitted inks before any upload, independently of the draft.

New PDF intents include an authoring backlink to their exact recipe snapshot.
Set `PLOTLINE_MAP_PUBLIC_URL` when serving a different public address (the default
is `http://dev.iac.jonh.net:9191/map`). HPGL's **Rebuild upstream** returns the chosen
substitution here. Newer unsaved work is preserved; an older submitted snapshot
opens in another workspace tab. Review it, then Prepare plot to rebuild.

The shared files are mirrored unchanged between the two development branches.
[The plan](../docs/pen-workflow-plan.md) records the model and delivery checks.

```sh
build/cartographer-lite-venv/bin/python tools/check_cartographer_pens.py
```

This isolated browser check covers read-only inventory, direct role assignment, HTTP-origin ID creation, more
than eight document inks, refresh without artwork mutation, explicit adoption
of calibrated inks after reload (including draft/preparation invalidation and
both boundary styles), the wide/mobile drawer and warning navigation, preserved
preview navigation on close, and upstream rebuild without replacing unsaved documents.
It never contacts a physical plotter.

## Storage retention

The service checks caches each minute when no render/preparation/print-handoff
worker is active. Defaults: **512 MiB of exports**, **512 MiB of decoded/build-node
geometry**, and **seven days since last access**. Recently accessed work gets a
15-minute grace period; caps may temporarily be exceeded by recent/active work or
PDFs still needed by an incomplete upload. Per-key file locks protect geometry
used by standalone composers too. Saved input storage is intentionally uncapped.

Before deleting an export, save its exact recipe, intent, result and provenance
under `state/cartographer/export-recipes/<key>/`. These snapshots also serve the
source-recipe API after the preview expires. Completed/cancelled task directories
are removed after the grace period; their requests and short diagnostic tails
remain under `preview-recipes/`. Saved documents/revisions, thumbnails, preparation
records and submission receipts are never evicted. An expired in-memory preview
reports that a new preview is needed; submitting the same recipe rebuilds the
missing content cache. Pending or ambiguous uploads without a document receipt
keep their exact PDF for recovery.

Downloaded HTTP tiles/elevations are frozen inputs. At startup the service moves
`build/cartographer/geometry/http` into `state/cartographer/map-sources-http` and
leaves a compatibility symlink. Future downloads go into retained state, selected
by the existing backup manifest; the rest of `build/` remains disposable. The move
is restart-safe and refuses conflicting stores. This does not claim a backup has
already run. It also does not turn this cache policy into cleanup of unrelated
experiments, frozen proofs or other worktrees.

`state/cartographer/storage-policy.json` can override `export_max_bytes`,
`geometry_max_bytes`, `max_age_days` and `idle_grace_seconds` (nonnegative integer
values). `storage-status.json` reports the last completed sweep and remaining bytes.
The implementation and migration ship with the service, requiring no cron setup.

Retention validation: two focused cache/input-protection tests plus 29 existing
cartographer tests, seven document tests, 24 printing tests and nine preparation
tests. No physical plotting is involved.

## Shared notifications

Map Studio uses `studioNotice` from `shared/web/studio.js`, shared unchanged with
PDF/HPGL Studio, calibration, and navigation. A single corner panel has a visible
**× / Dismiss message** button, including for errors and navigation actions.
Long messages scroll inside the panel while the close button stays reachable.
Dismissal preserves the recipe/preview; repeated status renders do not bring the
same message back. `notice()` resets application feedback for a new operation;
its optional expiry uses the shared timer. Stale timers cannot hide replacements.
Inline progress and field validation remain in their own controls.

`node controller/scripts/studio-notices-smoke.mjs` tests the shared behavior without
a backend or device; set `PLOTLINE_BROWSER=firefox` for Firefox. The print-driver
branch's `studio-navigation-smoke.mjs` also checks the Map adapter through the
same-origin proxy, including legacy workspace transfer and dismissible notices.

## Paper units

**Paper units** uses the shared `paper-units.js` preference, defaulting to decimal
inches and following changes in PDF/HPGL Studio tabs on the same Plotline origin.
Sheet choices, margins, page offsets, on-paper minimum building area, and hatch
spacings follow it; pen diameters stay in mm and geographic distances keep their
map units. Canonical recipes and geometry remain in mm. Changing the preference
only repaints controls: no recipe edit, save, preview regeneration or cache change.
Use the shared field/format helpers for new paper controls, and mirror the shared
module unchanged between the two branches. `tools/check_cartographer_paper.py`
covers the adapter; `controller/scripts/paper-units-smoke.mjs` covers preference,
precision, numeric bounds and cross-tab behavior (Chromium and Firefox).
