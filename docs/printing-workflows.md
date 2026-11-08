# Five studios, one physical plotting stage

Plotline Studios is one application containing Map Studio, Pattern Studio, Maze Studio, PDF Studio and HPGL
Studio. They share a landing page, navigation and document workspace language.
The Studios server hosts PDF/HPGL and proxies the map composer; IPP intake is
a helper process. Their HTTP document/intent/capability boundary remains useful
for modularity, but does not define separately branded applications or constrain
how the product is packaged.
See [studio navigation](studio-navigation.md) for source-version links and tab reuse.

| Stage | User's decisions | Advance |
| --- | --- | --- |
| Desktop application | Design a vector document | Print via IPP to PDF Studio |
| PDF Studio | Interpret that PDF as pen strokes: inks, fill representation, image/effect policies and intended page placement | Preview pen strokes, then Open plot ↗ |
| MapStudio | Design the map for intended paper and document inks, informed by available supplies | Print: full detail and conversion run as one operation, then open HPGL Studio |
| Pattern Studio | Choose a mathematical family, composition, paper and role inks, seeded from loaded pens | Prepare plot, then Open plot ↗ |
| Maze Studio | Choose a grid, mask, solution stretch, paper and wall/solution inks | Prepare plot, then Open plot ↗ |
| HPGL Studio | Load the planned sheet, verify fit and orientation, match intended inks to actual loaded pens | Plot |

New IPP captures stop after inspection, ready for PDF interpretation. They do not
commit an HPGL draft using unreviewed defaults. The explicit **Preview pen strokes**
action converts the chosen settings and registers the resulting HPGL as one server
operation. **Open plot ↗** enables when both finish and opens HPGL Studio only on
click. It never starts conversion or registration itself. A registration failure
can retry from the retained conversion; viewing a historical preview alone never
replaces today's draft. Existing HPGL jobs and historical automatic-intake receipts
remain available. Earlier job bytes and active physical plot receipts stay immutable.

MapStudio already knows its conversion choices. Its normal Print flow does not
stop in PDF Studio. Optional PDF downloads and conversion overrides remain under
PDF & other options. A completed preparation opens the same plot on subsequent
visits; it does not regenerate a PDF merely because the browser reloaded.
Preparation finishes in the map workspace. **Open plot ↗** opens HPGL Studio in
another browser tab, preserving MapStudio's open documents and unsaved edits.

Physical choices must not silently redesign the document. A differently numbered
carousel slot with the same color/diameter is an equivalent assignment. A different
color or diameter offers explicit acceptance on existing paths or a rebuild in
the source authoring or PDF studio. The shared paired pen display shows the difference;
acceptance applies only to the exact selected pen and source requirement. A PDF-derived job retains its planned sheet placement;
HPGL Studio shows the sheet to load rather than a collection of disabled layout
controls. Raw HPGL imports retain their existing placement tools.

## Pen-aware HTTP boundary

Use the existing [v1 PDF/ink/paper contract](../contracts/v1/README.md). The
authoritative payload is **PDF bytes plus a complete print-intent snapshot**:

- `source_sha256` binds intent to the exact PDF.
- Selected pages specify source dimensions, physical scale/offset/rotation and
  the intended sheet/printable-area profile. Preserve whitespace and page origin.
- Logical inks specify stable IDs, colors and known diameters. Two inks can share
  a color with different widths. Carousel revision/slot is historical provenance.
- Optional PDF layer bindings select inks and feature attribution precisely.
- Explicit fill, width and image/effect policies specify the pen interpretation.
  White paint still knocks out underlying strokes without requiring a white pen.

The upstream authoring interface is `GET /api/v1/capabilities`: supported policies,
available inks and paper profiles. Copy selected attributes into the document.
Refresh does not replace existing design choices. Unknown diameters remain
unknown. Paper profiles describe supported/calibrated configurations, not an
assertion that a particular physical sheet is currently loaded.

The implemented map handoff is:

1. Persist a submission UUID, then multipart `POST /api/v1/documents` with
   `document`, `intent` and `Idempotency-Key`. Retain the acknowledged document ID
   and status/review URLs; recover a lost reply through `/api/v1/submissions/{key}`.
   See the [recoverable submission contract](../contracts/v1/submission.md).
2. Await successful inspection, checking the exact source hash and intent.
3. `POST /api/v1/documents/{id}/draft` with the **complete authored intent**.
   MapStudio does not request generic loaded-pen defaults with `{}`.
4. Follow the conversion receipt until it is ready and the same revision's
   registered HPGL jobs are published. Open its HPGL URL. Keep the PDF review URL
   for optional overrides, unsupported-content review and recovery.

Both services persist their own receipts. MapStudio's `/api/prepare` is an
internal authoring endpoint: it saves the recipe, renders a proof if needed and
drives this HTTP sequence in the background. It is idempotent for an identical
recipe and reuses a previous exact-design handoff. Reload and ordinary preview
cancellation do not cancel an explicitly requested preparation. New edits do not
attach older results or navigate away when an older preparation completes.

## Progress and failures

Plotline Studios conversion status includes optional `progress` with `version: 1`,
`phase`, `label`, `completed`, `total`, `unit`, `page_number`, `page_count`,
`updated_at` and phase timing. Terminal conversion status is authoritative;
progress is advisory. Counts have meaning only within their named phase/page.

MapStudio forwards that progress into its single preparation display alongside
its own rendering progress. Its three visible stages are Map detail, Pen paths
and Ready to load. The progress bar is a monotone work estimate, not an elapsed
time prediction. Preparing and receiving documents never starts physical Plot.

Keep clear distinctions between a rejected request, an unsupported conversion,
and a response lost after a request may have committed. Known failures can retry
the saved PDF/intent without re-rendering. Keyed upload acknowledgements recover
automatically by receipt lookup or an exact same-key replay. Older unkeyed uploads
still require PDF Studio recovery. A lost draft response is reconciled only against
the selected conversion with matching source and intent. Preserve newer operator
choices and earlier successful drafts. Never silently choose fallback policies.

## Remaining contract improvements

The existing direct API is sufficient for the streamlined UI. A future single
Plotline Studios submission endpoint could absorb inspection/draft orchestration while
retaining the same intent and the now-implemented upload idempotency key. It
should return the same progress and PDF/HPGL links, including per-page
empty/unsupported outcomes. This combined endpoint is a proposal; upload keys
and receipt lookup are implemented now.

Also useful in a future contract revision: a source-application/recipe backlink,
structured diagnostics distinguishing artwork decisions from physical supply
mismatches, and a schema for the existing progress extension. None requires
coupling the conversion API to map recipes, OSM data, or Map Studio's renderer.
