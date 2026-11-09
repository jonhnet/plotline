# Pattern Studio

## Crossing strokes

New exports use **Allow overlapping strokes** for continuous curves, attractor
traces, branching line art, flat tilings and recursive outlines. The explicit
family lists live in [`patterns/print-policies.json`](../patterns/print-policies.json).
This covers 33 families, including Spirograph, Lissajous, Harmonograph,
Guilloché, Lorenz and Rössler. Tetrahedra opt in only in **All edges · wireframe**.
Depth drawings, woven crossings and layered/density contour families retain
paint-order cutting. Unknown future families retain cutting until reviewed.

The flag travels in the PDF's print intent. In PDF Studio, **Allow overlapping
strokes** can be changed before generating a new preview. White knockouts,
filled shapes, images and page clipping still apply. Crossings can mix inks;
the option keeps existing paths continuous but does not join separate paths or
remove deliberate generator gaps. Earlier prepared exports remain immutable;
change their conversion choice in PDF Studio, or prepare a new pattern export.

The three-ink default Spirograph regression produces 8 continuous pen-down runs
for 8 authored curves, versus 2,633 runs with cutting. Whole-pen containment
still passes. Run `node --test tools/pattern-print-policies.test.mjs` and
`cargo test --locked --test overlap` for the policy/geometry checks. For the
full browser handoff, run `python3 controller/scripts/dev-server.py run
--command node controller/scripts/pattern-overlap-smoke.mjs`; the helper stops
the isolated Web/Map processes when the check finishes.

Pattern Studio is Plotline's authoring workspace for fractals and adjacent
mathematical drawings. The name intentionally includes non-fractal patterns.
Jon requested the complete catalog below on 2026-11-05. Forty-seven generators are
implemented, covering all fifteen broad families; other candidates
remain planned. No generator starts physical plotting.

## Catalog to revisit

| Family | Candidates | Drawing / ink opportunities |
| --- | --- | --- |
| Recursive curves | Koch snowflake, dragon, Lévy C, Minkowski | Replacement rules, nested iterations, colored sections |
| Space-filling curves | Hilbert, Peano, Gosper | Continuous paths, rounded corners, sections / quadrants |
| Plants and branching | Trees, ferns, coral, imaginary botanical forms; deterministic, stochastic and context-sensitive L-systems | Stems, branches, leaves, flowers; editable grammars |
| Gaskets and carpets | Sierpiński triangles and carpets, recursive squares, nested polygons | Outlines, holes, subdivision rules, generation colors |
| Circle packings and inversion | Apollonian gaskets, nested tangent circles, inversion patterns; later Kleinian / Schottky constructions | Circle size and ancestry |
| Recursive solids | Menger sponge, Sierpiński tetrahedron, recursive architecture | Hidden edges, camera, surface hatching, face / edge direction |
| Complex-plane sets | Mandelbrot, Julia, Burning Ship, Newton basins | Smooth escape contours, basin boundaries, orbit patterns, density hatching; Mandelbrot-to-Julia navigator |
| Dynamical systems | Lorenz, Rössler, Clifford and de Jong attractors | Continuous trajectories where defined; density contours or stippling for point distributions (do not join unrelated chaos-game points) |
| Iterated function systems | Barnsley fern, affine recursive shapes, user-defined transforms | Recursive outlines or sampled density |
| Growth | Diffusion-limited aggregation, branching crystals, lightning / dendrites | Seeded growth, connected branch paths, growth stages |
| Rolling circles | Spirographs: hypotrochoids and epitrochoids | Radii, pen offset, phase, multiple interleaved curves |
| Oscillations | Lissajous curves, harmonographs | Frequency ratios, phase, damping, component pens |
| Botanical arrangements | Phyllotaxis / sunflower spirals | Divergence angle, radial growth, seed shapes, spiral families |
| Tilings | Penrose, Truchet, other substitution tilings | Tile types, edge motifs, shared-edge deduplication |
| Other adjacent drawings | Knot projections, woven curves, programmable geometric grammars | Over/under clearing, strands, reusable small programs |

## Gallery

Koch snowflake, dragon, Hilbert, branching plant, Sierpiński triangle,
Apollonian packing, Julia contours, an experimental Menger sponge, Spirograph,
Lissajous curves, Harmonograph, Phyllotaxis, Penrose tiling, Truchet tiling,
Mandelbrot contours, Burning Ship, Newton basins, Lorenz and Rössler attractors,
Barnsley fern, branching crystals, woven knots, Gosper curves, diffusion growth,
Sierpiński tetrahedra, Lévy C curves, Minkowski curves, Sierpiński carpets,
Clifford density contours, de Jong density contours, Peano curves, nested
polygons, Pythagoras trees, branching lightning, circle-inversion lace,
Ammann–Beenker tiling, organic trees/coral, recursive architecture, star lattices
and interlace, guilloché medallions, standing-wave contours and reaction–diffusion
contours, flowers, Schottky circle fractals, pinwheel tiling, hatched polyhedra
and curated L-system plants (47 generators).
**Browse families** groups the gallery into seven tabs: Curves & waves,
Plants & growth, Tilings & weaving, Recursive shapes, Complex & inversion,
Attractors & fields, and 3D forms. The family selector uses those same groups.
Every generator has exactly one browsing home, defined in `patterns/web/groups.js`.
Tabs show family counts, remember the last group in session storage, and support
Left/Right/Home/End keyboard navigation. The group is a browsing preference, not
a recipe edit. Tabs wrap on small screens; only the thumbnail panel scrolls.

Gallery thumbnails are representative 720×460 PNGs generated at build time by
`tools/build-pattern-thumbnails.mjs`, using the same JavaScript generators as
the editor and the existing MuPDF npm dependency for rasterization. Examples
use a fixed six-color palette, 0.6 mm pens, landscape 11×17-inch paper and
symmetric 15 mm margins. Complex contours use a bounded 280-cell illustration.
These are examples, not previews of the viewer's current carousel or paper;
choosing a family still creates real artwork using the normal document defaults.

`controller/build.rs` builds and embeds every thumbnail and its URL manifest.
Cargo watches the generator sources, builder and npm lockfile, so ordinary
builds/deployment refresh the examples without manual asset steps. The build
needs Node 20+ and `npm ci` in the checkout root, with no additional dependencies
or browser installation. Generated images live in Cargo's ignored output tree.
For inspection, run `node tools/build-pattern-thumbnails.mjs build/pattern-thumbnails`.

The gallery creates all cards immediately and requests the visible group's
images together, with no geometry workers or sequential generation queue.
Unvisited groups don't fetch their images; errors don't block other cards and
retry when the group reopens. Image URLs contain a SHA-256 of their PNG bytes
and receive one-year immutable caching. The manifest and normal assets keep
`no-store`, so changed images get fresh URLs after an update. Closing/reopening
or reloading reuses the browser cache. Named documents, their saved thumbnails
and immutable exports are unchanged.

The gallery-image acceptance check covers every PNG's content hash and visible
ink, untouched document state, zero new geometry workers while browsing,
keyboard/mobile navigation, failed-image retry and real HTTP cache reuse after
reload. Chromium and Firefox pass. On the dev machine, the first 12-card group
takes about 62/86 ms respectively, with cached reloads about 30/62 ms; the previous
live Chromium implementation took about 462 ms on the same machine. These are
local browser measurements, not laptop/network guarantees. The complete image
set is about 4.9 MB; only visited groups transfer, and subsequent visits reuse
cached bytes. The existing 78 controller tests also pass.

Presets are dense at ordinary paper sizes; controls use useful discrete steps
around those defaults. Koch, dragon, Hilbert, Sierpiński, Julia, Mandelbrot and
Burning Ship, the five natural-pattern generators, and the adjacent curves use an ordered
ink sequence, initially containing every usable loaded pen with a known
width. **All loaded** copies the current usable pens; click discs to select a subset,
use arrows to reorder, or add custom document inks. The sequence repeats through
layers, curve sections, generations or contours. It is not limited to eight inks.
Branching plants retain Stem / Branches / Leaves; circle packings retain size roles,
sponges retain X / Y / Z edge roles, and Newton has one role per attracting root. A monochrome fallback remains editable.
Existing saved bindings survive until the operator changes the sequence.
Refreshing inventory never silently changes document inks or physical inventory.

The six adjacent generators added on 2026-11-06 use the same worker, gallery,
document history, ink tray, page fitting, and PDF/intent handoff:

