# Maze Studio

Maze Studio lives at `/mazes` on the existing Plotline Studios server. It grows
plotter mazes on square, regular hexagonal and equilateral triangular grids.
Jon Howell's [maze-harvester](https://github.com/jonhnet/maze-harvester) is the
inspiration: repeatable seeds, adjustable solution convolution, movable openings,
shape masks and paper-sized vector output. The Java project is GPL-3.0; this
independent Rust implementation is Plotline-authored AGPL-3.0-only code. No Java
runtime, copied third-party source, image-analysis service or new dependency is
required. The original README and `Maze.java`, `SolvedMaze.java`,
`StretchOptions.java`, `ImageFieldMask.java` were consulted on 2026-11-06.

## Controls and physical output

- Geometry, a single **Detail level** slider, seed, and **Another maze**. The
  regular grid fills the printable area to within cell rounding; increasing
  detail adds smaller cells. Paper, orientation and margin changes derive new
  row/column counts automatically. Counts remain internal recipe parameters.
  Existing saved grids and source snapshots reopen unchanged; moving Detail
  level or changing their layout adopts automatic sizing.
- **Solution stretch** is a target multiple of the shortest available cell route
  between the openings. Zero leaves the initial random tree unchanged. Increasing
  it adds detours without loops or multiple solutions. This deliberately uses
  graph distance instead of the original program's grid bounding-box diagonal,
  so it remains meaningful for all geometries and masks. It is a length target,
  not a calibrated difficulty score. The achieved ratio is reported honestly.
- Start/finish X/Y positions are percentages across the grid bounds. Each snaps
  to a boundary edge, including hole boundaries, in a distinct cell.
- PNG/JPEG mask upload: dark pixels form the maze; white or transparent regions
  leave holes. The browser composites on white, downsamples to at most 256 pixels
  on the longest edge, and thresholds luminance at 128. The grid adapts to
  mask aspect and the chosen regular tiling at the selected detail level;
  narrow bridges can disappear at coarse detail. Disconnected islands are
  rejected with guidance, rather than silently dropped or linked across holes.
- **Include solution in artwork** controls the solution in the preview, SVG,
  PDF and prepared HPGL with its own ink role.
  Preparing once with it checked and again unchecked creates two separate
  retained drafts in the HPGL library. The automatic layout reserves both role
  widths so even a fatter solution pen cannot shift the walls between versions.
- Shared paper profiles, rotations, measured or custom margins, preferred paper
  units (decimal inches by default), and Walls/Solution ink assignments. Pen
  diameters stay in mm. The carousel is read, never changed by authoring.
- Save, Save As, library, document tabs, undo/redo, PDF/SVG downloads,
  **Prepare plot**, **Open plot**, and downstream **Edit maze** / exact source
  version links follow the existing authoring workflow.

Regular cells are fitted uniformly to the printable rectangle, leaving the
shared content inset (at least 1 mm) plus half the widest role pen for automatic
layouts (the plotted roles for legacy grids). Nothing is centered downstream: the PDF carries its intended physical page placement.
The ink footprint fits inside the rectangle in every paper orientation. The
conservative corridor check uses the cell's incircle diameter minus wall width;
when a plotted solution is included, its diameter must also fit. An over-dense
maze can be inspected but cannot be exported/prepared until the user reduces
detail level, enlarges the printable area, or chooses finer pens.

## Implementation and limits

`controller/src/maze.rs` uses integer vertex keys for the three tilings. Each
shared wall exists once. Seeded randomized Prim growth makes a spanning tree.
A multi-source traversal from the solution labels every branch by attachment
and depth. One scan of closed interior doors finds the largest positive detour;
opening that door and closing a solution edge preserves the spanning tree.
Only strictly improving swaps are accepted, with a limit of 128. The UI reports
when the target cannot be reached. Each iteration is O(cells + edges), with no
all-pairs geometry or repeated polygon intersections.

Degree-two wall vertices are joined into longer paths. Junctions remain split;
no wall is duplicated or retraced to reduce lifts. The solution is a separate
polyline. Mileage is geometric pen-down distance, not a plotting-time estimate.

Limits: 2–160 rows/columns, at most 16,000 unmasked grid cells, stretch 0–12,
32-bit seed, 8 MB PNG/JPEG input. The HTTP generator validates masks up to 512²
binary pixels; the UI's normalized masks are at most 256². Generation runs in a
blocking worker with a two-job concurrency bound. Browser generation is
debounced; obsolete replies cannot replace newer recipes. Color, pen width
and solution visibility reuse the graph and refit locally. Layout changes
rederive the grid before generation. The detail slider runs from 1 to 100,
using a geometric cell-count progression from roughly 64 cells to the maximum
that fits the chosen aspect within the row, column and 16,000-cell limits.
Whole-cell rounding keeps both counts nondecreasing with detail.

Named recipes and thumbnails live in `controller/state/mazes/documents/`.
Original uploaded mask bytes are retained by SHA-256 in `mazes/masks/`; the
normalized binary mask and source hash are frozen in the recipe. Immutable
`mazes/exports/<hash>/` directories contain recipe, paths, PDF and intent. These
are durable user documents covered by the existing `/root/plotline` backup
manifest, not disposable build outputs. Browser workspace backups retain
unsaved recipes; Save is the durable named-document operation.

**Open → Import recipe** opens a downloaded JSON recipe as a new unsaved tab.
**PDF & other options → Download recipe** shares the current design. The shared
Pattern/Maze importer preserves the exact seed, grid, entrance positions, inline
mask, inks and paper settings. Import does not recompute an older saved grid or
require the original mask image on the receiving installation. Saved document
IDs and print receipts are not transferred; the carousel is not edited.

`controller/src/patterns.rs` now shares its revision-checked document store and
immutable export machinery between Pattern and Maze Studios. Existing Pattern
API paths and state are unchanged. `tools/pattern_pdf.py --kind mazes` writes
native stroked PDF layers, logical ink bindings and `maze-studio` provenance
under the shared print contract. Preparation uses `authoring-print.js` and stops
at a registered HPGL draft. It never initiates physical plotting.

## Validation

From the repository root:

```sh
cargo test --manifest-path controller/Cargo.toml maze::
cargo test --manifest-path controller/Cargo.toml patterns::
node tools/maze-geometry.test.mjs
PYTHONPATH=tools .venv/bin/python -m unittest discover -s tests -p 'test_maze_export.py'
PYTHONPATH=tools .venv/bin/python -m unittest discover -s tests -p 'test_patterns.py'
PYTHONPATH=tools .venv/bin/python -m unittest discover -s tests -p 'test_print_contract.py'
cargo build --manifest-path controller/Cargo.toml
node controller/scripts/maze-studio-smoke.mjs
node controller/scripts/recipe-import-smoke.mjs
```

The browser test uses a temporary state directory and no device configuration.
It checks all tilings, solution preview/export agreement, mask holes/disconnection,
original mask retention, undo, save conflicts, separation from Pattern documents,
PDF/HPGL conversion, source-tab reuse, frozen source reopening, stale preview
invalidation, pen clearance and unchanged carousel inventory. It also checks
orientation-driven grid changes, retention of legacy grids, and preparing two
independent solution variants with identical wall paths despite unequal pen widths. The shared Pattern
browser acceptance remains a regression for the reused store/export path.
