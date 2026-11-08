# Geometry regression inputs

`house-width-corner.json` preserves the extracted stroke for OSM building
`way/220914345` from the HP7585 Letter proof. Its source width is 0.65 pt and its
original page coordinates deliberately preserve the failing HPGL grid phase.
The old area-erosion check rejected a single 0.4 pt pass because of a
2.6202e-7 pt² corner sliver, producing three outline traversals.

`house-width-clipped.json` preserves building `way/220914326` and the later
black road casing that clips its left edge. This tests local adjustment separately
from the corner correction. The test also derives a variant with an original PDF
clip and a later white cut.

Both are minimized from `build/ravenna-letter-hp7585/input.events.json`, composed
from the frozen `data/roosevelt-complete-2026-10-29.osm.gz` snapshot using
`styles/ravenna-letter-hp7585.json` and the README's explicit bbox. Map data:
OpenStreetMap contributors, ODbL 1.0. These are converter events, not archived
controller commands. `transformed-glyph.json` is the existing native text fixture.

`house-width-grid-neighbor.json` and `house-width-grid-road.json` preserve the
two substantial northwest Letter fallback houses, `way/220914218` (paint 76)
and `way/220925044` (paint 386). Both retain the original page clip and every
later paint intersecting the target's allowed envelope. The first overlaps the
white interior and red outline of `way/220914411`; the second overlaps building
`way/220925062` and service road `way/6555865` (black casing and white core).
Event indices and source attributions are remapped together.

`woodland-rotation-hole.wkt` retains one 23-vertex hole from the Washington
State preview failure of 2026-11-06, with a synthetic rectangular exterior.
It comes from OpenMapTiles/OpenStreetMap landcover/wood (OpenStreetMap
contributors, ODbL 1.0), centered at -120.7735682634974, 47.32361647286752
and scale 1:600,000. The source polygon is valid, but floating-point rotation
by 45 degrees makes the narrow tip cross itself and previously broke both
forest and hillshade hatch clipping. Keep full coordinate precision and the
ground origin: translating or rounding this fixture can hide the failure.

These use the same OSM snapshot, `styles/ravenna-northwest-letter-hp7585.json`
and its README bbox. Conservative local candidates missed 7.304e-6 and
1.050e-3 pt² after HPGL rounding. One adjacent-grid endpoint correction per
house meets the existing coverage/occlusion contract. Tests also translate
the complete fixtures through several grid phases and constrain decoded mileage.