| Generator | Controls and ink meaning |
| --- | --- |
| Spirograph | Inside/outside rolling, curated gear ratios, pen offset, interleaved curves and twist. Sequence colors follow curves; paths close exactly after their full gear period. |
| Lissajous | Frequency ratio, phase, curve count, phase step and aspect. Sequence colors distinguish interleaved closed curves. |
| Harmonograph | Pendulum ratio, detuning, damping, cycles and phase. One four-oscillator trace changes ink along its length; successive color sections share endpoints. |
| Phyllotaxis | Seed count, divergence angle, ellipse/leaf/diamond outlines, seed size, aspect and twist. Sequence colors follow seed order. |
| Penrose | Robinson-triangle subdivisions of a decagon, triangle or rhombus patch, with optional nested outlines. Roles are tile edges, thin-tile motifs and thick-tile motifs. Shared edges are emitted once; paired half-tiles become rhombs, while unpaired boundary half-tiles close the finite patch. |
| Truchet | Columns, rows, parallel strands, band spread and seeded arrangement. Quarter-circle arcs join into continuous strands across tile boundaries, with one sequence ink per complete strand. |

Ratio, rolling mode, seed shape and patch choices have named selectors rather
than unlabeled numeric modes. Other controls retain the shared stepped sliders.
Five of the six new generators start with every usable loaded ink; Penrose starts
with three feature roles. Curves use 384 samples per fastest oscillation; seed
outlines use 36 segments and Truchet quarter-arcs use 24. This is fixed geometric
sampling for standard sheets, not Julia's physical-error sampling criterion.
Penrose reveals complete lower subdivision levels before refining to the selected
one. Every new generator yields progress/batches and uses the shared memory guard.

