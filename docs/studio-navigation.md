# Plotline studio navigation

Plotline is the shared application name. The controller executable, API request
header, state directories and systemd unit keep their existing Plotline Studios names;
this UI change does not rename storage or the CUPS printer.

| Address on dev.iac.jonh.net:9191 | Purpose | Header |
| --- | --- | --- |
| `/` | Studio overview and workflow arrows | Plotline Studios, violet |
| `/map` | Design geography, composition and inks | Map Studio, green |
| `/patterns` | Design mathematical patterns and role inks | Pattern Studio, rose |
| `/pdf` | Interpret PDF artwork as pen strokes | PDF Studio, blue |
| `/hpgl` | Match physical pens and run the plot | HPGL Studio, amber |

The studio name leads the header and browser title; Plotline is the smaller brand.
Each studio has a matching letter favicon and a header switcher. The landing
page has its own four-tile favicon and connected, curved workflow arrows. Wide
**Prepare plot** arrows lead Map/Pattern/PDF → HPGL; lighter **Edit Source** arrows return
upstream. The dashed Map → PDF route uses Map's **PDF conversion settings** label.
Its tooltip explains the optional conversion adjustments: single/multiple-pass
strokes, thin-letter recovery and hatching for filled shapes, without changing
map geography or composition. Already-drawn hatch lines are not PDF area fills.
The cards reflow vertically on narrow screens and the arrows follow their edges.
The narrow diagram keeps the primary preparation and intake arrows; optional
conversion and source-return links remain available inside each Studio.
An inbound **Print from an app** arrow leads into PDF Studio, with **System print
dialog · IPP** below it. Its tooltip names the currently advertised printer,
**Plotline Studios HP7585**, and explains that the artwork arrives for review.
PDF and HPGL share the vertical floating
preview toolbar styling. No navigation action starts physical plotting.

## Browser tabs and source versions

Ordinary studio-link clicks reuse a known studio tab, focus it where permitted,
and select an internal document without reloading the app. A shared module uses
BroadcastChannel for both discovery and acknowledged document handoffs, including
independently opened tabs. Named-window lookup is not used to reacquire a discovered
tab: an unrelated browsing-context group can make that lookup create a duplicate.
Actual window references can assist with focus. Related tabs retain their opener
and register with every remembered neighbor on startup, discovery and heartbeat;
handoffs also establish a link in both directions. Firefox's named-target reuse
can change an existing tab's opener, so the opener tree alone is not durable.
A bounded same-origin reference walk recovers parents, children and siblings
after reload. Only a verified reference
permits a native named-target foreground request, never a BroadcastChannel name
alone. postMessage remains a fallback for related windows. A short discovery probe covers clicks just after page load. A request arriving during startup waits for the workspace to restore.
This preserves unsaved recipes and in-memory undo history. Ctrl/Cmd/middle clicks
keep normal browser behavior. If popups are blocked, an **Open here** fallback is
shown. Browsers control tab ordering and foreground focus; this is best effort.
If the receiving tab stays in the background, the sender briefly explains that
the browser kept the current tab in front. This separate navigation notice has a
close button and expires after five seconds; it never overwrites a document warning.
It is shown only if the sender still has focus after a short delay. A denied focus
request does not open another copy. Playwright tab-reuse checks verify document
handoffs and window counts; its Firefox build overrides focus/visibility and
cannot prove foreground selection. A separate stock-Firefox BiDi test uses real
pointer clicks and checks both destination-visible and source-hidden states for
all 12 directions, before and after reloading all four related tabs. Neither test
guarantees foreground selection for independently opened tabs. Firefox can restrict programmatic focus even after the handoff
succeeds; the app cannot guarantee focus without changing that browser policy. Old studio tabs running the earlier handoff protocol
ask for a reload instead of creating duplicates. Reuse requires the same origin:
use the shared `/map`, `/pdf`, `/hpgl` addresses, rather than mixing ports or hostnames.
The landing page uses the same navigation path as the studio switcher: reuse an
existing studio tab or open a new one, always keeping the landing page open.
Completing a slow preparation does not steal focus;
the operator clicks **Open plot** when ready.

The studio/Plotline logo and **Plotline home** switcher entry use the same handoff
policy for the landing page. The landing page advertises itself as a navigation
destination: reuse it when open, otherwise open a separate tab. Returning home
never replaces a studio or its unsaved workspace.

If an older tab has already lost every window reference, reloading it cannot
reconstruct those references from BroadcastChannel names. Save its pending edits,
close that tab, and reopen the studio through another Plotline tab. That creates
a real connection which the current module retains across further switches.

**Edit source** selects the editable source document, preserving an already-open
working copy. Separate **View source map/PDF version** links open the exact recipe
snapshot that generated the plot. If the existing document has changed, the
snapshot opens as a separate internal tab marked “source version.”

Map's optional `authoring` print-intent record contains:

- `application`, `url`, and `recipe_hash`: existing exact source-snapshot identity.
- `document_id`: stable saved Map document identity, when one exists.
- `document_revision`: present only when the prepared recipe exactly matches that
  saved revision. Unsaved edits on a named Map keep its ID and their own exact hash.

