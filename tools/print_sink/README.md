# Discoverable PDF printing device

The development printer **Plotline Studios HP7585** advertises `_ipp._tcp` via Avahi,
with the `_print` subtype and a web link to the HP7585 library. It accepts PDF,
preserves the received bytes, and opens a reviewable document in PDF Studio.
Choose the pen interpretation there, then use **Prepare plot** to create HPGL
drafts. New IPP captures do not automatically commit conversion defaults.
**Nothing physically plots until Jon clicks Plot.**

Review: **http://dev.iac.jonh.net:9191/** · PDF settings: **http://dev.iac.jonh.net:9191/pdf**.
Fallback printer URI: **`ipp://10.110.44.18:9192/ipp/print`**.
Transport status: http://dev.iac.jonh.net:9192/ (completed means captured for review).
Discovery requires multicast reachability; across routed networks use the URI.
This PDF-only endpoint does not claim IPP Everywhere certification.

Port 9191 is Plotline Studios's review, conversion and physical plotting UI. Port 9192
is the CUPS IPP receiver used by desktop print dialogs; its root page is CUPS's
basic transport/job status page. The numeric address and `dev.iac.jonh.net` on
9192 reach the same receiver. An explicit `PLOTLINE_SINK_HOST_ALIAS` (default
`dev.iac.jonh.net`) permits that DNS name in CUPS's HTTP Host validation, alongside
its existing numeric, localhost and mDNS names. This fixes the former HTTP 400
for the ordinary DNS name while preserving rejection of unconfigured names.

On the Linux laptop, remove the old **PlotlineCapture** queue in printer settings,
then select **Plotline Studios HP7585** from discovered printers. Discovery supplies the
current capabilities; manual queue editing is not required.

If you prefer to keep the existing queue name, these commands refresh it in place:

```sh
sudo lpadmin -p PlotlineCapture -E -v ipp://10.110.44.18:9192/ipp/print -m everywhere
sudo lpadmin -p PlotlineCapture -o print-scaling-default=none
```

Reopen Inkscape's print dialog after replacing or refreshing the queue (restart
Inkscape if needed).
Select the document's paper dimensions and actual size. The queue cannot undo
shrinking that an application has already baked into the received PDF.

Named sizes are Letter, Legal, 11×17, ANSI C–E, Arch C–E and ISO A4–A0, plus
custom widths/heights from **1 mm through 200 inches**. This is an intake range,
not a promise that the plotter can physically accommodate every size. A very small
page may need larger target paper selected in PDF studio. Named sizes advertise
asymmetric margins from the same Rust paper catalog used by PDF defaults and
physical registration. Letter uses 15 mm left/right/bottom and 39 mm top. The
11×17 landscape profile uses the existing measured/calibrated rectangle; other
formats use clearly labeled nominal margins. Final Plot still queries the loaded
plotter window. Custom PPD hardware margins remain zero so application-defined
pages arrive intact; the studio applies the physical target profile explicitly.

Type/source/tray are fixed, so some desktop dialogs display “Not Available” for
those controls. Page size and custom dimensions are the relevant controls.

## Run and verify

The implementation privately builds the pinned CUPS **2.4.20** `ippeveprinter`.
It does not replace system CUPS libraries or install a scheduler. The small patch
fixes attribute-file/PDF-format coexistence and custom-range validation, accepts
the configured DNS alias, and points the printer web link at Plotline Studios.
Archive/patch hashes are in `build/print-sink/build.json`.

```sh
sudo apt-get install build-essential patch pkg-config libavahi-client-dev avahi-daemon avahi-utils libssl-dev zlib1g-dev cups-ipp-utils cups-filters
cargo build --locked --release --manifest-path controller/Cargo.toml
python3 tools/print_sink/build_server.py

.venv/bin/python -m unittest discover -s tests -p 'test_print*.py' -v
node controller/scripts/ipp-flow-smoke.mjs
```

