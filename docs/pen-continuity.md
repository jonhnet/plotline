# Joining overlapping pen strokes

The Ravenna Park job `b6b546cd-a609-4da9-b2ef-bf5562cb530d` lifted the pen after
every converter path, including adjoining strokes that looked like one zigzag.
The final HPGL stage now joins adjacent routed paths of the same document ink.
This applies to new PDF conversions, including Map Studio's Prepare plot flow.
Previously saved HPGL remains immutable; rebuild its PDF conversion to use it.

Exact shared endpoints need no added geometry. For distinct endpoints, their
round end caps must overlap or touch (distance at most one pen diameter), and
the complete round-buffered connector must fit the union of the two paints'
allowed regions. Those regions already exclude original clips and later paint,
including white road cores and label knockouts. Checking just the centerline or
endpoint distance would incorrectly fill clear gaps. The existing 1e-7 pt²
geometry tolerance applies. Checks use the final integer HPGL coordinates.

The pass examines adjacent paths only, with no all-pairs search or global ink
union. It does not change routing, pen assignments, widths, or paper placement.
It can add a small amount of ink within the allowed regions at a joint; it does
not claim pixel-identical marks. It leaves distinct inks separate, even if the
operator later maps both to the same carousel slot.

Independent HPGL readback checks the exact concatenation of original segments
and checked connections. Original paths retain their paint IDs and their full
coverage checks. Per-paint reports describe these source paths; physical runs
can span multiple paints. `continuity.connections` records both source IDs,
ink, distance and allowed-area error for every removed lift. Per-ink totals count
physical runs and include connecting mileage. Per-feature path counts remain
source-path counts; connecting mileage is divided equally between its two source
features. This preserves mileage attribution without claiming one source owns an
entire joined run. Lost-detail diagnostics remain conservative source-paint checks.

## Retained Ravenna measurement

Input: conversion `52c3ac62-68f3-491c-ae83-3916aff251a1`, its saved `page-0/events.json`
and `settings.json`. The baseline reproduces its HPGL byte for byte.

| Measurement | Before | After |
| --- | ---: | ---: |
| All pen-down runs | 8,928 | 6,585 |
| Green runs | 3,478 | 1,901 |
| Pen-down travel | 40.703 m | 41.644 m |
| Pen-up travel, converter origin | 18.771 m | 17.830 m |
| Total Y travel | 26.595 m | 26.595 m |
| Routing plus stitching | 0.04 s | 0.20 s |

This removes 2,343 lifts (26.2%), including 1,577 green lifts (45.3%).
Of those joins, 374 share an exact endpoint and 1,969 need a short connection.
941 mm of existing travel changes from pen-up to pen-down; total travel does not
increase. These are software measurements, not a timed physical replot.

The original logical coverage is byte-identical. All 16,293 source paints retain
their footprint/required-coverage checks. The largest connector escape is floating
roundoff, 7.2e-15 pt². A separate Shapely audit of the complete decoded physical
runs finds zero area outside the allowed masks for all five inks. Integer segment
multisets confirm every original segment survives exactly, including duplicates.
Raw GEOS unions of centerlines can introduce floating intersection nodes; exact
integer segments are the preservation check. Polygonal buffer approximations
also tessellate caps and joins differently and are not proof of a lost physical
stroke. Evidence is retained in the development worktree's ignored `build/`:
`baseline/`, `optimized/`, `footprint-audit.json`, and `allowed-*.wkt`.

Tests cover shared endpoints, overlapping caps, narrow corridors and clear gaps,
different inks, grid rounding, cross-paint attribution, clips/later white paint,
exact HPGL readback and tampering. PDF-boundary tests independently decode run
counts and mileage and compare full and preview output. Run `cargo test --locked`
and `.venv/bin/python -m unittest discover -s tests -p test_pdf_driver.py` after
building the release converter.

## Installed preview

The dev converter was replaced without restarting Plotline Studios. Conversion
`66876933-2971-481d-b0ff-560a65eba356` uses the identical retained events/settings
and produces byte-identical HPGL to the audited optimized output. The refreshed
[Ravenna plot](http://dev.iac.jonh.net:9191/#34c36bf8-1975-4cac-8ac1-1c804a14c4e5)
retains the previous job's complete paper/pen/speed/force setup. An offline
prepared-file export confirms 6,585 runs, including 1,901 using physical slot 6
(green), so registration and serial preparation preserve the joins. All 66 Rust
and 17 PDF-boundary tests pass, together with formatting and Clippy checks.
No physical replot was started. Implementation: `9f81b7d`.
