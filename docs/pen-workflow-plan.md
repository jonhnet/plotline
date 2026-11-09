# Shared pen workflow

Accepted design, updated 2026-11-02 after the direct-assignment UX discussion. This applies to all three Plotline Studios: Map, PDF and HPGL.

## Model and interaction

Use one shared assignment tray. Select a source, then click a destination pen.
A scrim dims the page while the source, destination row, selection summary and
Cancel remain visible. Escape or clicking the scrim cancels without changes.
The chosen source stays identifiable while scrolling to a destination. A narrow
viewport has explicit horizontal scroll buttons; the nearest loaded candidate
is brought into view without changing the assignment.

PDF and HPGL source stacks expand into one inline list. Every complete member
row is clickable; picking one closes the list and begins assignment. **Select
all N** is explicit. Mapping has no dialogs and no separate Use-slot button.
PDF always uses carousel order; HPGL also offers source order. Differences and
accepted substitutions remain visible. Physical inventory editing has its own
**Edit** action and never shares the assignment click.

HPGL Studio exposes the loaded carousel even when no document is open. Its
eight slots use the same Edit action; document pens and mapping controls appear
only when a document is selected. Closing the last document keeps inventory
editing available, so a fresh installation can configure pens before authoring.

Map has one **Feature inks** tray: role names above their actual inks. In Map
Studio this opens as a full-width bottom drawer from the sidebar's compact
role chips / **Edit inks…**. Each chip shows a role name and color dot and opens
that exact assignment; diameters stay in the drawer. The preview remains above it. A warning triangle
beside the launcher and Prepare plot opens an affected assignment; the drawer's
attention strip also exposes issues in offscreen columns and the other active
boundary region. Known but unloaded inks remain advisory; unknown diameters
block only when emitted. Opening/closing does not edit the recipe or reset
preview navigation. PDF and HPGL retain their existing tray placement.

Roles sharing an ink are listed above it. Exact color/diameter matches occupy loaded
carousel columns; other colors occupy **Not loaded** columns. The current
inside/outside selectors beside styling and inks stay synchronized and scope
the roles being edited. Selecting a loaded pen
copies its properties to the role's document ink without changing inventory.
Identical inks are reused. Edits prune inks with no remaining inside or outside
role references; there is no unused-ink drawer. Refreshing inventory preserves
the artwork and moves changed matches into their proper columns.
Unloaded colors used only by the other region stay out of the resting tray;
they become available as destinations during assignment. The other region's
saved style is preserved.

In Map, **+** creates and assigns an ink in one inline operation. Cancel creates nothing.
Map supports more than eight document inks; the physical output limit is separate.
There is no second feature-picker or palette-management interface.

PDF owns conversion choices, not source artwork. Its upper row is the immutable
source palette (authored inks when supplied, otherwise source colors and stroke
widths). Mapping selects real loaded output pens; there is no grouping or order
control and no custom output-ink editor. Sources assigned to the same slot stack
automatically, exposing exact members and individual or bulk assignments.
**Map automatically** chooses the closest color (OKLab distance), then the closest
diameter for a color tie. Empty/unavailable slots, white and unmeasured pens are
excluded. This changes conversion settings without submitting a conversion or
changing source inks or inventory. New PDFs default to **Preserve document widths**,
allowing multiple passes. **Use pen width** remains available for one pass per
visible source stroke.
Explicit Map intents and existing conversions retain their width policy. Fills,
white knockouts and outlined text keep their own semantics.