Production owns the household Pen Plotter queue. Leave `plotline-pdf-sink`
stopped and disabled on dev. The old manager now only supports inspection and
safe retirement; starting/restarting it is rejected. Do not remove IaC's
`95-production-owner.conf` override. Use the isolated `ipp-flow-smoke.mjs` check
above; it disables discovery and closes its processes when finished.

For isolated foreground tests, use `tools/print_sink/run.sh` with
`PLOTLINE_SINK_PORT`, `PLOTLINE_SINK_STATE`, `PLOTLINE_SINK_DISCOVERY=off`, and
`PLOTLINE_REVIEW_URL`, `PLOTLINE_SINK_NAME="Plotline isolated IPP test"`, and
`PLOTLINE_SINK_HOST_ALIAS=localhost`. Use unused ports, temporary state and
`PLOTLINE_SINK_DISCOVERY=off`; do not advertise the household printer from dev.
`PLOTLINE_SINK_INTAKE=0` retains capture-only behavior.
The studio watches the directory named by `PLOTLINE_IPP_INBOX`; dev defaults
point to `state/print-sink/captures`. Durable receipts prevent duplicate imports
across restarts; captures received while studio is offline are picked up later.

Exact inputs, selected scalar/collection IPP attributes exposed by CUPS, and
hash-bound inspection reports remain in `state/print-sink/captures/<id>/`.
CUPS may omit collection-valued attributes from the callback environment: this is
not a complete wire-level ticket archive. The original PDF is authoritative for
geometry. Each restart uses a distinct raw transport spool. Captures and studio
originals/settings are unique inputs within the existing backup selection;
rebuildable `build/` is excluded. No backup completion is implied.

The client-filter regressions exercise real CUPS-generated PPDs and `pdftopdf`,
including custom pages, Letter and rotated Tabloid. The full-flow test uses
isolated state and a fake serial helper; it never contacts the physical plotter.

## Historical capture evidence

The server-side IPP transport test delivered the Seattle relief PDF unchanged:
1,978 vector paints, 398 glyphs, zero image paints. Local tests also preserve
mixed vector/image documents and exact source bytes.

Jon's first Linux/Inkscape submission arrived on 2026-10-30 at 18:48:42 UTC as
job 2, `Print pattern.svg`. **Vector retention passes for this print path**:
34 stroked paths (194 cubic segments, 106 straight segments and one rectangle),
168 glyphs in eight text spans, and zero image paints. Colors remain black,
green, blue and purple. The received PDF is 15,834 bytes, produced by Cairo
1.18.4. Native extraction reports no unsupported operations. Its source SHA-256
is `72cb3c3c45ca87fc8fd16d3b7bed5e0f75a1b7c68b5c212b4b577ea8ba7fc340`.
The raw IPP spool file and retained capture are byte-identical.

**The original page size/scale failure:** this job actually supplies
`IPP_MEDIA_COL` with 914.4×1604.28 mm (36×63.16 inches), but its received PDF
MediaBox and CropBox are 612×792 pt: portrait Letter. The content includes a
2592×4547.589 pt page clip under a 0.158326 scale, with translation putting its
scaled height between 36 and 756 pt. Thus the large artwork was already reduced
to roughly 15.8% before capture. The sink never rewrites PDF bytes. This does
not by itself isolate the responsible application/desktop-filter step or
establish 1:1 placement relative to the original SVG. The investigation below
now reproduces the failure in the desktop PDF filter.