Mathematical references for the Penrose construction:
[Treibergs, University of Utah](https://www.math.utah.edu/~treiberg/PenroseSlides.pdf)
and [Preshing's illustrated half-tile convention](https://preshing.com/20110831/penrose-tiling-explained/).
The implementation is original JavaScript; no new runtime dependency was added.

### Attractors, fern, crystals and weaving

The next five use the same documents, paper, ink sequence, preview worker and
PDF/intent path. **Browse families** includes their live thumbnails. Defaults
use every usable loaded ink; **All loaded**, subsets and ordering work as before.
No additional daemon, library or rendering service is involved.

| Generator | Controls and ink meaning |
| --- | --- |
| Lorenz attractor | Trace length, convection, azimuth and elevation. One continuous orbit, with successive colored sections sharing endpoints. |
| Rössler attractor | Trace length, spiral shape, azimuth and elevation. The same continuous-orbit interpretation; small shape steps near the preset avoid the neighboring nearly periodic retracings found during review. |
| Barnsley fern | Generation limit, stem curl, leaf spread and smallest twig. The affine IFS draws transformed stem intervals, colored by generation; it never connects unrelated chaos-game samples. |
| Branching crystal | Branch generations, arm count, branch pairs, angle, reach and variation seed. One seeded dendritic arm is repeated with exact rotational symmetry; colors follow arm and generation. This is a decorative branching construction, not a diffusion or ice-growth simulation. |
| Woven knots | Six torus-knot presets, ribbon strand count/width, loop depth, elevation and crossing clearance. Sequence inks follow parallel strands; the lower ribbon is interrupted at projected crossings. |

Lorenz and Rössler use deterministic RK4 integration with steps of 0.008 and
0.025 time units, after 20 and 120 units of settling. These are finite sampled
trajectories, not error-certified curves or a promise of arbitrary-time numerical
accuracy. Their defaults have about 15,000 and 12,000 vertices. Fern recursion
has both a generation limit and a minimum twig length in its mathematical units;
the minimum can make later generations identical. The crystal stops twigs below
0.012 of the original arm length and checks the memory allowance while growing.

Knots use 240 segments per fastest winding, projected depth and a spatial grid
to find neighboring segments. Gaps clip the lower strand against round-ended
capsules around the upper ribbon, including both pen radii and the selected
clearance in widest-pen diameters. The frame is fixed by the uncut ribbon, so
changing gap size cannot rescale the drawing. Paper size, composition size,
rotation, and role pen widths invalidate these gaps. Gallery, preview and export
use the same widths. This is a sampled ribbon projection, not a general solid
renderer; extreme band widths/cameras can create merged or hidden sections.

All five reveal a complete coarse drawing before continuing automatically:
40/100 time units for Lorenz/Rössler, 12 fern generations, two crystal generations,
or three knot strands. Crystals then complete each intervening generation.
Later batches paint progressively over the coarse guide. **Cancel** keeps a
completed drawing and restores its trace length, generations or strand count;
Undo restores the requested settings. Cancelled partial batches never become PDF
exports. Width-dependent checkpoint compatibility is shared with Newton.

Mathematical references: [Sprott's published chaotic systems and parameters](https://sprott.physics.wisc.edu/chaos/comchaos.htm),
[Treibergs' IFS lecture](https://www.math.utah.edu/~treiberg/FractalSlides.pdf), and
[Wolfram's torus-knot parameterization](https://reference.wolfram.com/language/ref/KnotData.html.en).
The implementations are original JavaScript, including the seeded crystal rule.

### Remaining menu, in priority order

Jon clarified on 2026-11-06: favor ready-made drawings with understandable knobs;
editable mathematical languages come **last**, not immediately after touching
every broad category. There is plenty left within the existing families:

1. More recursive curves and substitution tilings. Peano, nested polygons,
   branching recursive squares, Ammann–Beenker and pinwheel tilings are delivered.
2. More botanical forms and recursive solids. Space-colonization trees/coral,
   stylized lightning, and stacked temple/city architecture are delivered;
   flowers, four curated L-system plants and hatched convex polyhedra are delivered;
   other fern forms and different architectural rules remain possible.
3. Other curated point attractors and affine IFS, using density contours or short
   marks for distributions rather than fictitious connecting trajectories.
4. Richer inversive/Kleinian/Schottky constructions (circle-inversion lace is
   delivered, with classical paired-circle Schottky orbits added on 2026-11-07);
   complex-plane orbit/density treatments and more woven curves.
5. Finally, editable L-system grammars, affine transforms, formulas and small
   geometric programs, with validation and a learning-oriented interface.

The catalog remains the full inventory; this priority list does not delete its
remaining candidates or commit to a particular next batch.

### Gosper, diffusion growth and tetrahedra

Jon approved this three-generator batch on 2026-11-06. All use the existing
gallery, loaded-ink sequence, paper fitting, worker/checkpoints, saved documents,
and exact PDF/intent handoff. They add no dependency or service.

| Generator | Controls and ink meaning |
| --- | --- |
| Gosper curve | Detail 2–6 (default 4), corner rounding. One continuous hexagonal replacement curve, split into consecutive ink sections with shared endpoints. Each detail is oriented to the same start-to-end direction. |
| Diffusion growth | 500–6,000 particles (default 2,000), Point/Line starting seed, repeatable Variation. Actual off-lattice diffusion-limited aggregation, represented by its attachment tree. Inks follow angular sectors around the seed. A line seed grows on both sides. |
| Sierpiński tetrahedra | Detail 1–6 (default 4), azimuth, elevation, Visible edges/All edges wireframe. Four half-size corner tetrahedra per generation. Consecutive groups of small tetrahedra receive sequence inks. |

Gosper traverses the two replacement rules without constructing an exponential
command string. Rounding uses three straight segments per quadratic corner;
the sharp setting retains exactly `7^depth` segments. This is geometric sampling,
not a physical-error guarantee for arbitrarily large paper.

Diffusion growth uses an incrementally updated quadtree for nearest-particle
queries. Walkers take uniformly directed steps to the boundary of a known-empty
circle, rather than testing each tiny step against every particle. This is a
finite Brownian walk-on-circles approximation: unit particle separation, attachment
at distance at most 1.01, launch at aggregate radius + 5, and relaunch beyond
three radii + 20. A deterministic budget of 10,000 walking steps per requested
particle fails explicitly if exhausted. The stored parent is the actual attachment;
there are no decorative connections between unrelated samples. Degree-two chains
of one ink are joined, preserving each attachment exactly once. More particles
retain the exact earlier growth in mathematical coordinates; final page fitting
can rescale it. Particle diameters describe the simulation, not physical pen widths.

Tetrahedron visibility uses analytic intervals along projected edges, clipping
against the linear inside-triangle and depth inequalities. A screen-space grid
limits comparisons to nearby faces; coplanar edges remain visible. This removes
solid occlusion before the stroke PDF is written. It is centerline hidden-line
rendering of these tetrahedra, not a general finite-pen solid renderer. The selected
pen width stays constant on paper and whole pen footprints remain inside the
printable area. Wireframe deliberately retains hidden edges.

Gosper first completes detail 2, diffusion first completes 500 particles, and
tetrahedra complete successive details starting at 2. Later geometry arrives in
batches; Cancel retains a completed drawing and restores its detail/particle
control. Undo restores the requested settings. All three retain the shared
memory allowance. Defaults and every adjacent control setting have vector review
sheets under ignored `build/spatial-review/`.

References: [Prusinkiewicz and Lindenmayer, The Algorithmic Beauty of Plants,
figure 1.11](https://algorithmicbotany.org/papers/abop/abop.pdf),
[Witten and Sander's DLA model](https://doi.org/10.1103/PhysRevLett.47.1400),
and [the tetrix construction](https://mathworld.wolfram.com/Tetrix.html).
Implementations are original JavaScript; reference publications supply ideas,
not copied implementation code.

### Inversion lace, octagonal tiling, organic growth and architecture

Jon approved all four on 2026-11-06, bringing the gallery to 38. Original
implementations are in `patterns/web/ornament.js`; no new runtime dependency or
service. All four retain the shared progress worker, exact source snapshots,
physical paper fitting and PDF/intent export.

| Generator | Controls | Ink assignment |
| --- | --- | --- |
| Circle-inversion lace | Generations 2–8 (default 5), symmetry 3–8 (default 5), circle spacing 0–0.2, smallest-circle radius 0.001–0.008 | Loaded ink sequence by sector and generation |
| Ammann–Beenker tiling | Detail 1–5 (default 3), Octagon/Circle/Square patch, 0–4 nested motifs (default 2) | Tile edges, square motifs, diamond motifs |
| Organic trees & coral | Branch density 1–5 (default 3), Broad tree/Tall tree/Coral fan, spread, sideways growth bias, seeded variation | Loaded ink sequence by branch ancestry |
| Recursive architecture | Detail 1–4 (default 3), Temple/City, height, setback, azimuth/elevation, seeded variation | X, Y and vertical edges |

**Inversion lace.** Mirrors have centers on the unit circle, with radius
`sin(pi/symmetry)/(1+spacing)`, so mirror interiors are disjoint. Seeds include
the mirrors and their central/enclosing tangent circles. Inversion maps a circle
analytically; immediate inverse steps are excluded and duplicate circles are
removed. Both the generation limit and minimum radius stop expansion. Curated
seeds avoid circles through inversion centers; that singular case is detected,
not sent through division by zero. Circle polylines have mathematical sagitta
at most 0.0002, with at least 16 segments, and explicit identical closing points.
This fixed sampling is not a physical-error guarantee at arbitrary paper sizes.
The drawing is a finite circle orbit, not a claim to render the full limit set.

**Octagonal tiling.** Integer lattice points in four dimensions project onto
physical and internal planes. The latter must lie in the centered octagonal
window obtained from a unit 4-cube. Bounding two integer coordinates with the
window inequalities avoids a full four-dimensional scan. Four accepted vertices
of an elementary lattice face produce a unit square or a 45-degree rhombus.
Detail increases the patch radius (`3+3*detail`), then normal page fitting scales
the complete drawing. The patch selects whole tiles by center and retains the
central edge-connected component, avoiding stranded corner tiles. Exact lattice identities keep the crop connected. Shared geometric edges
are drawn once; motifs are scaled
copies strictly inside each tile. Patch boundaries follow the tiles, rather
than slicing them into partial polygons. The independent GEOS audit uses the
unmodified coordinates, without buffering/snapping to conceal cracks.

**Organic growth.** A deterministic set of `400*density` targets fills the chosen
elliptical crown. Each target steers its nearest existing branch node within an
influence radius of 65 units. Branch steps are 3 units; targets within 4.5 units
are consumed, and proposed nodes within 1.65 units of existing nodes are skipped.
The shared quadtree handles nearest queries. Growth stops at no new branches,
no targets, or 240 iterations; reaching every target is not promised. Sideways
bias and a small upward bias shape the growth. Every node records its actual
parent, yielding a connected tree; joining degree-two chains preserves every
edge exactly once. This is a 2D artistic adaptation of space colonization, with
constant physical pen widths and no branch-thickness/foliage model. Changing
branch density changes the target distribution and can change the whole tree.

**Architecture.** Recursive box towers sit on their parent roofs, with disjoint
sibling footprints inside the parent. Temple uses five towers per roof, including
a taller central tower; City uses four and more varied heights. The camera is
orthographic, with azimuth/elevation controls. All box faces become projected
triangles; a 2D spatial grid rejects unrelated face/edge comparisons. Remaining
occlusion intervals use the shared analytic half-plane/depth clipper. On the
default temple, 142,991 comparisons replace 3,504,384 possible pairs. Visible
centerlines retain constant paper pen widths. Numerical endpoint fragments below
`1e-7` projected model units are omitted; this removes tolerance specks, not a
physical pen-width simplification. This renderer handles these boxes, not general
3D scenes or perspective cameras. Broad pens can merge close features at high
detail; these four families do not claim a physical crowding filter.

Complete coarse checkpoints are generation 2 for inversion and detail 1 for the
other three. Cancel retains the completed drawing and restores its control;
Undo restores the requested settings. Default/neighbor contact sheets and all
categorical presets are in ignored `build/ornament-review/`.

Construction references: [circle-inversion limit sets](https://www.math.union.edu/research/circleinversions/InversionLimitSets.html),
[Ammann–Beenker cut-and-project construction](https://arxiv.org/html/2308.07701v2),
[Runions, Lane and Prusinkiewicz: space colonization](https://www.algorithmicbotany.org/papers/colonization.egwnp2007.html).
The implementation reuses mathematical ideas, not source code from these works.

### Flowers, Schottky circles, pinwheel tiles, shaded solids and prepared plants

Jon approved this batch and curated L-system presets on 2026-11-07. The five
new families bring the gallery to 47. They share existing documents, ink roles,
worker progress, paper fitting and PDF/intent exports; no dependency or service
was added. `patterns/web/collection.js` registers their independent modules.

| Generator | Controls and ink meaning |
| --- | --- |
| Flowers | Daisy / Lotus / Starflower, 6–24 petals, 1–4 whorls, petal width, stem bend and 0–5 leaf pairs. Inks cycle around petals and whorls; stems/leaves use the first ink. |
| Schottky circle fractals | 2–3 circle pairs, 2–8 generations, initial circle spacing, 0–180° pair twist and smallest-circle cutoff. Inks follow root sectors and generation. |
| Pinwheel tiling | Detail 1–5, triangular or rectangular patch, 0–3 nested motifs. Roles are shared tile edges and the two motif handednesses. |
| Hatched polyhedra | Cube / Octahedron / Icosahedron, hatch density and angle, optional crosshatching on dark faces, azimuth, elevation and 1–3 copies. Roles are edges, light faces and shaded faces. |
| L-system plants | Branching herb / Bush / Fern / Tiered tree, 2–6 generations, branch spread, branch reach, lean and optional leaf tips. Roles are trunk, branches and leaves. No grammar editor or executable input. |

**Flowers.** Closed two-sided cubic petal outlines repeat rotationally around
successively smaller whorls. The three forms change petal control points and
width. Curved stems stop below the bloom, and paired leaves start on the stem.
These are stylized ornamental outlines; adjacent whorls remain visible where
they overlap. They do not claim a botanical simulation or petal occlusion model.

**Schottky circles.** Equally sized disjoint disks lie on a unit ring. Each
holomorphic generator has the form `target + A / (z − source)`, pairing opposite
disks with `|A| = radius²`; the phase of `A` is Pair twist. The inverse swaps
source and target with the same `A`. Reduced words exclude immediately inverse
generators, and each child is transformed analytically into its parent disk.
Generation and minimum-radius limits both stop refinement. The drawing shows
finite circle orbits of a classical Schottky construction, not the exact infinite
limit set. Circle sampling targets a model-space sagitta of 0.0002, with at least
16 segments. The cutoff is relative to the unit ring, not a physical pen filter.

**Pinwheel.** Every 1:2 right triangle subdivides into five similar triangles,
including both handednesses. All vertices retain integer coordinates with a
common `10^detail` denominator. Collinear sides are grouped by exact integer
line identity; interval events split partial shared edges and T-junctions, so
the boundary network is drawn once. Degree-two segments join, while inset
triangles remain inside their own convex tile. Nested motifs can be disabled.
The rectangular patch has two initial triangles. Maximum detail contains 6,250
tiles; the finite patch exhibits many orientations rather than the infinite
orientation distribution of the mathematical tiling.

**Hatched solids.** Convex hull faces have outward normals. Only front-facing
faces and their unique edge network are drawn. Hatches lie in each face's own
3D plane, with density varying under a fixed illustrative view-space light;
dark faces optionally receive a perpendicular second pass. Their page width
stays constant. Each projected hatch is clipped to the convex face inset by
half its own pen width plus half the edge pen width, accounting for round caps.
Paper size, composition and role widths therefore invalidate physical geometry.
The fixed unit-sphere envelope keeps density checkpoints anchored. Copies are
separated by 2.25 model units, preventing mutual projected overlap. This is a
convex-polyhedron study, not arbitrary scene occlusion or recursive surface
shading. Fine faces may lack room for a hatch while their outline remains.

**Prepared L-systems.** Four bracketed mathematical rules from the classic
plant examples are interpreted by a streamed recursive expander; no exponential
command string is allocated. The turtle saves position, angle and branch depth
at a branch, then restores without drawing a return path. Branch reach scales
forward length by stack depth, Branch spread multiplies the preset's angle, and
Lean applies a final shear. Identical whole edges are emitted once and degree-two
paths join. Leaf tips attach to actual terminal graph vertices. This is planar
line art; partial branch overlaps remain visible. The normalized square frame
stays fixed between completed generation checkpoints.

Initial completed checkpoints are one flower whorl, two Schottky generations,
pinwheel detail 1, hatch density 10 and plant generation 2. Cancel restores the
completed control value; the five families retain the normal Undo behavior.
Default and neighboring-control contact sheets are in `build/collection-review/`.

Mathematical references: [Bielefeld's Conway–Radin pinwheel rule](https://tilings.math.uni-bielefeld.de/substitution/pinwheel/),
[Southampton's paired-circle Schottky construction](https://www.southampton.ac.uk/maths/research/projects/schottky_groups_and_uniformizations_of_closed_surfaces.page),
and [The Algorithmic Beauty of Plants, chapter 1, figure 1.24](https://www.algorithmicbotany.org/papers/abop/abop-ch1.pdf).
The implementations are original; no third-party implementation code is copied.

### Stars, guilloché, standing waves and reaction–diffusion

The next four bring the gallery to 42. All use the shared ink sequence, worker,
source documents, page fitting and PDF/intent handoff. Implementation is original
JavaScript in `patterns/web/textile.js` and `patterns/web/wave-fields.js`; there
are no new dependencies, services or remote computation.

| Generator | Controls and construction |
| --- | --- |
| Star lattices & interlace | Detail 1–6, squares / hexagons / octagons-and-squares, star angle 25–75°, crossing clearance 0–3 pen diameters. Hankin rays join motifs at shared tile-edge midpoints. |
| Guilloché medallions | Rosette / double rosette / rounded border ribbon, 4–24 lobes, wave depth, 12–72 lines and phase twist per line. Each line is explicitly closed; inks cycle by line. |
| Standing-wave contours | Five mode pairs, relative balance, 1–9 contour bands, band spread and sampling detail. Inks cycle by contour level. One band follows the near-zero nodal set. |
| Reaction–diffusion contours | Winding stripes / spots / cellular presets, broad / medium / fine features, 1,000–6,000 growth steps, 1–7 contour bands and seeded variation. Inks cycle by concentration level. |

**Stars.** Shared midpoint junctions connect opposite edges; degree-two tips
continue the strand. Binary constraints assign alternating over/under crossings
around every closed strand, including its seam. Contiguous color sections change
only at motif tips, keeping each crossing in a single ink. The overpass's two
segments are capsules; underpasses are cut using both actual pen radii plus the
requested clearance times the widest selected pen. Cuts follow bends when wide
pens reach a neighboring tip. This is local crossing clearance, not general
crowding removal between all nearby motifs. High detail and broad pens can still
merge non-crossing features. The normalized template frame stays fixed across
detail, angle, gap and ink changes. Paper/size/width changes invalidate physical
geometry exactly as with woven knots; color-only changes keep the geometry.

**Guilloché.** Polar waves form one or two frequencies around nested radii. The
border uses an arc-length rounded rectangle and its outward normal; its 0.45
model-unit corner radius exceeds every permitted inward displacement. Sampling
uses 48 points per lobe and an identical closing endpoint. Its envelope frame
stays fixed while line-count checkpoints refine. This is a decorative wave
construction, not a mechanical simulation of a particular rose engine. Dense
crossings are intentional; there is no interlace occlusion or crowding filter.

**Waves.** The field is `cos(mπx) cos(nπy) − balance cos(nπx) cos(mπy)` on
`[0,1]²`, sampled at 96 times Detail cells per side (96–480). The shared marching
squares extractor joins isolines across cell boundaries. A `1e-10` bias replaces
the exact zero level to resolve grid vertices/saddles deterministically. These
are Chladni-inspired standing-wave drawings, not a plate-elasticity solver or
measured vibration modes. Sampling does not automatically follow physical pens;
large paper can reveal polygonal approximation. The square page frame is fixed.

**Reaction.** Gray–Scott uses simultaneous explicit Euler steps (`dt=1`), a
periodic five-point Laplacian and diffusion rates `Du=.16`, `Dv=.08`. The presets
use `(feed, kill)` = `(.035,.060)`, `(.030,.062)` and `(.022,.051)`. Jittered seed
disks initialize deterministic variation. Feature size selects 96², 128² or 160²
simulation cells; changing it changes the physical domain, not just sampling
quality. The worker reuses its exact 1,000-step simulation for the requested
later stage. Parameter changes or shorter growth restart from the seed. Only
one state is retained, and contour extraction never mutates it.

Periodic cubic interpolation at 2× grid resolution smooths contour extraction;
interpolation overshoot is clipped to `[0,1]`. Levels span 25–85% of peak
concentration (55% for one band). Export contains joined vector contours, with
open paths at the square boundary; periodic simulation does not join pen paths
across the page. No raster hatch or stipple approximation enters the PDF.
Finite simulation and seeded initial conditions limit the available patterns;
this is not an arbitrary chemical-system editor.

Complete initial checkpoints are detail 1 for stars/waves, 12 lines for
guilloché and 1,000 steps for reaction. Cancel retains that completed drawing
and restores the matching control; Undo restores the requested setting. As
elsewhere, exported palettes include only inks actually used: three concentration
bands need three inks even if the sequence contains seven. Default, neighboring
control and categorical-preset contact sheets are under `build/textile-review/`.

Construction references: [Kaplan's polygon-template star patterns](https://cs.uwaterloo.ca/~csk/other/starpatterns/),
[rose-engine guilloché](https://snagmetalsmith.org/2016/04/guilloche-engraving-2/),
[University of Toronto's Chladni demonstration](https://www.physics.utoronto.ca/nonlinear/chladni.html),
and [MIT's Gray–Scott model](https://groups.csail.mit.edu/mac/projects/amorphous/GrayScott/).
These inform the mathematics and terminology; no implementation code is copied.

### Peano, nested polygons, branching squares and lightning

The next batch brings the gallery to 34. All four use the loaded-ink sequence,
shared worker/checkpoints, paper fitting and existing PDF/intent export. The
implementation is original JavaScript in `patterns/web/recursive-art.js`, with
no new dependency or service.

| Generator | Controls and construction |
| --- | --- |
| Peano curve | Detail 2–5 (default 4), corner rounding 0–0.4. Streamed ternary walk visits every cell in a `3^detail` square once. Continuous color sections share endpoints. |
| Nested polygons | 3–10 sides, layer limit 10–100 (default 50), edge step 5–45%. Each new vertex advances that fraction along its parent's edge. Convexity keeps each polygon inside its predecessor; colors follow layers. |
| Pythagoras tree | Detail 3–12 (default 9), branch angle 20–70°. Square outlines grow from the two legs of a right triangle above the parent square; child areas sum to parent area. Inks follow generations. |
| Branching lightning | Detail 4–11 (default 8), 6–42 forks (default 18), jaggedness 0.08–0.32, seeded variation. Bounded perpendicular midpoint displacement forms the spine and each attached fork. Inks follow branches. |

Nested polygons stop when an edge becomes shorter than `1e-5` mathematical
units; the layer knob is a limit. Pythagoras emits each coincident whole edge
once, retaining its earliest generation's ink, and joins degree-two paths. It
is a drawing of overlapping square outlines, with no solid-fill occlusion or
partial collinear-overlap removal. Lightning forks begin at actual vertices of
already-generated branches, including earlier forks, and each branch is drawn
once. This is stylized seeded geometry, not a physical electrical simulation.
Increasing detail retains the spine's coarser vertices; fork attachment choices
can shift with the available vertex count. None of these generators claims
physical pen-aware crowding removal; high detail can merge with wide pens.

Initial complete checkpoints are detail 2 for Peano, 10 layers for nested
polygons, detail 3 for Pythagoras, and detail 4 for lightning. Cancel retains the
completed drawing and restores that control; Undo restores the requested value.
All defaults and their neighboring controls have vector contact sheets under
ignored `build/recursive-review/`.

Carpet recipes now discard the old `round` parameter when opened or reverted.
An affected saved document becomes a modified working copy until Save. Earlier
exports remain byte-identical, while an old rounded HPGL receipt cannot enable
Open plot for the corrected square preview. Historical source links open the
corrected working recipe with an explanatory notice and reuse its authoring tab.

Construction references: [Peano replacement rules, appendix I](https://www.davidsalomon.name/DC2advertis/AppendI.pdf),
[Pythagoras square construction](https://www.math.uci.edu/~mathcircle/materials/2013.11.13MSMC.pdf).
No implementation code is copied from these sources.

### More curves, carpets and point-density contours

Jon requested another batch on 2026-11-06. Five new families bring the gallery to
30. They reuse the existing worker, loaded-ink sequence, page fitting, source
recipes and PDF/intent export. There are no new dependencies.

| Generator | Controls and ink meaning |
| --- | --- |
| Lévy C curve | Detail 7–17 (default 12), one to four rotated copies, corner rounding. Each edge becomes two sides of a right isosceles triangle. Consecutive ink sections share endpoints. |
| Minkowski curve | Detail 1–5 (default 4), Island/Open curve, corner rounding. The eight-step quadratic replacement motif, with a closed square base for the island. Colors follow the continuous curve. |
| Sierpiński carpet | Detail 2–6 (default 4), aspect. Outlines of the removed middle ninths and the outer boundary; each opening appears once. Inks distinguish the eight first-level regions, repeating as needed. |
| Clifford contours | Four named shapes, Detail 1–4, contour count 6–18, Softness. Inks follow increasing sampled point density. |
| De Jong contours | The same controls, with four separately chosen presets of the de Jong map. These are density isolines, not lines connecting successive orbit points. |

Curve corners use three line segments per quadratic arc. The island's closing
corner is rounded too, before dividing the path into ink sections. Detail bounds
and the shared estimated memory allowance apply before expansion; the maximum
rounded drawings can exceed the obsolete 400,000-vertex threshold. These are
sampled corner constructions, not an arbitrary-paper-size physical-error bound.
Correction after Jon's review: rounded carpet corners break the recursive visual
containment. Rounding is removed from the carpet, including its outer boundary;
all holes use exact square corners. The earlier rounded artifacts remain evidence.

The point maps use simultaneous updates of the preceding x/y pair. Clifford uses
`x' = sin(a*y) + c*cos(a*x)`, `y' = sin(b*x) + d*cos(b*y)`; de Jong uses
`x' = sin(a*y) - cos(b*x)`, `y' = sin(c*x) - cos(d*y)`. Coefficients are frozen
in `patterns/web/density.js`; the UI offers curated presets rather than a formula
language. Start at (0.1, 0.1), discard 4,096 settling iterations, then deposit
`12*n*n` points on a grid with `n = 128*detail` cells per side. Bilinear deposition
preserves mass and position. Padding encloses the theoretical trigonometric bounds
plus the finite smoothing kernel. A separable Gaussian uses sigma
`0.008*n*softness`, truncated at three sigma and normalized. Thus Softness controls
bandwidth relative to the drawing, rather than changing with detail resolution.

Normalized density levels are logarithmically spaced between the 15th and 97th
percentiles of cells above density 0.05. Shared marching squares, including its
saddle decider and edge identities, joins closed loops. Loops with fewer than
eight sampled vertices are omitted. Finite sampling and smoothing are artistic
approximations, not certified invariant measures. Very close contours can overlap
with broad pens; these families do not claim Newton's physical crowding filter.
Changing paper or pens refits/recolors their retained geometry. The final drawing
fits its ink bounds uniformly into the printable area, like the other direct
generators; the PDF/intent then freezes that page placement.

Lévy first completes detail 7, Minkowski and carpet complete successive levels
starting at 2, and the two density families first complete detail 1. Cancel keeps
the best completed drawing and restores its Detail control; Undo restores the
requested settings. Default and adjacent-control reviews, plus every named shape,
are in ignored `build/curves-density-review/`.

References: [Lévy C construction](https://resources.wolframcloud.com/FunctionRepository/resources/LevyCCurve),
[quadratic edge-replacement grammar, table 1](https://researchcommons.waikato.ac.nz/bitstream/handle/10289/1319/00NM-IHW-Onlineoffline.pdf?isAllowed=y&sequence=1),
[Sierpiński carpet](https://mathworld.wolfram.com/SierpinskiCarpet.html),
[Kononovičius's Clifford model](https://rf.mokslasplius.lt/clifford-attractor/),
and [Hetrick's DrawJong discussion](https://www.mat.ucsb.edu/Masters/Michael_Hetrick_DrawJong2.pdf).
Implementations are original; no source code was copied from these references.

The workspace reuses shared Studio chrome, tabs/undo, notices/progress, preview
gestures, paper units, role-to-ink assignment, and cross-studio tab navigation.
All four Studios use `renderPreviewControls` for a − / Fit / + row and paired
Undo/Redo beneath it. Pattern's Margins extension shades the unprintable part of the sheet
and draws a contrasting dashed guide above both vectors and progressive bitmaps.
Guides are inspection-only and never enter thumbnails or exported artwork.
Generation runs in a cancellable browser worker. Most geometry is independent of
inks; changing a pen recolors/refits retained paths. Changing the number of sequence
entries changes path attribution. Complex-plane sampling depends on physical paper size,
composition and the finest selected pen, so those changes invalidate its geometry.
Paper units are an origin-wide browser preference under the shared gear in the
four Studios' document-tab rows, not document state or a generator parameter.
The authoritative print handoff is a stroke PDF with explicit layer bindings and
the existing complete PDF/ink/paper intent. The preview and PDF use the same paths.
Prepare plot goes through PDF conversion into HPGL Studio. Conversion never plots.

Keep named documents and immutable source snapshots in the service, and unsaved
workspace tabs in browser storage. Source links must retrieve the exact prepared
snapshot even after the named document changes. Show actual progress and bounded
complexity; never allow an unbounded recursive expansion to freeze the UI.

## Complex-plane drawings

The three additions use the same joined-contour implementation as Julia:

| Generator | Controls and ink meaning |
| --- | --- |
| Mandelbrot contours | Whole-set, seahorse-valley, double-spiral and satellite starting views; contour count and spacing. Sequence inks repeat through escape contours. |
| Burning Ship | Whole-set, little-ship and mast starting views; contour count and spacing. Absolute values of both components before squaring produce its folded boundary. Sequence inks repeat through contours. |
| Newton basins | Three through seven roots of `z^n − 1`; detail depth (4–20, default 8) and contour spacing. Each root has an independently assignable ink role. Finite convergence contours reveal progressively smaller basins; the former unlimited boundary pass is removed. |

**Explore** changes the complex-plane window: drag to pan, scroll or pinch to
zoom around the pointer. A gesture is one undoable edit; Escape restores its
starting window. The shared − / Fit / + controls still inspect the paper.
**Reset view** restores the selected starting view. Named starting views also
reset the window. Coordinates, width, an arbitrary Julia `c`, paper, inks and
rotation survive save/reload and source snapshots.

In Mandelbrot, **Pick Julia** then a click inside the drawing window opens a
new document tab for that parameter. The source document stays intact; the Julia
document inherits its paper, inks and composition. It shows editable real and
imaginary components instead of a misleading preset number. **Use Julia preset**
returns to the preset controls. Point selection inverts both inspection navigation
and the rotated, margin-aware paper transform. The existing Julia image orientation
is retained for saved documents; the new parameter-plane views use +Im upward.

Complex windows have fixed bounds on paper, including an empty view. Panning
never refits the ink to the page. An empty view can be navigated and saved but
cannot prepare a blank plot. Julia's legacy initial view keeps its ink-bound
fitting until a window is explicitly selected. All four families reveal completed
280- and 560-cell samples before final refinement; Cancel keeps a completed
sample and Refine detail resumes normal sampling. Sampling follows physical paper
size and the narrowest selected pen.

Escape contours join shared grid edges with a saddle decider. Newton traces
convergence levels from 2 through the selected depth, stopping orbit evaluation
two iterations beyond that horizon. Depth determines the smaller basins; spacing
determines the number of nested outlines. Sampling resolution improves accuracy
without adding an independent, deeper classification-boundary pass. Unconverged
samples remain unassigned. These are finite approximations, not exact basin
boundaries.

Newton also uses each role's physical pen diameter: contours smaller than 1.5 pen
diameters and contours with at least 90% of their estimated length crowded by existing ink
are omitted whole. A bounded raster proximity index makes this selection; the
artwork remains vector paths. Its conservative cell-error allowance prevents
near misses being counted as overlaps. The rule retains isolated crossings and
useful separated contours; it is not a guarantee that all surviving ink footprints
are disjoint. A linear pass coalesces vertices within one-eighth pen diameter,
retaining endpoints and closure. Exact duplicate segments are removed afterward.
Progress reports the number of tiny/crowded contours omitted. Role-to-width
bindings participate in geometry caching and checkpoint compatibility, including
rebinding to an already-present document ink. Raw contour batches are released
after selection instead of accumulating rejected detail.

Earlier Newton recipes receive an explicit depth derived from their previous
outermost contour level (the original default becomes depth 7); their original
saved records and immutable PDF/path snapshots remain unchanged. Save records the
new working recipe. The misleading **Contours per basin** control is replaced by
**Detail depth**. All generators coalesce steps shorter than 0.1 micrometre on
paper before preview/PDF, avoiding degenerate segments below useful PDF coordinate
precision.

This is original JavaScript with no new runtime dependency. Mathematical references:
[Lattimer's Mandelbrot/Julia introduction](https://web.cortland.edu/jubrani/RogerTalk.pdf),
[MIT's Newton iteration notes](https://ocw.mit.edu/courses/18-04-complex-variables-with-applications-fall-1999/pages/study-materials/newton/),
and [Burning Ship formula and exploration](https://mathr.co.uk/helm/AtTheHelmOfTheBurningShip-Paper.pdf).

## Reuse candidates

- [Lindenmayer](https://github.com/nylki/lindenmayer), JavaScript, MIT: richer L-system grammars.
- [ContourPy](https://contourpy.readthedocs.io/en/stable/), C++/Python, BSD-3-Clause: field contours.
- [ln](https://github.com/fogleman/ln), Go, MIT, and [larnt](https://github.com/HellOwhatAs/larnt), Rust, MIT: general 3D line rendering.
- [vsketch](https://github.com/abey79/vsketch), Python, MIT code (documentation CC BY-NC-SA): prototyping.
- [vpype](https://github.com/abey79/vpype), Python, MIT: vector processing, where it adds to existing routing.
- [Fractalshades](https://github.com/GBillotey/Fractalshades), Python, MIT: deep numerical zooms.
- [The Algorithmic Beauty of Plants](https://algorithmicbotany.org/papers/#abop): reference and inspiration; book availability is not a code-reuse license.

The first bounded generators use original formulas and the existing PDF stack;
these candidate packages are not dependencies merely by being listed here.

## Running and validation

Build and start the existing Studios server following `controller/README.md`.
Pattern Studio is at `/patterns`; it adds no daemon or Python/npm dependency.
The existing `.venv` with `requirements-native.txt` and
`requirements-contracts.txt` supplies PDF export. Runtime source is
`patterns/web/`, `controller/src/patterns.rs`, `tools/pattern_pdf.py` and the shared
`authoring-print.js` handoff client. `pattern-studio` extends the v1 authoring
application enum; existing map intents remain valid.

The controller state directory retains `patterns/documents/*.json` (names,
revision-checked recipes and PNG thumbnails) and `patterns/exports/<sha256>/`
(exact paths, source recipe, PDF and intent). These are durable user sources,
not disposable cache. The current Plotline backup root already covers this state.
Per-tab unsaved recipes and preparation receipts survive reload in session
storage, with a local-storage recovery copy. Named saves are service-side.

**Open → Import recipe** opens a downloaded JSON recipe in a new unsaved tab;
**PDF & other options → Download recipe** exports the current design. Both use
`shared/web/recipe-files.js`, also used by Maze Studio. Import preserves paper,
inks and generator settings, validates before opening, and never adopts saved
document IDs or print receipts from another installation. File size is capped
at 8 MiB. The README's example files are exercised by the import browser check.

```sh
node --test tools/pattern-generators.test.mjs
node --test tools/spatial-patterns.test.mjs
node --test tools/subdivision-density.test.mjs
node --test tools/recursive-patterns.test.mjs
node --test tools/ornament-patterns.test.mjs
node --test tools/textile-patterns.test.mjs
node --test tools/collection-patterns.test.mjs
PYTHONPATH=tools:. .venv/bin/python -m unittest discover -s tests -p test_collection_geometry.py
PYTHONPATH=tools:. .venv/bin/python -m unittest discover -s tests -p test_ornament_geometry.py
node --test tools/authoring-print.test.mjs
node --test tools/recipe-files.test.mjs
PYTHONPATH=tools:. .venv/bin/python -m unittest discover -s tests -p test_patterns.py
cargo test --locked --manifest-path controller/Cargo.toml
cargo build --locked --manifest-path controller/Cargo.toml
node controller/scripts/pattern-thumbnails-smoke.mjs
PLOTLINE_BROWSER=firefox node controller/scripts/pattern-thumbnails-smoke.mjs
node controller/scripts/pattern-studio-smoke.mjs
node controller/scripts/recipe-import-smoke.mjs
PLOTLINE_BROWSER=firefox node controller/scripts/pattern-studio-smoke.mjs
node controller/scripts/adjacent-patterns-smoke.mjs
node controller/scripts/adjacent-patterns-smoke.mjs --natural
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --natural
node controller/scripts/adjacent-patterns-smoke.mjs --spatial
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --spatial
node controller/scripts/adjacent-patterns-smoke.mjs --curves-density
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --curves-density
node controller/scripts/adjacent-patterns-smoke.mjs --recursive
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --recursive
node controller/scripts/adjacent-patterns-smoke.mjs --ornament
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --ornament
node controller/scripts/adjacent-patterns-smoke.mjs --textile
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --textile
node controller/scripts/adjacent-patterns-smoke.mjs --collection
PLOTLINE_BROWSER=firefox node controller/scripts/adjacent-patterns-smoke.mjs --collection
node controller/scripts/complex-patterns-smoke.mjs
PLOTLINE_BROWSER=firefox node controller/scripts/complex-patterns-smoke.mjs
node tools/pattern-review.mjs
node tools/pattern-review.mjs build/complex-review --complex
node tools/pattern-review.mjs build/natural-review --natural
node tools/pattern-review.mjs build/spatial-review --spatial
node tools/pattern-review.mjs build/curves-density-review --curves-density
node tools/pattern-review.mjs build/recursive-review --recursive
node tools/pattern-review.mjs build/ornament-review --ornament
node tools/pattern-review.mjs build/textile-review --textile
node tools/pattern-review.mjs build/collection-review --collection
```

The browser check starts an isolated store without a device connection and
exercises real export, inspection, conversion and HPGL registration. Gallery and
desktop/mobile screenshots are retained in ignored `build/pattern-browser-*`.
Both browsers check visible ink in every family-gallery thumbnail, preview zoom
and Fit, paired toolbar layout, and margin toggling without recipe changes or
regeneration. Workers fetch the shared `limits.json` as data: Firefox rejects
static JSON module imports in workers when served with `X-Content-Type-Options:
nosniff`. The server retains that header. Node callers await `loadLimits()` before
using the generators, reading the same JSON from disk.
The five natural-pattern checks reuse the adjacent-family browser harness with
`--natural`: seven loaded inks including mixed widths, completed coarse previews,
bitmap updates, knob/undo, save/reopen, exact source snapshots and native PDFs,
then woven-knot preparation through HPGL. They also check that composition size
regenerates knot gaps and Cancel restores the completed strand count, with Undo
restoring the requested drawing. The cancellation test deliberately holds the
worker checkpoint briefly so it remains testable on a fast machine.

For the 22-generator batch, all 46 generator tests, 73 controller tests and three
Python export tests passed. Full gallery/workspace checks and the five-family
PDF/HPGL acceptance passed in Chromium and Firefox. The complex-family regression
also passed after sharing physical-width handling. Independent geometry tests
cover ODE convergence, continuous color transitions, fern stem intervals, crystal
symmetry/unique edges, capsule distance clipping and all ten crossings of the
default (3,5) knot. Default/every-neighbor vector reviews live in ignored
`build/natural-review/`; browser evidence is in `build/natural-browser-*` and
`build/pattern-browser-*`.

The generator checks cover defaults and every adjacent knob value, role coverage,
whole-pen page fit, seeded determinism, curve continuity and an independent
ray/box oracle for sponge visibility. No test sends physical plot commands.
The adjacent-family check saves and exports all six via the browser, checks
native PDF line counts and finite pen bounds independently, and prepares a
Truchet plot through HPGL Studio. The vector review script makes a six-pattern
contact sheet plus every neighboring control setting under `build/pattern-review/`.

## Current limits

- Forty-seven generators are implemented; remaining catalog candidates are planned.
- The four complex-plane generators use automatic sampled fields. A coarse 280-cell preview
  measures the shape; final sampling targets a grid-cell diagonal of half the finest
  pen diameter on paper, capped at 3072 cells per side. Final bounds report the actual
  sampling size and whether the target was missed. This is a sampling criterion,
  not a proven bound on the exact fractal boundary or a guarantee of tiny-island
  discovery. Crossing cells are indexed once, avoiding a full-grid allocation pass
  per contour. Navigation spans are bounded to `1e-10` through 20 complex units
  (center components within ±100). Escape iterations increase with zoom from 256
  (Julia starts at 110) to at most 2048; Newton stops at the selected depth plus two iterations. Zoomed escape
  views choose contour levels from sampled escape-value quantiles, with the spacing
  knob scaling that interval. This helps reveal fine structure but is not an
  arbitrary-precision deep-zoom renderer. A formula editor remains future work.
- Apollonian generation count and minimum circle size are simultaneous stopping
  conditions. The latter is shown as a percentage of the enclosing circle diameter.
- Sponge visibility uses voxel ray traversal, with bounded sampling along edges.
  It is an experimental line drawing, not an exact solid-boundary renderer. Detail
  5 is supported (178,316 visible paths at the default camera). Levels 2, 3 and 4
  establish complete checkpoints. During each finer level, completed edge batches
  are painted over a faint previous-level guide in a worker-owned bitmap. Edge
  directions are interleaved spatially; the page receives fixed-resolution bitmap
  updates, not repeated copies of an expanding vector scene. Final preview and
  export use the complete vectors without the faint guide.
- Rendering continues automatically. **Cancel** keeps the last complete level,
  moves the detail control back to match it, and enables export for that drawing.
  This is an undoable document edit. It retains the finest compatible checkpoint, including an already-completed
  finer drawing when the new worker builds a coarser guide; with no completed geometry it offers Resume.
  Complex-plane families have completed sampling checkpoints at 280 and 560 cells, then the target.
  Cancelling records a `sampling_limit` in the recipe and offers **Refine detail**.
  Intermediate batches can be viewed but cannot be saved as a completed plot.
- The former 50,000-path / 400,000-point rejection is removed. Browser generation,
  Rust intake and Python export share `patterns/web/limits.json`: a 512 MiB estimated
  per-drawing working-memory allowance at 384 bytes/path plus 192 bytes/point.
  These weights account for vector staging and temporary fitting/transfer/PDF-writing
  copies, not measured heap use or a guarantee of process RSS. Downstream GEOS
  conversion has additional costs and can use substantially more memory. Errors give the
  counts and estimated memory. Predictable large curve allocations are checked
  before expansion; iterative generators check growing results at checkpoints.
  HTTP scenes have a matching 128 MiB transfer allowance. The browser cache is
  bounded by estimated size and inactive documents release fitted geometry.
- Koch detail extends to 7, dragon folds to 17, Hilbert detail to 9. Unchanged
  geometry is cached, subject to complex-plane physical sampling requirements.
- PDF writing uses at most 16 subpaths or 2048 points per paint, with up to
  32 paints per bounded content stream, and shared-endpoint polyline chunks
  to avoid quadratic string growth. Round caps/joins and role bindings are kept;
  the export regression independently counts every PDF line segment above both
  former size ceilings.
- Defaults and neighboring steps have been inspected as vector drawings. No
  physical paper proof of these new patterns has been made yet.

### 2026-11-05 follow-up validation

The sequence / preference / detail follow-up passes 14 generator checks, the
shared unit-preference browser check, PDF exporter tests, cross-Studio navigation,
and the isolated Pattern → PDF → HPGL browser check. The latter covers seven
loaded pens, a nine-ink custom sequence, undo, two progressive level-4 previews,
cancellation, automatic Julia sampling, shared preferences, and mobile layout.
The older `pen-workflow-smoke.mjs` currently times out at its generic-PDF
`#convert-button` click (line 73), reproduced against both the unchanged release
binary and this change's debug binary; it is not a passing gate for this milestone.

### 2026-11-06 adjacent-pattern validation

All 26 generator checks pass, including every default and neighboring knob,
one/seven/eight-ink sequences, complete incremental batches, golden-triangle area
and angle checks, unique Penrose edges, and unbroken Truchet strands. The complete
fourteen-family browser workflow passes in Chromium and Firefox. The separate
six-family check verifies save/undo/reload, actual downloaded PDFs and exact
native segment counts, page placement, finite pen bounds, and Truchet → HPGL.
The 68 controller and three PDF-export tests pass. Default and neighbor contact
sheets were visually reviewed. The default Truchet joins 1,680 quarter-circle arcs
into 159 strands. No physical test plot was started.

### 2026-11-06 complex-plane validation

All 34 generator checks pass, including independent escape iteration and known
Newton roots, unique basin-boundary ownership, no repeated Newton segments,
analytic contour joins, zoom-anchor invariance, fixed physical frames, arbitrary
Julia parameters, empty windows, and lossless progressive batches. Defaults and
neighbor contact sheets were reviewed as vectors. The complete 17-family browser
workflow passes in Chromium and Firefox. The separate complex-plane check covers
save/export for all three additions, root-role reset, gesture undo/cancel,
inspection versus authoring, rotated point selection into a separate Julia tab,
save/reload, blank-view recovery, and Julia → PDF → HPGL. Native PDF inspection
checks every remaining segment and each finite pen footprint. The 73 controller
and three PDF-export tests pass. No physical plot was started.

### Newton depth and ink-density correction

Jon reported stacked ink and memory-budget failures after the initial basins.
The former boundary pass in `2c21d95` traced the 80-iteration classification all
the way down to the sampling grid, independent of the visible contour controls.
At 2,240 samples, it added 149,081 paths after only 1,739 contour paths. Exact
segment deduplication did not catch the many nearby, physically overlapping lines.

The replacement uses finite contour depth and pen-aware whole-contour selection.
For five roots, the default view, spacing 0.75, a 0.3 mm pen and a 380×250 mm
printable rectangle at 96% composition size:

| 2,240 samples | Earlier default | Depth 8 with pen spacing |
| --- | ---: | ---: |
| Paths | 150,820 | 777 |
| Vertices | 797,766 | 100,472 |
| Stroke length | 66.33 m | 10.12 m |
| Estimated geometry working storage | 201.31 MiB | 18.68 MiB |

The storage figure uses the shared path/vertex weights, not measured total RSS.
At 3,072 samples, depth 8 completes with 745 paths / 126,183 vertices (23.38 MiB
estimated geometry storage). The explicit depth-20 stress case also completes:
62,612 paths / 1,211,114 vertices, about 244.69 MiB estimated geometry storage and
15 seconds in a Node run. Higher depth is deliberately more expensive; the
existing cancellation and memory guard remain active. Increasing sampling from
560 to 3,072 at depth 8 holds stroke length near 10 m instead of exposing more and
more boundary ink. Local raw measurements and vector sheets are under ignored
`build/newton-depth/`; the initial baseline is commit `2c21d95`.

All 36 generator tests pass. Checks include increasing depth, bounded default
refinement, legacy parameter migration, pen-dependent omissions, preserved
crossings and roots, and exact progressive batches. Browser checks cover depth
undo/save/reload, legacy document preservation, and regenerating spacing when a
root is rebound to a broader existing ink. The PDF audit still verifies native
segment counts and full pen bounds. Run `tools/pattern-review.mjs` with
`build/newton-depth/review --complex` for presets and neighboring settings using
the same physical ink filtering as the Studio worker.

### Automatic continuation and cancellation

This follow-up supersedes the earlier level-5 exclusion and count limits. No
Continue prompt or long-render approval step is shown. The large browser check
exercises bitmap progress, cancellation back to level 4, undo back to level 5,
large export and the ordinary PDF/HPGL handoff, using an isolated store and no
plotter connection:

```sh
PLOTLINE_LARGE_PATTERNS=1 node controller/scripts/pattern-studio-smoke.mjs
```

The ordinary browser check also cancels and resumes Julia refinement. Generator
checks verify that incremental batches exactly reconstruct the finished vectors.

Measured acceptance at the default camera and Tabloid paper: level 5 generated
178,316 paths / 356,632 points in 46.5 seconds with 167 bitmap updates. Real PDF
conversion and HPGL registration completed in the isolated browser check; converter
coverage took 1.8 s, visibility 7.4 s, planning 144 s and verification 17.4 s.
Decoded finite-footprint checks passed. These are development-machine measurements,
not timing guarantees. The PDF exporter test exceeds both former count ceilings and
independently verifies all 354,940 source line segments survive PDF serialization.

### 2026-11-06 Gosper / diffusion / tetrahedra validation

All 49 general generator tests and eight dedicated spatial-generator tests pass,
including all neighboring controls, maximum detail, all eight inks, rotated
full-pen page bounds, exact Gosper counts/continuity, independent nearest-particle
queries, nonoverlapping DLA particles and preserved growth prefixes. The tetrahedron
interval test uses a separate 3D ray/triangle implementation; the integrated
visibility check compares actual drawn edge portions against every face. Joining
checks retain the original edge multiset, including branches, loops and roles.
All 60 default-size DLA seed/layout combinations completed (maximum 206 ms in
that Node run; browser/rendering/PDF overhead is additional).

Chromium and Firefox passed all 25 gallery previews, existing workspace/navigation
checks, and the new three-family saved-document/PDF acceptance. The new checks
include mixed-width loaded inks, knob/undo, completed coarse previews and bitmap
updates, cancellation with control restoration, save/reload, exact source snapshots,
independent native-PDF segment and finite-footprint audits, and tetrahedra through
the full PDF → HPGL preparation path. The 78 controller tests and three Python
export tests pass. Browser evidence is under ignored `build/spatial-browser-*`
and `build/pattern-browser-*`. Physical plotting was not part of validation.

After merging portable recipe import/export, the combined 60 JavaScript tests,
three-family browser/PDF/HPGL acceptance and portable-recipe browser checks
passed again. Controller formatting also passes on the combined tree.

### 2026-11-06 curve / carpet / density validation

All 54 general generator tests, eight earlier spatial tests and nine new
subdivision/density tests pass. The new checks cover exact replacement counts and
lengths, closed rounding seams and continuous color sections, disjoint carpet
openings and their total area, all eight attractor presets, conserved deposited
mass and centroid, a direct 2D Gaussian oracle, and independently evaluated
isoline values. Every contour segment stays within one grid cell's diagonal;
there are no links across unrelated orbit points. Maximum detail, lossless
progress batches, all eight inks and rotated finite-pen page containment pass.
The generic geometry census now uses the actual shared memory allowance rather
than restoring the obsolete 50,000-path/400,000-point limits.

Chromium and Firefox pass the 30-family gallery/workspace checks and the new
five-family acceptance: mixed-width pens, knob/undo, complete coarse previews,
bitmap updates, cancellation with Detail restoration, save/reload, exact source
snapshots, native stroke-PDF segment/placement/footprint audits and de Jong through
PDF conversion into HPGL Studio. All 78 controller tests and three Python export
tests pass; controller formatting is clean. Evidence is under ignored
`build/curves-density-browser-*`, `build/pattern-browser-*`, and
`build/curves-density-review/`. No physical plotting was part of these checks.

### 2026-11-06 recursive-art batch and carpet correction validation

All 82 JavaScript generator checks pass (58 general, eight spatial, nine
subdivision/density and seven new recursive checks). The new checks independently
verify Peano's complete grid traversal and continuous color sections, convex
nest containment, square/triangle geometry and area conservation, whole-edge
deduplication, bounded midpoint displacement, exact fork attachments and seeded
repeatability. Maximum details, lossless monotone progress, eight inks and
rotated finite-width page bounds also pass.

Chromium and Firefox pass the 34-family gallery/workspace suite and the four-new-
family saved-document/PDF/HPGL suite. Each exported PDF receives an independent
segment-count, stroke-only, placement and full-pen footprint audit. Lightning
reaches HPGL Studio, and its cancellation test restores the completed detail.
The carpet browser regression restores a legacy rounded document/receipt,
checks square working geometry and stale receipt rejection, exercises
save/revert/reload/source-tab reuse, and verifies that the historical snapshot
and PDF remain byte-identical. Current exports contain five-point square paths.
All 78 controller tests, three Python export tests and controller formatting pass.

Reviewed default and neighboring-knob contact sheets are under ignored
`build/recursive-review/`; browser evidence is under `build/recursive-browser-*`
and `build/pattern-browser-*`. Tests use isolated state and never plot.

### 2026-11-06 inversion / octagonal / organic / architecture validation

All 92 JavaScript generator checks pass (62 general plus 30 focused geometry
checks). New independent checks compare circle and point inversion, inversion
round trips and containment; validate octagonal lattice identities, unit tile
shapes, eightfold symmetry and the emitted edge census; check each growth edge's
actual parent, step length and nearest-node queries against a brute-force oracle;
and compare projected architecture visibility with 3D ray/box intersections at
six camera/preset combinations. Lossless progress, maximum detail, all selected
ink roles and rotated whole-pen bounds pass. The GEOS test independently verifies
no overlapping tiles, interior holes or disconnected pieces for all five detail
levels and all three patch shapes, without geometric snapping or buffering.

Chromium and Firefox pass the 38-family gallery/workspace suite and all four new
saved-document/PDF workflows, including complete coarse previews, progressive
frames, edits/undo, persistence/reload and exact source snapshots. Every new PDF
passes a separate native-PDF segment count, stroke-only paint, page-placement
and finite-width footprint audit. Architecture also passes cancellation/control
restoration and full PDF → HPGL preparation. All 78 controller tests, three Python
export tests and controller formatting pass.

Reviewed contact sheets are under ignored `build/ornament-review/`; browser
artifacts are under `build/ornament-browser-*` and `build/pattern-browser-*`.
Tests use isolated state and never start physical plotting.

### 2026-11-06 star / guilloché / wave / reaction validation

All 106 JavaScript generator checks pass (66 general and 40 focused checks).
New tests independently verify regular template polygons and requested Hankin
ray angles, complete edge coverage, cyclic crossing parity, tip-only ink
transitions and finite-width clearance with unequal pens. Continuous guilloché
seams, analytic radial samples and border normals pass. Wave isolines agree with
independent analytic field evaluation. Gray–Scott updates agree with a separate
periodic finite-difference oracle, preserve the uniform equilibrium and resume
bit-for-bit from growth checkpoints. All presets stay finite and active at both
grid-size endpoints and two seed extremes through 6,000 steps. Cubic resampling
preserves native values and periodic seams; contour paths never jump across the
page. Maximum drawings pass rotated whole-pen page containment, and all new
families deliver lossless monotone progress batches.

Chromium and Firefox pass all 42 gallery thumbnails and the workspace suite,
plus the four-family saved-document/PDF suite. The new checks cover mixed pen
widths, coarse checkpoints and bitmap updates, edits/undo, physical gap
regeneration on resizing, cancellation/control restoration, save/reload and exact
source snapshots. Every exported PDF passes an independent segment census,
stroke-only paint, physical page placement and finite-pen bound audit. Reaction
also reaches HPGL Studio through the real PDF conversion path. All 78 controller
tests, three Python export tests and controller formatting pass.

Reviewed defaults, neighbors and categorical presets are under ignored
`build/textile-review/`; browser evidence is under `build/textile-browser-*` and
`build/pattern-browser-*`. Tests use isolated state and never plot.

### 2026-11-07 five-family collection and grouped browser validation

All 121 JavaScript generator checks pass (71 general and 50 focused checks).
The new checks independently verify flower rotational symmetry and leaf/stem
attachments; compare streamed plant rules with explicit parallel rewriting and
a separate stacked turtle; preserve real terminal leaf positions and avoid
pen-down return walks. Pinwheel tests cover exact integer subdivisions, right
angles, leg ratios, area, orientation variety and a boundary-length census that
includes partially shared sides. Schottky transforms pass point/circle and
inverse oracles plus child-disk containment. Polyhedron topology has the expected
vertices, edges, faces and outward planes. Hatch clearance uses independent
signed distances and responds to unequal widths while preserving the edges.
Maximum drawings pass rotated finite-pen page containment and lossless progress.

Independent GEOS audits cover both pinwheel patch shapes at all five detail
levels using original integer coordinates, with zero overlap, zero holes and
exact target coverage. A separate capsule audit verifies full hatch footprints
inside their projected faces across three solids and 27 camera combinations.
All 78 controller tests, three Python PDF export tests, two new GEOS tests and
controller formatting pass.

Chromium and Firefox pass the 47-family gallery and normal workspace suite,
including grouped navigation, roving keyboard focus, visible thumbnails, retained
browsing state and mobile wrapping without horizontal overflow. Group changes
do not edit artwork; initially hidden groups do not begin thumbnail work. The
five-family acceptance saves/reopens every family, checks undo and progressive
checkpoints, exports exact source recipes and independently audits every native
PDF segment, paint type, page placement and finite pen bounds. Hatch geometry
recomputes after size changes. Plant cancellation restores generation 2 and Undo
restores the requested settings; its complete PDF → HPGL preparation also passes.

Evidence stays under ignored `build/collection-review/`,
`build/collection-browser-*` and `build/pattern-browser-*`. Test libraries are
isolated from user documents, and no physical plotting is performed.