Map paint must resolve to an explicit role in the relevant boundary region;
unknown categories cannot silently borrow Labels. Furniture uses Labels, and
legend samples use their named feature roles. Cache replay is checked too.
Print intents contain only the inks referenced by emitted layers (with a measured
palette placeholder permitted for a completely blank page). Unused Buildings or
inactive outside assignments with unknown diameters do not block a plot.
A missing emitted diameter names the ink and its roles and offers **Choose
measured inks**. Refreshing the loaded carousel does not rewrite document inks:
reselect the measured pen to adopt its diameter, including a newly measured one.
Map's **Update map inks from carousel** also offers bulk adoption from recorded
slot bindings. It updates both boundary styles in the active document, marks it
dirty, regenerates the draft and invalidates its PDF/prepared-plot pointer.
Custom inks stay as authored. Legacy built-in inks and old bindings may
recover through a unique design-color match; ambiguous matches need explicit
assignment. Scan-derived `measured_color` is separate from the loaded pen's
design `color`. HPGL preview can use either, while authoring keeps design colors. Reload can identify a prepared plot using earlier measurements
without silently changing the document or submitting another plot.

HPGL shows the pens its existing paths were prepared for above the actual
carousel. Matching pens may move slots without rebuilding. Explicitly choosing a
destination in HPGL's ink tray accepts that exact loaded material on the existing
paths; it needs no second confirmation. Carousel drift and automatic matching
still offer **Use existing paths** or **Rebuild upstream** actions in a fixed strip
above the scrolling pens. A selector visits multiple unresolved changes without
growing the drawer. Acceptance is specific
to the source requirement, selected slot, color and diameter, and is checked on
the server. The physical preview uses the actual selected pen on unchanged
paths. Rebuild returns the substitution to PDF Studio or the originating Map
workspace, preserving unrelated settings and unsaved work. No action here starts
a physical plot. Remove the inventory-overwriting “Use document pens” shortcut.

The 2026-11-06 HPGL review checks pass in Chromium and Firefox: both actions
remain clickable above the scroller at desktop and phone sizes, explicit
color/width choices survive reload, and subsequent inventory drift invalidates
acceptance. The HPGL workspace smoke check also passes. The broader pen workflow
test currently reaches an unrelated PDF saved-state failure after a loaded-width
edit and Undo (`Unsaved changes` instead of `Saved`); this also reproduces on the
unchanged server. Its small source-PDF fixture now explicitly selects a supported
tabloid sheet to satisfy the current platen-width rules.

Eight is the physical capacity of one plot, not the authoring palette size.
Resolve explicit many-to-one assignments and compact identical effective pens
before conversion. If more than eight distinct used pens remain, explain the
needed merges; never silently drop or recolor inks. Extra pen-change passes are
outside this implementation.

## Contract and compatibility

- Remove the eight-entry document-palette schema limit. Keep stable logical IDs.
- Add explicit source-to-output ink mappings and stroke-width mode to print
  intent. Missing width mode preserves existing conversion behavior.
- Inspect generic PDF source styles with stable identities; retain exact values
  beneath grouping. White remains paper/occlusion, never a requested white pen.
- Keep recorded inventory separate from conversion palettes and provenance.
- Carry a source-workspace reference and reviewed substitutions upstream without
  rewriting the original document or silently submitting a new conversion.
- Permit unassigned slots and many-to-one mappings in saved HPGL setups; reject
  unassigned/unavailable selections at preparation, and validate any material
  substitution acceptance against the current recorded pen.

## Delivery and validation

- [x] Shared component, contracts, mapping and source-style helpers.
- [x] PDF inspection, effective-palette resolution and single-pass policy.
- [x] HPGL substitution acceptance, accurate preview and upstream rebuild.
- [x] PDF immutable source row, grouping and conversion controls.
- [x] Map role-to-ink tray, unlimited document palette and upstream rebuild.
- [x] Contract/conversion/controller tests and browser checks across studios.
- [x] Install on the existing dev services and preserve user state.

Validation includes twelve-plus document inks; thousands of source styles;
many-to-one mappings; unknown and unavailable pens; accepted substitutions becoming
stale; white paint and clips under single-pass conversion; preserved legacy map
conversion behavior; and returning a substitution to the correct unsaved Map
document. No physical plotting is part of validation.

## Delivered and checked, 2026-11-01