Durable input: `state/print-sink/captures/20261030T184842Z-89ac0e302869/`.
Derived review files: `build/inkscape-capture-2/inspection.json`,
`events.json`, [received PDF](http://dev.iac.jonh.net:11088/build/inkscape-capture-2/received.pdf)
and [rendered preview](http://dev.iac.jonh.net:11088/build/inkscape-capture-2/received.png).

### Custom media correction (2026-10-31)

The old sample printer lacked custom-size support. We generated its client PPD
using the installed `driverless` tool, then ran the real CUPS `pdftopdf` filter
on a synthetic 36×63.16-inch vector PDF with `PageSize=Custom.36x63.16in`.
The filter reports "falling back to US Letter format", returns a Letter page,
and reduces a 100-point ruler to 15.8328 points (scale **0.158328**). This closely
reproduces the **0.158326** transform in Jon's capture without running Inkscape.
It is strong evidence for the desktop-filter fallback. Jon's new laptop
submission below now confirms the fix with his actual client.

With the new advertised custom range, the generated client PPD includes
`ParamCustomPageSize` for both axes (72–14400 points). The same filter retains
the full custom page, 100-point ruler, one-point stroke width, color and vector
text. Decimal conversion introduces less than 0.1 point of page/position error
in the tested path; the ruler length and width remain unchanged. The check also
covers Letter, Arch E, Tabloid landscape and explicit `media-col` dimensions.
CUPS represents the tested landscape job using `/Rotate 270`; tests check that
explicit rotation as well as the marks. The PDF studio normalizes received PDF
rotation in the ordinary way and never silently stretches it to match a ticket.

Five real integration tests query IPP attributes, generate the client PPD, run
the PDF filter, submit with strict media fidelity, and compare exact capture
bytes plus the retained custom-size ticket. They also reject media outside the
range on either axis and raster transport. Together with the existing six
boundary tests, **11 tests pass**. Synthetic test captures live in a temporary
directory and are removed; the original user captures remain untouched.

The local `driverless` version writes Unicode inch marks in generated PPD
labels, which `cupstestppd` flags as translation style errors; this does not
change numeric dimensions or the tested filter behavior. This endpoint does
not claim IPP Everywhere certification (that standard requires raster support).

### Laptop confirmation (2026-10-31, 02:52 UTC)

Jon's second `Print pattern.svg` arrived as capture
`state/print-sink/captures/20261031T025228Z-ce8e92159292/` (job 1 after restart).
Its 15,845-byte PDF has SHA-256
`4f6587bd7d5e5690ed6fad553ae602afaf52d0f2aeff6957c6361085836b579f`.
Both the ticket and received PDF now describe **36×63.16063 inches**:
914.4×1604.28 mm, with `IPP_PRINT_SCALING=none` and PDF rotation 0.
The page wrapper is `1 0 0 1 0 0.000244 cm`: unit scale, with only a tiny
0.000244-point Y translation, replacing the earlier 0.158326 shrink.

The decoded `X1` artwork stream is byte-identical between the two captures
(15,642 bytes, SHA-256
`e95d82637b51f5b5bf3b617b646612d7e2ad816cac24d85153aae9efeb5b3678`).
Both retain 34 stroked paths/301 path segments, 168 glyphs in eight text spans,
the same black/green/blue/purple colors and text, and zero image paints. The
former 4.74978-point text is now 30 points; every stroke width scales back by
the reciprocal of 0.158326. The original artwork is arriving at full size.

Jon reported paper type/source/output tray as “Not Available” in Page Setup.
The generated client PPD has no `MediaType`, `InputSlot` or `OutputBin` choices:
the endpoint has one fixed source (`main`), one type (`stationery`), and no output
bin selection. `PageSize` and `ParamCustomPageSize` are present. Those unavailable
stock/tray controls did not prevent correct custom paper dimensions or capture.

The status page's completed state means captured, not physically printed.
At this historical checkpoint, captures were not auto-forwarded. The integrated
intake described above now forwards new submissions; old captures remain preserved.

Raw transport spool copies now expire after the callback has durably saved and
hash-verified `captures/<id>/source.pdf` and its receipt. Startup also removes old
spool PDFs only when an intact capture has exactly the same SHA-256. Unmatched
spools and all captured originals remain. `-k` still protects a job whose callback
fails before it can retain the input. This preserves intake/replay receipts while
avoiding an extra permanent copy of every accepted PDF.