The preparation request validates saved identity/revision before passing this
metadata through PDF conversion to HPGL. Reusing an immutable print receipt keeps
its original ancestry, even if another named Map has identical content. Older
receipts remain unchanged: without a stable document ID, **Edit map** opens the
recorded snapshot instead of guessing a saved document by name. An expired or
missing source snapshot reports unavailable; it never silently substitutes the
latest map. PDF conversions retain authoring metadata when their recipe changes.

## Service routing and old addresses

Pattern Studio is hosted directly by the Studios server at `/patterns`. Its
browser worker generates bounded native paths; `tools/pattern_pdf.py` uses the
existing Python PDF environment to export them. Named documents, thumbnails and
immutable source snapshots live under the controller state directory's
`patterns/` subtree. `pattern-studio` authoring records use the same source
identity fields as maps. HPGL's material-substitution action opens a separate
source pattern with the proposed inks, preserving other working documents.

Map remains its own Python service. The controller proxies `/map` and `/map/*`
to `PLOTLINE_MAP_UPSTREAM`, default `http://127.0.0.1:9193`. The configured upstream
must be a loopback HTTP origin. Requests retain browser Host/Origin; Map still
rejects foreign-origin writes. The proxy sets `/map` as the forwarded prefix;
Map assets, artifact links and API requests then use that prefix. Shared assets
are served from the controller root. Keep `shared/web` and the v1 print-intent
schema synchronized between the current separate driver and cartographer branches.

`/#job-id` and `/?recipe=…#job-id` bookmarks redirect to `/hpgl` with their query
and hash intact. Old receipt URLs normalize to the current Plotline origin.
The old Map address at port 9193 remains usable. Its browser storage has a separate
origin: after reload, **Move open documents** copies its workspace (including
unsaved drafts) to the shared address, preserving existing drafts and leaving the
original tab unchanged. Nothing is deleted automatically. Saved server documents
are available from either address. The old Map transfer uses the print client's
configured public server origin; its receiver accepts only the matching old Map
origin. `PLOTLINE_MAP_PUBLIC_URL` defaults to the canonical dev `/map` address for
new source links.

## Verification

Tests start isolated stores and fixtures; none contact stain or send a real plot.
Prepare the converter and boundary PDF fixture as in the main README, and install
`controller`'s Playwright dependencies and the shared Lite Python environment
in `.venv`. All components are in one checkout; run from its root:

```sh
node controller/scripts/studio-tabs-smoke.mjs
# Optional second engine, after `npx --prefix controller playwright install firefox`:
PLOTLINE_BROWSER=firefox node controller/scripts/studio-tabs-smoke.mjs
# Native tab selection, using an ordinary Firefox installation, not Playwright's:
PLOTLINE_FIREFOX_BINARY=/path/to/firefox \
  node --experimental-websocket controller/scripts/studio-focus-firefox.mjs
cargo test --locked --manifest-path controller/Cargo.toml
cargo build --locked --manifest-path controller/Cargo.toml
PLOTLINE_TEST_BINARY="$PWD/controller/target/debug/plotline-studios" \
  node controller/scripts/studio-navigation-smoke.mjs
PLOTLINE_TEST_BINARY="$PWD/controller/target/debug/plotline-studios" \
  node controller/scripts/pdf-workspace-smoke.mjs
PLOTLINE_BINARY="$PWD/controller/target/debug/plotline-studios" \
  node controller/scripts/hpgl-workspace-smoke.mjs
```

The cross-studio check exercises real PDF conversion and source metadata, Map's
proxy origin checks, three-tab reuse/reload, dirty documents, exact snapshots,
legacy HPGL bookmarks and Map workspace transfer, mobile layout, and blocked-popup
fallback. Screenshots/logs go into ignored `build/`. Map preparation unit tests
also check immutable source lineage. Development deployment uses the guarded
`controller/scripts/dev-server.py restart`; preserve active map workers when
restarting `plotline-cartographer`.

Validated 2026-11-04: 59 controller unit tests, 41 Map preparation/printing/document
tests, the combined navigation check, PDF/HPGL workspace checks, general preview
browser checks and the complete fake-plotter suite passed. The controller was
merged with the concurrent PDF raster-rendering work before the final navigation
and PDF workspace runs. The deployed site also passed a read-only round trip from
a retained HPGL drawing through its PDF recipe to its exact Map snapshot. No
physical plotting, saved recipe edits or new conversions were used for deployment
verification.

The tab-reuse regression was reproduced with independently opened browser tabs:
the old implementation opened a fourth HPGL tab. The standalone check covers that
case, landing navigation, workspace preservation, reload, startup queues, lost
acknowledgements, focus denial and a closed-tab replacement. The cross-service
check now starts HPGL independently and exercises the real PDF **Open plot** button
and landing link against it. Earlier related-window tests did not cover this case.

Validated the independent-tab regression in Chromium and Firefox, and the real
PDF/Map/HPGL workflow in Chromium. The optional standalone Firefox run tests
navigation without taking responsive screenshots; an exploratory full Firefox
run stalled while resizing the Map screenshot viewport. The existing Chromium
mobile checks remain enabled. This change does not alter preview layout.