Plotline Studios runs from `main`; Map Studio runs from `artistic-cartographer`.
Both dev services were rebuilt/restarted and their shared assets and capabilities
checked. Saved documents, conversion revisions and recorded inventory were
preserved. Existing cached exports without an authoring backlink still rebuild
through PDF Studio; newly generated Map intents include their source reference.

Passed checks:

- Rust converter and controller suites, including explicit substitution acceptance,
  invalidation after material changes, unassigned pens and many-to-one output.
- Python PDF driver (17), print contract (6), new pen workflow (3), and
  cartographer (85) tests. Single-pass fixtures cover white knockouts and clips;
  legacy conversion fixtures retain their previous behavior.
- Existing PDF, HPGL and simulated plotting browser checks, plus Map document
  Save/Open/dirty-tab checks and the real isolated Map-to-Plotline Studios handoff.
- `controller/scripts/pen-workflow-smoke.mjs`: actual conversion, 24 distinct
  source widths mapped together, 10,000-style grouping, accepted substitutions,
  stale acceptance rejection, upstream PDF rebuild and desktop/mobile layout.
- Map's `tools/check_cartographer_pens.py`: read-only inventory, shared feature
  picker, eleven document inks, snapshot refresh without artwork changes,
  document editing, and source rebuild preserving newer unsaved work.

Browser evidence is retained under ignored `build/pen-browser-Np6eOT` in the
Plotline Studios checkout and `build/pen-browser-dvwvzaku` in the Map checkout. Tests
used isolated state and simulated hardware. Build/test commands are in
`controller/README.md` and `cartographer/README.md` on their respective branches.

## Direct assignment validation, 2026-11-02

The previous assignment dialogs and unused-ink drawer are superseded by the
interaction above. The dead PDF assignment buttons were a separate bug:
`crypto.randomUUID()` is unavailable on the plain HTTP dev origin. Output ink
IDs now use `crypto.getRandomValues()`, which is available there. Browser tests
use ordinary HTTP hostnames mapped to isolated local servers, assert that
`isSecureContext` is false and `randomUUID` is absent, then perform the actual
red-to-pink and green-to-green assignments and conversion.

The updated Map test covers the role tray, Escape/scrim cancellation, atomic
custom creation, unused-blue deletion and reload, identical-ink reuse, more than
eight used inks, read-only inventory refresh and preserving newer unsaved work
when returning from HPGL. The Plotline Studios test covers individual and bulk stack
assignment, inline HPGL acceptance/rebuild, custom pens and phone interaction.
The existing PDF/HPGL browser regressions and Map Save/Open/tab tests remain in
use. All fixtures use isolated state and no physical plotting.

Installed on the existing dev services after checks passed. Current evidence:
`build/pen-browser-nGnq93` in Plotline Studios (including the regression that applying
an inline custom ink must not submit the outer conversion form), and
`build/pen-browser-to6go98q` in Map. Controller's 40 tests, existing PDF/HPGL browser
checks and Map document persistence checks pass. Shared assets were verified
byte-for-byte against both live services. No user documents or inventory were
rewritten by validation.

## Role ownership and carousel-only PDF mapping, 2026-11-02

The PDF grouping/order controls and custom conversion pen are superseded by
fixed carousel order and **Map automatically**. Validation checks exact-color
priority over a closer diameter in another color, width choice between two
same-color pens, immutable source styles, and no accidental conversion submit.
Evidence: `build/pen-browser-cveEVO`; the existing PDF browser suite also passes.

Map validation now checks every interpreted stroke/fill against its region's
role and ink color in clean and cached 2D/terrain PDFs. Unknown categories and
stale cached bindings fail explicitly. All 88 Map unit tests pass, as does the
isolated PDF/intent → inspection → HPGL handoff (`build/cartographer-print-nder2cdt`).
The browser fixture (`build/pen-browser-hko0dzc1`) covers an unused unknown pink
Buildings ink, a used unknown ink in both draft and proof, refreshing a newly
measured 0.7 mm pen without rewriting document assignments, reselecting it, and
synchronized inside/outside selectors. Fixtures do not connect to a plotter.
