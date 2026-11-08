# Raster Lab

Development preview: **http://dev.iac.jonh.net:9194/**

Eight user-supplied images form the portfolio. The working set now has four views:
**Stroke recovery** (one selected result per image), **AM/FM shading** (four
columns, six images), **Lettering diagnostic** (six columns, The Visitor), and
**Combined ink + screening** (four matched columns, all eight images).
Earlier rounds, including scattered breaks, are folded under **Earlier
experiments** (52 columns). All 67 recipes are generated for all eight sources
(536 samples), so the comparison picker can still explore any source/recipe.
Principal's Office and The Visitor remain hidden from the current shading view.

- Stroke study: http://dev.iac.jonh.net:9194/?family=strokes
- Shading study: http://dev.iac.jonh.net:9194/?family=screens
- Lettering: http://dev.iac.jonh.net:9194/?family=lettering
- Combined treatment: http://dev.iac.jonh.net:9194/?family=combined
- Palette-aware color: http://dev.iac.jonh.net:9194/?family=palette

This is an independent lab for PDF Studio's future raster representation policy.
It does not modify PDF conversion, register HPGL jobs, access the plotter, or
start physical plotting. The grid supports challenge filtering, column selection,
sticky originals/headers, horizontal scrolling, synchronized pan/zoom, original
blinking, downloads and full provenance. All requests stay local. Use **Comment**
under a cell, or the panel beside the right-hand comparison. Archived tables load
only when opened; comparison selectors can still reach every experiment.

## Arbitrary-ink color separation — round 11

Jon approved taking the useful color-mapping improvements without requiring a
scanner calibration round trip. This separate study compares the existing ideal
CMYK separation, optimized Turquoise/Magenta/Yellow/Black, and optimization with
Blue/Red added. The uploaded IMG_0456 photograph, a generated color ramp, and the
five color portfolio images provide seven challenges. The original portfolio
index, artifacts, input bytes, and comments remain preserved.

Every column uses 0.25 mm **test** widths, artwork fitted to 11 × 17″ with 0.75″
minimum margins, 100% interior fullness, and black stroke recovery off. This is
a controlled color experiment, not a reproduction of the photograph's current
PDF Studio recipe (75% fullness, contrast recovery, and actual inventory widths).
The baseline retains the existing 70% peak nominal channel demand.
Declared inventory-revision-27 swatches are Turquoise #009f7c, Magenta #ff1493,
Yellow #ffff00, Black #000000, and Red #ff0000. **Blue #245bd6 is provisional**:
there is no blue in the recorded carousel. Scan-derived `measured_color` fields
are not treated as calibrated solid-ink reflectances. Inventory is never edited.

`palette.py` accepts one to eight arbitrary named inks and known pen widths.
Its approximate forward model is `product(1 - coverage * (1 - swatch))` in sRGB,
matching the existing illustrative multiply preview under independent hatch
coverage. This is deliberately not a claim about physical ink spectra. A batched
bounded L-BFGS-B solve minimizes Oklab color error, with small travel and
neighboring-mixture penalties. A 17³ RGB table is cached by exact swatches,
widths, color space, and maximum coverage; trilinear interpolation maps image pixels. Paper
white requests no ink. A small travel penalty favors economical alternatives,
but better color can still require substantially more travel than the baseline.

The renderer measures a 33-step **software** tone ramp of its actual finite pen
footprints. This accounts for self-overlap, wave saturation, and highlight gaps
without a scanner. The inverse ramp turns desired coverage into AM/FM demand;
it does not increase the existing waveform's maximum ink capacity. For 0.25 mm
test pens, the simulated maximum is about 57.2% area coverage despite nominal
70% demand. All selected inks use the lowest achievable maximum across their
screens, a conservative bound. Fullness/edge emphasis modifies the desired color
toward paper white before separation, so it is not applied twice afterward.
This matched lab study holds black recovery off. PDF Studio now exposes the
mapper as **Selected pens · optimized color**, independently of black treatment;
existing Auto/K/CMY/CMYK recipes remain on their existing algorithms. See
[the Studio controls and contract](../docs/pdf-raster.md).

Jon found that the original fit extended purple across roughly 40% of the blue
ramp instead of the small source corner. Blue strokes were present: both blue
and magenta reached the 57.2% coverage limit. This was a color-objective error,
not dropped paths. CIELAB's blue-to-white hue behavior is a documented weakness;
the replacement uses [Ottosson's Oklab transform](https://bottosson.github.io/posts/oklab/)
(public-domain code, 2021 matrices), without a new dependency or a hue knob.
The rendered central-blue patch changes from RGB (130,78,176), hue 271.7°, to
(84,79,151), hue 244.2°; the source patch's hue is 234.5°. Lightness/saturation
remain limited by the illustrative ink gamut and open hatch coverage.

The active `*-oklab` recipes are new immutable samples labeled **blue fix**.
The previous CIELAB experiments remain under **Earlier experiments**, with
their original artifacts, code provenance and comments. `palette.py` retains
CIELAB only for those recipes. Tests bound the hue drift across saturated blues,
check neutral/white behavior, and independently sample the actual rendered ramp.

This is ordinary local numerical optimization using existing NumPy, SciPy,
scikit-image, OpenCV, and Cairo dependencies; no new proprietary code or AI model.
Multi-ink separation has substantial prior art, for example
[Chen et al.](https://library.imaging.org/cic/articles/11/1/art00048). Our simple
swatch model is not their spectral-calibration method.

The main index remains `portfolio.json`; `palette-portfolio.json` has independent
code/input/recipe hashes and result IDs, using the same local comments and
downloads. `palette-sources.json` is private input provenance beside the original
`sources.json`. Its photo is extracted losslessly from the retained uploaded PDF;
its diagnostic PNG is reproducible. Rebuild from this checkout:

```sh
/root/plotline/.venv/bin/python -m raster_lab.palette_inputs \
  --state /root/plotline/state/raster-lab \
  --pdf /root/plotline/controller/state/jobs/documents/96e871c2-87b9-4b3a-92a0-a82501451eb7/source.pdf
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m raster_lab.build \
  --state /root/plotline/state/raster-lab --output build/raster-lab \
  --sources /root/plotline/state/raster-lab/palette-sources.json \
  --experiments raster_lab/palette-experiments.json --index palette-portfolio.json
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -m unittest \
  tests.test_raster_palette tests.test_raster_combined tests.test_raster_comments
python3 -m raster_lab.check_palette \
  --state /root/plotline/state/raster-lab --output build/raster-lab
node raster_lab/palette-browser-smoke.mjs
```

On an existing portfolio, append `--retain-previous` to keep frozen results and
generate only new columns; never overwrite a reviewed recipe under its old ID.

The UI reports path mileage and coarse RGB error after a 1 mm blur. That error
describes broad colors in the simulated preview; it is neither a physical color
accuracy claim nor the PDF interpretation regression gate. The extra-ink column
must visibly improve the blue/violet challenge, while white stays blank, paths
remain within the finite-width boundary, and comments stay anchored to exact
source/result versions. No plot job or device command is generated by the lab.

The first 21 samples pass the independent path/provenance audit. These are the
original CIELAB results for IMG_0456, retained as historical comparisons:

| Separation | Pen travel | Mean RGB-channel difference after 1 mm blur |
| --- | ---: | ---: |
| Existing ideal CMYK | 214.2 m | 0.1157 |
| Optimized TurqMYK | 299.5 m | 0.0353 |
| Optimized + provisional Blue / recorded Red | 303.1 m | 0.0275 |

The latter brings the dress closer to blue/violet; TurqMYK alone remains muted
and reddish there. These full-shading comparisons spend more ink to improve
color. The cached-table solves took about 0.7 and 1.6 seconds respectively; the
photograph's full path/preview builds took about 19 and 23 seconds. This is a lab
review checkpoint, not a change to PDF Studio's defaults.

The Oklab correction adds 14 samples (35 total). For IMG_0456, optimized TurqMYK
uses 311.7 m with coarse RGB error 0.0272; adding Blue/Red uses 333.6 m with error
0.0182. These improve the simulated color match while spending additional ink.
The palette solves take about 0.6/1.4 seconds; complete photograph builds take
19/22 seconds. The renderer still cannot match colors outside the available
swatches and hatch coverage. Existing Studio recipes are not changed automatically.

## Decisions and PDF Studio direction — November 4

This is the consolidated checkpoint after round 9. Jon asked to confirm the
decisions and discuss integration, with a small number of meaningful controls.
Jon subsequently approved this design and the combined-renderer check. The PDF
Studio integration below is not yet implemented; round 10 tests the combination.
`preferences.json` records the latest conversation verdict, image-specific
fullness reviews with comment IDs, and the proposed controls/open questions.
An audit found all **64 notes**, their pinned previews, and all referenced note
IDs present. All **63 recipes / 504 results** remain available; frozen round
preferences and existing samples have not been rewritten by this checkpoint.

| Decision | Evidence / qualification |
| --- | --- |
| Keep AM/FM screening | The wave treatment was preferred over the initial simple screens. Preserve continuous darker waves. |
| Keep longitudinal error diffusion in highlights | Jon accepted it for a later paper trial. It addresses the transition between no mark and a straight carrier; it is not random interruption across the whole tonal range. |
| Keep image-dependent fullness and edge falloff | Full preserves dark mood; 75% can be competitive; Game Night also benefited from light 25% interior shading. These are artistic choices, not reliable proportional mileage savings. |
| Prefer joined fill for the ink-shape/lettering treatment | Dense fill beat thinned centerlines in legibility; joining retained its paint with fewer lifts. The approved diagnostic used 0.075 mm. |
| Keep a black-treatment selector | Round-10 visual review rejected joined fill, including its neutral-mask variant, for contrast enhancement of colored screened art. Retain earlier ridge recovery for that purpose; joined fill remains an explicit choice for monochrome line-art comics. |
| Respect the actual pen and final art size | Jon called the 0.25 mm, 11×17 result “not great,” while considering the approach close to convergence. This does not approve its fine-detail quality or request plotting. |
| Retain 2× cubic mask preparation | It helped lettering; reducing diagnostic pen width alone did not repair the skeleton's shape loss. No OCR or invented pixels/letters. |
| Archive doubled edge traces, random scattered breaks and wiggly thinning alternatives | Canny doubled outlines; scattered breaks made worms; Guo–Hall/medial axis gave insufficient readability improvement for their wiggliness. Smoothing/weight recovery did not displace the fill winner. |
| Keep all processing local and open source | No artwork to AI analysis, no proprietary Intel IPP. Preserve the existing dependency guard. |

Image-specific findings remain distinct; a new comparison does not erase an
older artistic preference:

| Source | Screening findings |
| --- | --- |
| My Love | Full is acceptable; 75% with a softer 0.5″ fade is competitive but may not save enough travel to matter. Longer 1.6 mm highlight packets won their comparison. |
| Happy Shreksgiving | The earlier 25% treatment worked; 75% was nearly as good as full, with softer falloff requested. No single universal setting was chosen. |
| Shrek 5 Felicia | Full is a plotting candidate; fine 0.8 mm highlight packets preferred. The earlier claim that full used less travel than edge emphasis was confounded by different peak demand and was corrected. |
| Principal's Office | Screening added noise; retain lines-only treatment. |
| Game Night Sketch | Light 25% interior K screening was useful. Fine highlight diffusion later won a separate comparison. Their combination still needs review. |
| The Visitor | Screening added noise; concentrate on ink/lettering shapes. The larger-pen joined fill loses fine detail. |
| Swamp at Dawn | Full screening preserves the dark mood; edge-driven lightening weakens it. |
| Felicia 2020 | Full preserves tonality; 75% remains competitive; 25% loses too much. Fine highlight packets preferred. |

The PDF Studio integration is now implemented; see
[Raster images in PDF Studio](../docs/pdf-raster.md) for controls, limitations and
end-to-end checks. The design sequence below records the agreed boundary;
historical experiments and their reviews remain unchanged.

### Compact controls

Use the existing image policy to choose **Draw**, omit, reserve blank space or
placeholder. Under Draw, an independent **Screening** switch and a **Black
treatment** selector support screens-only, ink-only and both. The treatment
choices are **Off**, **Contrast strokes** (earlier ridge recovery) and **Ink
shapes · joined fill** (explicitly selected for monochrome line-art comics).
This supersedes the earlier on/off-only proposal after Jon's round-10 review.
Do not use zero fullness as an off switch: it means edge-only screening. If
screening and black treatment are both off, use the explicit omission/reservation policy rather than silently
deciding what happens to underlying vector art.

| Control | Meaning |
| --- | --- |
| Screen inks: Auto / K / CMY / CMYK | Auto detects monochrome using local color statistics and shows the resolved choice. Prefer K for monochrome and the established CMY screen for color when those inks are mapped. Resolve unavailable channels explicitly through the existing pen picker. |
| Ink fullness | How far the interior fades toward white: 100% retains the full-reference shading everywhere; 75% keeps 75% far from edges; 0% leaves only edge emphasis. Strong edges retain the reference demand. This is not measured ink volume, a promised travel saving or solid page coverage. |
| Edge falloff | How rapidly emphasis relaxes spatially toward that interior level. Reuse the smooth falloff with a physical reach, labeled in decimal inches or as tight/broad softness. At 100% fullness this control has no effect and should disable. No additional curve-shape control. |
| Black treatment: Off / Contrast strokes / Ink shapes | Ridge recovery for contrast strokes over screened color; joined fill for ink shapes in line-art comics. Either treatment uses the selected black pen diameter. Keep threshold, interpolation, thinning alternatives and joining budgets internal. |

Pen colors, identities and diameters belong to the existing PDF Studio pen
mapping; do not add a second competing inventory. The renderer consumes a frozen
snapshot of selected materials, just as the PDF worker does now. Auto should
display e.g. “Auto → K”; it must not hide missing-pen substitutions or infer
semantic image roles. CMY screening may still have recovered black ink enabled:
**CMY + recovered K** is different from **CMYK screening**, which also assigns
shading to K. The current selected color screen implements the former.
Default to Contrast strokes when black enhancement is enabled. Keep Ink shapes
an explicit choice: a monochrome photograph is not necessarily line art, so
palette Auto must not silently select joined fill. With treatment Off, K/CMYK
screening can still produce black hatching; only the recovered black layer is off.

For the current matched recipes, the fullness weight is
`F + (1 − F) × edge_emphasis`; the reference has a fixed 70% peak channel demand.
Thus “100% fullness” means the existing full-reference treatment, not 100%
physical coverage. Keep peak demand and signal-processing details internal in
the first UI. A single falloff control already spans the reviewed 0.25″ and
0.5″ transitions. The small distinction in highlight packet grain is recorded:
start with fine diffusion as an internal default, retain the coarse variant in
the lab, and only expose Fine/Coarse later if that distinction merits a control.
Packet lengths must remain valid for the selected pen diameter.

### Treatment decision and remaining checks

Joined fill is a way to depict a binary ink mask, not a universal detector of
the artist's strokes. The current mask thresholds luminance at 128; on color
art or a photograph it can turn dark colored regions into broad solid black.
The round-10 comparison has now been reviewed. Jon rejects both joined variants
for colored screened art, even after the neutral guard reduces the black mask.
Keep the earlier ridge-based contrast strokes and the joined ink-shape treatment
as separate user choices. The internal neutral-mask experiment stays archived;
it is not another normal UI option. Joined fill remains useful for monochrome
line-art comics. This is a representation choice, not a single strength slider.

The Studio renderer supports K, CMY and CMYK with separation/residual tests.
The reviewed portfolio preferences primarily concern K or CMY plus recovered K. Recovered ink must reduce
remaining channel demand using the actual finite pen footprints. Do not hatch
all the same darkness a second time, and do not conflate black hatching with
black stroke recovery. Combined edge emphasis + highlight diffusion also needs
review; separate wins do not certify a new combination.

### Integration sequence and boundary

1. Consolidate the selected algorithms into a versioned local raster renderer.
   Inputs are decoded image pixels, final page-space placement/clip, chosen inks
   and diameters, and the compact settings. Return physical paths grouped by
   ink plus coverage and mileage diagnostics. Keep earlier lab recipes frozen.
2. Extend `PolicyDevice.fill_image` in `tools/pdf_driver.py` and the planner's
   event boundary. The implemented Draw policy captures image pixels alongside the
   opaque footprint. Decode each image through the PDF stack,
   preserve its transform, active clip and paint order, and distinguish **source
   paint coverage** from **paths used to depict it**. An opaque image covers
   earlier art even where its screen leaves white paper. Later PDF paint clips
   the result. Overlapping C/M/Y paths within that image are intentional and
   must not erase each other as ordinary later opaque PDF paints would.
3. Extend saved print intent, validation, capability reporting and PDF Studio's
   image controls together. Reuse preview staleness, progress, resolved pen
   snapshots, immutable revisions and Prepare plot → HPGL Studio. Start with
   ordinary opaque images; preserve explicit decisions/errors for masks and
   effects until separately supported. Keep vector paths and text on their
   existing route rather than rasterizing the entire page.

Generate strokes **after** accounting for final physical image placement.
Rotation, skew or nonuniform image scaling changes the source sampling, not the
circular physical pen diameter. Clipping constrains the full pen footprint.
The lab's 8″ default and round-9 centered sheet are experiments, not rules that
may recenter or resize images in a PDF. Cache decoding separately from paths;
path cache keys include source/occurrence, resolved placement/clip, settings,
ink widths/colors and renderer version. Page/pen edits invalidate the affected
preview. Global defaults can come first, with stable image-occurrence identities
allowing later per-image overrides through the same small control panel.

The integration fixtures should include a placed/rotated image, multiple uses
of one image, nonrectangular clipping, earlier art hidden under sparse screening,
later white/text/vector overpaint, intentional CMY overlap, and mixed raster/
vector pages. Verify selected widths and decoded HPGL, finite-pen clipping,
per-image/per-ink mileage, source paint fidelity and representation loss
separately. Raster approximation is not a reason to weaken PDF decoding checks.

## Scope of these experiments

All processing is local, using ordinary image processing and vector geometry.
There are no recognition models, downloaded weights, generative tools, AI APIs,
or semantic judgments such as identifying eyes. Contrast and connectivity are
geometric evidence, not knowledge of an artist's intentions. Later experiments
can add multiscale persistence, width recovery, coherent edge flow and manual
regions where detail should be preserved.

`experiments.json` specifies each column, including the physical image scale,
pen diameter, cutoffs, blur and density. Add/change columns there and rebuild.
`portfolio.json` lists the challenge titles and artist handles, transcribed from
the supplied filenames. It does not assert a license to redistribute the art.

Current methods, all newly written project code:

- **Dark ink / More ink:** thresholds 96 and 160 on 8-bit luminance. OpenCV
  thinning followed by graph tracing; each skeleton edge is visited once.
  Junctions break runs, and pixel-grid diagonal shortcuts are suppressed.
  This fixed-width baseline loses original stroke weight, can skeletonize broad
  dark regions in undesirable ways, and omits isolated pixels / paths under
  0.35 mm. It is not completed comic-stroke reconstruction.
- **Fine / Coarse edges:** Gaussian smoothing and Canny, followed by the same
  graph tracing. These can trace both sides of existing ink marks. They provide
  a useful comparison with the centerline interpretation.
- **Black / CMY 35% and 70%:** continuous straight hatch rows gated by an
  eight-row ordered screen. Density scales requested ink demand. Black uses
  two directions; CMY uses 15°, 75° and 135°. This simple baseline has visible
  screening patterns and a tone-resolution/pen-lift tradeoff.
- **Ink + CMYK 50%:** dark centerlines plus CMYK shading, with the actual
  centerline footprint reducing subsequent shading demand. This can fragment
  shading heavily and is deliberately available as an experiment, not a claim
  that residual subtraction is solved.

Earlier experiments use an 8.0-inch longest edge and original aspect ratio; the
round-9 paper proof instead fits inside a physical sheet, as described below. Reference
pens have nominal diameter 0.30 mm; the fine-pen study uses 0.15 mm. Screen analysis is capped at 1,000 pixels on
the longest edge; fine-detail recovery and lettering use native pixels up to
2,048. The lettering interpolation experiments then use ordinary 2× cubic
resampling; other paths do not upscale small sources. Pixel center positions map
to page millimetres. Paths are clipped to the image rectangle inset by the pen
radius, including a rounding allowance. SVG and path JSON contain these same
three-decimal coordinates. Cairo renders round finite-width paths for previews
at 150 dpi with 3× supersampling.

The earlier illustrative palette is K `#222222`, C `#00bcd4`, M `#e32991`, Y `#efcf00`.
Separations are the simple algebraic CMY/CMYK baseline; the displayed inks are
**not calibrated**. PNGs multiply per-ink coverage in sRGB, and SVG groups use
CSS multiply. This is an illustration of color mixing, not a physical spectral
ink model. Planned overlaps survive inside the experiment. Importing the SVG
through today's ordinary PDF converter may apply different blend/occlusion
policies; these vector downloads are not yet the PDF Studio integration contract.

Metrics are pen-down distance, separate path count, rasterized preview coverage,
and mean absolute RGB difference after a 1 mm Gaussian blur. Color error is
descriptive, not a pass/fail gate, calibrated colorimetry or artistic quality
score. It remains separate from the converter's PDF decoding regression test.
There is no travel optimization or plotting-time estimate in this lab yet.

## Current comparison: combined ink and screening with turquoise

**Visual review:** Jon calls joined ink a disaster for contrast enhancement in
colored screened art, including the neutral-mask version. Preserve both results
as evidence. Keep joined fill for monochrome line-art comics and expose Off /
Contrast strokes / Ink shapes in the planned PDF Studio controls. The geometric
checks below did not establish artistic suitability.

Jon has no cyan pen and intends to use turquoise in that role. Plotline Studios's
revision-22 inventory records **Turquoise #009f7c / 0.658 mm** in slot 7 and
**Black #000000 / 0.555 mm** in slot 8. Jon explicitly chose **0.25 mm test
widths** with the recorded turquoise color for this comparison. Magenta and
yellow pens exist in new-old-stock boxes but have not been tested. Their colors
remain illustrative (`#e32991` / `#efcf00`). These facts are frozen in recipes
and decisions; the inventory has not been modified.

All four columns use the same image scale fitted to 11×17″ with 0.75″ minimum
margins, materials, full-reference shading, and fine highlight diffusion. Color
images use CMY roles plus optional K recovery; monochrome uses K only. Turquoise
changes preview mixing, but channel demand still uses the baseline separation;
there is no calibrated gamut or spectral compensation.

| Column | Black treatment |
| --- | --- |
| Screen only | No recovered K; all tone comes from screening. |
| Earlier recovery + screen | Selected per-image ridge method, regenerated at the matched test width. |
| Joined ink · dark mask | Existing 2× cubic luminance-threshold mask and joined fill; diagnostic for over-blackening dark colors. |
| Joined ink · neutral mask | Dark saturated colors stay in the screen instead of becoming solid black. |

`combined.py` renders the recovered K's actual finite footprint at screen-analysis
resolution and subtracts it from channel demand before screening. This reuses the
lab's geometric residual approximation, not calibrated optical mixing or a
guarantee of zero CMY/K overlap at every boundary. Recovery and screening switch
independently. CMYK decomposition has unit coverage but is not a reviewed column.

The near-neutral candidate requires the brightest channel below 128 and channel
spread at most 24/255, in addition to the existing dark mask. This is a color
rule, not an ink/photograph classifier; neutral shadows can still fill solid.
K-only treatment retains the luminance mask unchanged, including small colored
JPEG fringes. The Visitor otherwise lost 1,447 mask pixels despite being classified
as monochrome. These policies are internal, not new user controls.

On My Love, the plain mask selects 72.4% of mask pixels versus 13.1% with the
guard, and recovered K travel is 659.8 m versus 117.8 m. On Happy Shreksgiving,
those fractions are 32.2% versus 7.7%, with 256.1 m versus 43.5 m of recovered K.
These measurements confirmed the color-selection issue. Subsequent visual review
rejected both joined variants for color enhancement despite the mask reduction.

Screen row spacing/amplitude retain the reviewed ratios (4 and 1.4 pen diameters)
and frequency scales inversely with width. Highlight packets stay at least two
diameters long. The 300 dpi art previews and 150 dpi paper views use explicit
role-to-material colors also exported to SVG/JSON. Older samples keep their
original colors and hashes, including the 450 dpi lettering diagnostics.

Round-10 validation passes all 114 tests, both portfolio audits and the live
read-only browser check. All 536 samples are valid, with 504 prior samples,
64 notes and 245 saved feedback files unchanged. The combined audit certifies
638,205 joins in the new comparisons, checks matching physical dimensions and
material colors, and verifies identical joined/screened paths for both mask
policies on all three K-only sources. These checks establish implementation
consistency; they do not select an artistic winner.

### Mixed PDF input fixture

`pdf_fixture.py` builds a synthetic mixed image/vector PDF and verifies its source
interpretation using the installed PDF stack. It includes one opaque image used
twice at different transforms, rotation, a circular source clip, earlier vector
strokes hidden by white image pixels, later white overpaint, and subsequent vector
strokes/text. Seven point probes verify source paint order/hue; image hashes prove
reuse. It contains no challenge artwork.

```sh
python3 -m raster_lab.pdf_fixture --output build/raster-lab/pdf-boundary
```

The PDF, source PNG, PDF-rendered `source-page.png` and `fixture.json` live there.
This is input/expected semantics for the next boundary change, not evidence that
the production PDF worker already converts raster images into pen paths. Its
default verifier is `/root/plotline/.venv/bin/python`; override `--pdf-python`
as needed.

## Previous comparison: a 0.25 mm pen on 11×17 paper

Jon requested the preferred joined fill with a **0.25 mm pen**, fitted to
**11×17-inch paper with 0.75-inch margins** while preserving the source aspect.
`fill-paper-quarter` adds that comparison alongside the unchanged diagnostic
results. For The Visitor, portrait wins: **9.5 × 12.288 inches** of art, with
0.75-inch left/right margins and 2.356-inch top/bottom margins. Other subjects
choose whichever paper orientation allows the largest image. Placement is
centered; the margin is a minimum, with excess white space on the other axis.

`paper.py` resolves physical art size **before** recovering and joining the
fill. The threshold-128, 2× cubic source mask and 0.45 mm joining travel cap
remain unchanged. Source ink narrower than the pen can disappear under the
existing conservative full-footprint containment policy. This is a rebuild,
not a thicker rendering of the old paths. No calibrated physical pen inventory
or legibility claim is implied.

The lab compares artwork at corresponding image positions and keeps its
**450 dpi art preview**. **Paper preview** opens a separate 150 dpi full-sheet
PNG with white margins; **Paper SVG (actual size)** carries the exact sheet
dimensions and translated vector strokes. Translation preserves the 0.25 mm
pen width and path distances. No border or margin guides are drawn as ink.
The old ink index is omitted for this differently sized art; actual mileage,
path count and pen-width × distance remain visible. These files are previews
and vector downloads, not registered HPGL jobs.

The Visitor proof contains **19.68 m** of pen-down travel in **16,932 runs**.
Its modeled ink coverage is 4.09% of the art rectangle, versus 5.94% for the
smaller 0.075 mm diagnostic. The wider pen therefore loses some narrow ink
despite the larger print; mileage reduction alone is not a quality verdict.
The independent audit checks fit/aspect, actual SVG placement and finite-pen
margins, identical source masks, every original fill segment, and every join's
geometric containment. At 0.25 mm Cairo's round-cap/join approximations can
differ slightly in antialiasing: the separate raster check permits at most
4/255 alpha at fewer than 0.1% of pixels and reports observed differences.
The geometric certificate remains strict; the older 0.075 mm controls retain
their exact-pixel equality check.

Round-9 validation passes for all 504 samples, retaining all 496 earlier
artifacts, 64 notes and 245 saved feedback files unchanged. All eight paper
fits and their joins pass the independent geometry audit. Observed raster
differences peak at 3/255 alpha on less than 0.02% of pixels per image.
The 104 regression tests and live read-only browser checks pass.

## Previous comparison: preserve the usable fill, reduce lifts

Jon's saved comment `65e8cd30-da30-4e8c-a6ee-90f38b972e99` calls **Ink-shape fill**
best in the row and usable, and asks about chatter. The comment is attached to
the unchanged `thin-fill` control / result `7e0cd2e1c60e889db4ca`, not the newer
smooth/weight candidates. That control stays at the far right with its note.
**Joined fill · fewer lifts** is inserted immediately before it. All earlier
columns, source masks, pen widths and feedback remain available unchanged.

Jon’s subsequent round-8 review chooses **Joined fill** as the current leader.
This remains a diagnostic at an **8-inch longest image edge**: The Visitor is
6.185 × 8.0 inches, with no sheet or margins assigned. The modeled 0.075 mm pen
is not a confirmed physical pen. Jon estimates his smallest pen may be 0.25 mm.
Preserving the same width-to-image proportions with that pen requires a 3⅓×
enlargement to about 20.6 × 26.7 inches before margins. At the existing image
size, regenerate for the wider pen; swapping pens on the existing paths would
invalidate the modeled gaps and finite-footprint safeguards. Preview DPI sets
sampling resolution, not paper size.

The original fill has 133,771 separate horizontal runs in The Visitor, averaging
0.29 mm. `fill_join.py` pairs overlapping runs on neighboring rows and builds
boustrophedon chains. Each original run is traversed in full; small retraces keep
its endpoints and round caps. A vertical connector is accepted only when its
entire finite-width footprint is contained in the existing pair of run footprints.
Thus it neither crosses white gaps nor invents extra ink area.

For pen radius `r` and row separation `h < 2r`, the nearest row is at most `h/2`
away anywhere along the bridge. Its round cap extends at least
`sqrt(r*r - (h/2)**2)` beyond the horizontal run's endpoint. Insetting the
connector from the common endpoints by `r` minus this reach certifies full-width
coverage. Coordinates round inward with extra clearance. Eligible pairs are
found by an interval sweep and matched greedily by added travel, capped at
0.45 mm per join. This is local joining, not a global travel optimizer.

For the complete 8-inch Visitor image with the **0.075 mm diagnostic pen**:

| | Original fill | Joined fill |
| --- | ---: | ---: |
| Separate runs | 133,771 | 11,510 |
| Pen-down distance | 38.70 m | 47.62 m |
| Pen-up distance, listed order | 1,187.72 m | 330.00 m |

That is 91.4% fewer runs, costing 23% more pen-down travel. Extra vertices/retraces
also affect command volume and real ink deposition; fewer lifts alone do not
predict plotting time. Pen-up figures exclude home/carousel moves and future
route optimization. The current physical pen model and source scale must remain
part of any later paper proof.

The independent audit verifies every original segment remains, checks each
connector's finite footprint with a separate squared-distance condition, and
compares the resulting 450 dpi pixels exactly with the original fill. Synthetic
tests also compare high-resolution GEOS buffered unions, preserve tiny marks,
forks and white gaps, and exercise round reversals in Cairo.

At this checkpoint all 496 samples pass the independent audit. All eight joined
previews are pixel-identical to their original fills, with 553,597 connectors
certified geometrically across the portfolio. The 488 earlier PNG/SVG/path-JSON
triples, 64 notes and 245 saved feedback files are unchanged. All 98 tests and
the live/read-only and disposable-state comment browser checks pass.

## Previous comparison: bounded fitting and local stroke weight

The lettering view keeps the existing Zhang–Suen and dense fill samples, including
their original result keys and comments. Two new columns share their exact mask,
scale, skeleton and 0.075 mm pen:

| Column | What changes |
| --- | --- |
| Zhang–Suen · reference | The previous centerlines, unchanged |
| Smooth · bounded | Simplify and round the same paths within 0.12 mm |
| Smooth + stroke weight | Identical fitted paths, plus overlapping parallel passes |
| Ink-shape fill · control | The previous dense fill, unchanged |

`ink_fit.py` converts the binary source to the exact union of its pixel-cell
rectangles, merging identical runs vertically before GEOS unions them. It uses
the distribution Shapely/GEOS stack. The 0.12 mm fitting budget is split 70/30
between Douglas–Peucker simplification and small quadratic corner cuts. Curves
are flattened, compacted to 0.002 mm and rounded to the existing 0.001 mm output
grid before validation. Open-path endpoints and closed-loop anchors stay fixed;
runs shorter than eight fitting tolerances retain their original geometry. Fits
must preserve simple paths and rings, retain at least 75% of a closed loop's
area, and pass a sampled Hausdorff-distance check. Failed fits retry at smaller
tolerances, then retain the original path.

Every accepted fit's **whole pen footprint** must fit within source ink. Round
caps/joins are conservatively circumscribed, with extra coordinate-rounding
clearance. Existing unmodified paths can retain their earlier excess; fitting
does not enlarge it. The downloadable **Skeleton before fitting** is the common
input skeleton, not a rasterization of the fitted curves.

Weight recovery uses offsets 0.85 pen diameters apart, up to 0.4 mm on either
side. Each added pass is clipped to inset source ink and must remain within
0.95 diameters of the preceding pass, so separated ink cannot be reached across
a white gap. Rounded output is checked again for full pen containment. Added
runs shorter than two diameters are omitted; original dots and short arms remain.
The working inset uses conservative mitered corners on the rectilinear source
cells; final round-pen containment is still checked against the exact ink.
Eight-millimetre tiles and an STRtree limit each repeated overlay to nearby ink.
Indexing whole connected components proved insufficient: one connected outline
can span nearly the entire image. A tile-seam regression checks continuity.
This is a bounded stroke-weight experiment: broad solid regions and short
caps can remain underfilled, and overlaps at junctions are not globally deduplicated.
There is no OCR, semantic classification, variable pressure or travel optimization.

The independent portfolio audit verifies identical input masks/skeletons, fixed
endpoints and path counts, an identical smooth-path prefix in the weight result,
and containment using finer circular pen polygons. Quality still comes from
reviewing the previews. The `study_columns` preference orders active columns and
allows old controls to stay active without duplicating them; other studies keep
their chosen columns.

GEOS operations: [Shapely simplification](https://shapely.readthedocs.io/en/2.0.6/reference/shapely.simplify.html)
and [offset curves](https://shapely.readthedocs.io/en/2.0.6/reference/shapely.offset_curve.html).

The Visitor's final whole-image counts are:

| Result | Pen travel | Paths |
| --- | ---: | ---: |
| Zhang–Suen reference | 8.39 m | 5,297 |
| Smooth | 8.22 m | 5,297 |
| Smooth + stroke weight | 15.50 m | 19,200 |
| Dense ink-shape fill | 38.70 m | 133,771 |

The weighted version uses about 60% less travel and 86% fewer paths than dense
fill; this does not assert equal fidelity. Across all eight sources, 8,354 changed
fits and 155,423 added weight paths pass the finer finite-pen containment audit.
All 488 samples pass mileage/path/bounds checks, all 472 earlier PNG/SVG/path-JSON
triples are byte-identical, and all 63 notes / 241 saved files are unchanged.
The 89 unit tests and live/read-only plus disposable-state comment browser checks
pass. Shreksgiving's weight calculation took over four minutes before spatial
tiling and about 12 seconds in the isolated tiled probe; full cell generation
also includes thinning, diagnostics and rendering.

## Previous review: fixed-mask thinning and shape controls

**Round-6 verdict:** Jon reports legibility, with ink-shape fill the most legible.
Guo–Hall and medial axis are slightly more readable than Zhang–Suen, but much too
wiggly to justify switching. Keep Zhang–Suen as the centerline reference and the
fill as the shape/weight control. The following recipes and previews remain
unchanged; this review does not select a new physical plotting policy.

The current tracer maps skeleton pixels directly to page coordinates and calls
`approxPolyDP` with zero tolerance: it removes collinear points but leaves pixel
stair steps intact. A numeric probe on paths wholly within the Visitor's
upper-right quadrant found that 0.05 mm simplification cuts >30° turn counts from
11,424 to 6,516 (Zhang–Suen), 7,821 to 3,526 (Guo–Hall), and 11,877 to 6,027
(medial axis). Those counts do **not** reproduce Jon's visual ranking; they are
not a perceptual wiggliness metric. The probe does not establish safety of the
shortcuts or produce new previews. A next experiment should fit smoother paths
subject to the source ink shape, preserve endpoints/junctions/small loops, and
validate the finite pen footprint. Local stroke-width recovery with a few
touching passes can then be compared separately against the dense fill control.

The reason for the four-way comparison was the earlier stage review:
Jon can still read stage 2 (the binary mask), despite some corruption; stage 3
(the skeleton) is unreadable. That localizes the major loss to thinning, before
finite-pen rendering. The active lettering columns now share **exactly the same
2× cubic / threshold-128 mask**, 8-inch image scale, **0.075 mm diagnostic pen**
and 450 dpi preview:

| Column | What changes |
| --- | --- |
| Zhang–Suen · reference | Exact previous `letter-cubic-narrow` paths |
| Guo–Hall | Different pixel-thinning rule, then direct tracing |
| Medial axis | Distance-guided skeleton, deterministic tie ordering |
| Ink-shape fill · control | Bypass thinning; dense horizontal passes within the ink mask |

The first three reduce ink to constant-width centerlines. The fill control tests
whether retaining more of the original shape and stroke weight helps. It is
deliberately expensive and is not single-stroke recovery. Its round pen's entire
bounding square must fit within the mask's pixel cells, with rounding clearance;
white counters remain clear. It uses 80%-diameter row spacing plus source row
centers to retain narrow horizontal marks. Sub-pen features can still be lost.
The control has a mask download and explicitly has no skeleton download.

OpenCV supplies Zhang–Suen and Guo–Hall; scikit-image supplies `medial_axis`.
Equal-distance ties in the latter can introduce a tiny enclosed cavity in solid
ink at a junction. Restore only cavities containing **no source-white pixels**,
leaving genuine letter counters intact. The existing graph tracer suppresses
diagonal shortcuts around the restored junction. Neither new skeleton is passed
through Zhang–Suen afterward. The common 0.8 mm source-radius veto remains on
centerlines; it is not applied to the shape-fill control.

The mask audit compares all new masks byte-for-byte with the previous cubic
mask. Synthetic tests check dots, loops, junction topology, reproducibility,
absence of accidental re-thinning, and the fill's coverage/white-hole clearance.
These are mechanical checks, not readability scores. The comparison still opens
at the upper-right panel; judgment of lettering is left to Jon. No OCR or image
model is involved. Screen/stroke views keep their selected round independently.

Method documentation: [OpenCV thinning](https://docs.opencv.org/4.13.0/df/d2d/group__ximgproc.html)
and [scikit-image medial axis](https://scikit-image.org/docs/0.22.x/api/skimage.morphology.html#skimage.morphology.medial_axis).

At this checkpoint all 472 samples pass the path/footprint-bounds/mileage audit;
the 440 earlier PNG/SVG/path-JSON triples are byte-identical. All 63 notes and
their 241 saved files are unchanged. Eighty unit tests and both read-only/live
and disposable-state comment browser checks pass. For The Visitor, the reference
uses 8.39 m / 5,297 paths; Guo–Hall 8.35 m / 5,858; medial axis 9.27 m / 8,611;
shape fill 38.70 m / 133,771. These whole-image counts expose the fill control's
cost; no path joining or travel optimization is attempted here.

## Earlier review: keep diffusion; diagnose lettering before another sweep

Jon selected highlight error diffusion for a later real-paper trial. Current
per-image votes favor 1.6 mm packets on My Love and 0.8 mm on Shrek 5 Felicia,
Game Night and Felicia 2020. Those decisions and comment IDs are recorded under
`review_decisions` in `preferences.json`; they do not rewrite frozen experiments
or initiate a plot.

For lettering, halving the pen width made little visible difference. Cubic
interpolation substantially improved the right balloon, but the left remains
unreadable. This points toward loss during thresholding/centerline extraction,
without proving which step is responsible. The current routine knows pixel
geometry; it has no letter templates, dictionary or language context.

[The four-stage upper-right-panel close-up](http://dev.iac.jonh.net:9194/artifacts/visitor-stages.png)
shows the published `letter-cubic` sample's grayscale input, binary mask, accepted
skeleton and actual pen rendering. Individual stage PNGs and the numeric report
are under `visitor-stage-{gray,mask,skeleton,pen}.png` and `visitor-stages.json`.
Reproduce from the current published portfolio with:

```sh
python3 -m raster_lab.lettering_stages \
  --state /root/plotline/state/raster-lab --output build/raster-lab
```

Excluding the outer paper boundary, every one of the 26,328 accepted skeleton
pixels in this panel receives some pen coverage; the second thinning pass changes
zero pixels. Mask and skeleton have the same component and hole counts. These
checks rule out dropped centerline pixels in the final trace/paint step here;
they do **not** prove that thinning preserves a readable letter's shape or that
thresholding kept the right connections. Component counts are not glyph counts.
The close-up is for Jon's visual judgment, not image-model analysis.

Next candidates depend on that judgment: if the binary mask is damaged, compare
a local Sauvola threshold with the fixed cutoff; if thinning damages a readable
mask, compare Guo–Hall thinning with Zhang–Suen and consider fitting strokes to
the grayscale/ink envelope. Ordinary 4× interpolation is another grid-sensitivity
control, not a source of new information. Both Sauvola and Guo–Hall are available
in the installed open-source OpenCV-contrib stack; see the
[official OpenCV documentation](https://docs.opencv.org/doc/doxygen/html/df/d2d/group__ximgproc.html).
No new recovery algorithm or OCR has been enabled by this diagnostic.

## Current feedback round: longitudinal highlight diffusion

Full screening is the practical reference following the latest review. The
75% / 0.5″ version saves only 1.7–3.7% of travel on three color sources, although
My Love and Felicia 2020 save 10.6% and 8.9%. The proposal now targets light-tone
texture rather than seeking a large reduction in overall path distance.

Four screen columns hold the selected black recovery and 70% peak demand fixed:

| Column | Change |
| --- | --- |
| Full · reference | Existing row-selection screen, 4% near-white cutoff |
| Full · lower cutoff | Same method, cutoff lowered to 0.4% |
| Highlight diffusion · 0.8 mm | Low cutoff plus longitudinal error diffusion in light tones |
| Highlight diffusion · 1.6 mm | Same, with longer nominal packets |

For a 0.30 mm pen on 1.2 mm rows, a straight carrier represents nominal ink area
`diameter / spacing = 25%`. The source channel is multiplied by 70% demand, so
this boundary occurs at approximately 35.7% source-channel darkness. Below it,
`highlights.py` carries the difference between requested and deposited ink along
the row, choosing pen-down or pen-up packets. At or above it, the original AM/FM
modulation stays continuous; no debt can create holes in a dark interval.
Unsupported/white intervals are hard barriers and reset the debt. There is no
cross-row diffusion. Seeded ±20% packet-length variation and initial error
phases decorrelate rows; they do not discard the accumulated error.

The deposition budget counts round end caps. A straight run of centerline
length `L` has nominal footprint area `diameter × L + π × diameter² / 4`.
Continuing a run adds length without another cap pair. The implementation also
accounts for the sample interval omitted by a terminated run. Very short
terminal intervals that cannot satisfy the minimum path length remain blank;
there is no attempt to repay their ink in an unrelated region. Page clipping,
bends and simplification add small differences from the ideal budget. The
uniform-tone tests measure actual Cairo coverage as well as accumulated error;
these remain geometric tests, not calibrated optical density or quality scores.

The lowered cutoff is a separate control because it can remove some existing
breaks by itself. On My Love, Full has 9,485 shading paths; the lower-cutoff
control has 6,658. Diffusion has 10,595 / 8,565 with short / long packets. Total
travel is 125.8 / 126.6 / 124.8 / 125.1 m respectively. The grid now reports
**Screen paths**, subtracting recovered K from the total run count. More runs
mean more lifts before travel optimization. The general width×distance ink
index still excludes cap area; preview coverage includes the modeled caps.
These columns are not intended to promise major mileage savings.

A [uniform tone ramp](http://dev.iac.jonh.net:9194/artifacts/highlight-tone-strip.png)
compares all four methods with one black pen. Reproduce it with the optional
open-source distro Matplotlib package (`python3-matplotlib`):

```sh
python3 -m raster_lab.tone_strip --output build/raster-lab
```

### Lettering: pen width versus interpolation

The new note favors Connected ink · dark, but the left balloon within the
upper-right panel remains difficult. Four columns form a controlled comparison:

| Input to tracing | 0.15 mm pen | 0.075 mm diagnostic pen |
| --- | --- | --- |
| Native source pixels | Favored dark tracing, unchanged geometry | Same pixel skeleton, smaller physical footprint |
| Ordinary 2× cubic interpolation | Interpolated pixels, original pen width | Interpolated pixels, smaller footprint |

All use threshold 128 and **450 dpi previews**. The native 0.15 mm column has
exactly the old `letter-dark` paths; only preview resolution changes. Pen-width
pairs use identical threshold masks and skeletons; clipping at the image edge
can differ with pen radius. The 2× version interpolates 828×1071 Visitor pixels
to 1656×2142 before thresholding/thinning. This can change grid-sensitive tracing,
but supplies no new source information and is not recognition or generated
“enhancement.” The two variables stay independent. Metadata records native
analysis dimensions, tracing dimensions/interpolation and preview dpi.

Browser zoom enlarges both the line and its gaps. Printing larger with an
unchanged pen reduces relative pen width, as does selecting a smaller pen at
the original size. Upsampling before tracing is a different operation and can
change the graph. The 0.075 mm pen is a diagnostic, not an assumption about the
physical carousel. SVG downloads remain available for unlimited vector zoom.

### Retaining historical controls

`build --retain-previous` explicitly keeps unchanged old columns and generates
only the new ones. It retains each control's original result key, code hash,
dependency metadata and artifacts, rather than relabeling them with current
code. Changed recipes, resolved selections or source hashes under a retained
ID are rejected; use a new ID. The new portfolio records its parent index hash
and retained result keys. The comparison's parameter panel exposes each sample's
actual implementation hash. Without this flag, the normal builder still
recomputes/cache-checks everything against current code.

All 440 samples pass the independent path, footprint-bounds and mileage audit.
The 368 earlier samples retain byte-identical PNG/SVG/path JSON files. All 59
existing comments and their 225 stored files remain unchanged. The new full
screen matches its former reference geometry, the native lettering matches the
favored connected-ink geometry, and pen-width pairs have identical tracing masks.

## Previous round: selected ink and connected lettering

The new comments favor fine recovery on the color art, while increased
sensitivity clutters faces in Principal's Office and turns broad pale marks in
Game Night into noise. **Selected recovery** uses:

| Source | Recovery / pen |
| --- | --- |
| My Love | Fine detail / 0.30 mm |
| Happy Shreksgiving | Fine detail / 0.15 mm |
| Shrek 5 Felicia | Fine detail / 0.15 mm (both widths accepted) |
| Principal's Office | Fainter + wider / 0.30 mm (its previous Recovery reference) |
| Game Night Sketch | Fainter + wider / 0.30 mm (its previous Recovery reference) |
| The Visitor | Earlier Fainter marks / 0.30 mm, **unresolved** |
| Swamp at Dawn | Fine detail / 0.15 mm |
| Felicia 2020 | Fine detail / 0.15 mm (both widths improved) |

These selections and evidence comment IDs live under `rounds.4` in
`preferences.json`. Earlier recipes resolve against the earlier preference set;
new votes must never rewrite archived preferred-profile samples. The choices
are editorial responses to feedback, not automatic aesthetic classification.

**Color screens:** compare full screening, 75% interior with a 0.25″ fade, the
requested softer 75% / 0.5″ fade, and the light 25% / 0.25″ option that worked
for Game Night. Every column retains 70% peak demand and identical selected K
paths/width within a row. The five color sources use 0.30 mm CMY pens plus
the selected K; Game Night uses K alone. Black recovery may use native pixels,
while color analysis stays at the same 1,000-pixel cap in every new screen.
The actual K width drives footprint subtraction as well as preview/SVG stroke
width. Ink indices sum **each ink's width × its distance**, including overlaps;
using the common 0.30 mm width for fine K would overcount it.

Game Night's screen is indeed AM/FM: both amplitude and frequency vary with
local demand. Its horizontal texture comes from the underlying rows. At light
tones the waves approach straight carriers. The continuous-row treatment stays;
the rejected scattered-break variants remain archived. Full screening remains
available for Swamp's darker mood.

### The Visitor: upper-right panel

Jon can read the source lettering; blaming source resolution alone was too
strong. The old ridge detector can reject crossing/junction pixels through its
normal/extremum and aspect tests. Its graph tracer also drops isolated pixels,
and its minimum-run filter can discard short marks. These are distinct from
physical pen overlap or absent source detail. A synthetic crossing confirms the
distinction: the fine ridge reference leaves its central junction unpainted;
the connected tracer retains one connected cross with no central hole.

The new **Lettering diagnostic** compares the old fine-detail ridge result with
connected-ink tracing at grayscale thresholds 128 and 180, both with 0.15 mm
pens, plus threshold 180 with a 0.30 mm pen. **Upper-right panel** restores a
shared 2× view centered at normalized (0.75, 0.25), and opens automatically in
this study. There is no OCR, recognition, or semantic redrawing of letters.

The tracer thresholds native pixels, thins the dark mask, and walks each graph
edge once. It retains loops and short junction arms, and paints isolated marks
as tiny round-capped segments. OpenCV's Zhang–Suen thinning can entirely erase
a 2×2 dot; if an input component disappears, one of its own pixels nearest its
centroid is restored before the width filter. Wide skeleton interiors (local
source radius over 0.8 mm) are vetoed *after* thinning. Thus narrow lettering
attached to a broad mark is not discarded with that whole component, and a
filled region is not converted to an artificial hollow band. Broad filled
corners can still create spurs; texture can survive; thresholding can join
neighboring marks. This is a diagnostic alternative, not a universal replacement
for the chosen ridge recovery.

Downloads include the native **Threshold mask**, **Accepted skeleton**, and
physical vectors. Recipe metadata records whole-image and upper-right-quadrant
component/hole counts and isolated skeleton pixels. These are geometric
measurements, **not glyph counts or readability scores**; crop boundaries can
split marks. A thicker physical pen can merge neighboring strokes even when the
underlying graph is correct. All previews retain the same 150 dpi with 3×
supersampling; the SVG preserves vector detail for further zooming.

All 368 samples pass independent finite-pen bounds, path-count and recomputed
mileage/weighted-ink checks. The eight selected recovery outputs match their
chosen archived profiles exactly; all 32 new screens retain the selected K
geometry and width. The 296 earlier PNG/SVG/path-JSON samples are byte-identical.
All 57 existing comments and their 217 stored files were verified unchanged.

## Previous round: stronger recovery, matched screening

The latest review found substantial recovery gains without significant visible
noise. Scattered breaks were rejected: the interruptions looked like worms even
in dense tones. Full screening was preferred for the color mood of My Love,
Swamp at Dawn and Felicia 2020. Edge/interior blends remained useful for the
colored comics and the monochrome Game Night sketch. The source comments stay
untouched; this interpretation is recorded separately.

`preferences.json` identifies the recovery reference for each image, with comment
IDs as evidence. It selects Weak continuations for My Love, Happy Shreksgiving,
Shrek 5 Felicia and Swamp at Dawn; Fainter + wider for Principal's Office and
Felicia 2020; and Fainter marks for The Visitor. Game Night retains Fainter + wider,
the recovery used by its preferred edge/25% screen. These are explicit editorial
choices, not an automatic best-image classifier. The result signature snapshots
the resolved profile name, ID and complete recovery parameters. A profile change
cannot silently reuse a result with different settings.

### Recovery comparisons

| Column | What it isolates |
| --- | --- |
| Recovery reference | Per-image selection from the previous round |
| Fainter still | Contrast 0.012, darkness 0.98, response 0.001, minimum run 0.15 mm; no strong-seed requirement |
| Fine detail · 0.30 mm | Same permissive thresholds; native input pixels up to 2048, smaller 0.07/0.10 mm scales, 0.45-pixel minimum sigma, 0.10 mm runs and 0.012 mm simplification |
| Fine detail · 0.15 mm | Exactly the same detector as the fine-detail 0.30 mm version, using a finer physical pen |

There is no upscaling: Visitor now uses its supplied 828 × 1071 pixels in the
fine-detail experiment instead of 773 × 1000. This small increase removes our
analysis cap as one possible loss; it cannot supply detail absent from the JPEG.
The smaller scales and shorter-run threshold test additional algorithmic losses.
Per-result metadata records the actual analysis dimensions. Scale-relative spur
rejection remains enabled, with regressions for filled-corner artifacts, doubled
lines and broken closed loops even at the new permissive settings.

### Matched screening comparisons

Every new screen uses **70% peak demand**, the same 0.30 mm pens, continuous rows
with the existing low-tone row gate, and the **same preferred recovered black
paths for that image**. The columns are full screening, 25% / 50% / 75% interiors
with a 0.25-inch fade, and a 25% interior with a longer 0.5-inch fade. The latter
isolates the requested softer transition. Game Night uses K only; the five color
sources use CMY plus recovered K. No new scattered-break recipe is active.

**Correction to the previous comparison:** its full screen used 70% demand, but
its edge/interior experiments used 95% at edges. Consequently the edge/25% result
could consume more ink than full screening, as observed for Shrek 5 Felicia.
The mileage was correct; the comparison changed two variables. The new set holds
peak demand and black recovery constant. The retired samples are preserved as
rendered, with their original settings. Measured path distance still decides the
ink score; screening, finite width, clipping and wave modulation do not promise
an exact proportional reduction.

Ink 100 now means the per-row Recovery reference in the stroke study, or
Full · preferred ink in the screen study. Earlier rounds retain their original
index references when opened in the archive. The score remains pen width ×
travel, with overlapping passes counted; it is not calibrated ink consumption.

The matched Shrek 5 Felicia screens measure 88.2 m full, then 74.8 / 79.8 / 84.6 m
for 25% / 50% / 75% interiors; the longer 25% fade measures 82.4 m. All 296 samples
pass independent path-bounds, path-count and mileage checks. Each of the eight
new recovery references matches its chosen older geometry, and all 40 new screen
outputs retain that exact recovered black geometry (as a prefix of K for mono).
The 224 earlier PNG/SVG/path-JSON samples are byte-identical; all 44 existing
comments and their 165 stored files were verified unchanged.

## Previous focused studies (archived)

The November 4 feedback asked for more sensitivity, recovery of heavier eyebrows
and frame strokes, a 25% interior ink floor, monochrome-only screening on the line
art, and row interruptions spread across tones instead of conspicuous highlight
bands. The working views keep those questions separate.

### Stroke recovery

| Column | Change from the earlier recovery |
| --- | --- |
| Recovered ink | Earlier 0.12 contrast / 0.58 darkness cutoffs; reference |
| Fainter marks | Contrast 0.05, darkness 0.85, normalized response 0.004 |
| Fainter + wider | Adds 0.90 and 1.30 mm analysis scales to the sensitive settings |
| Weak continuations | Contrast 0.025 / response 0.002 / darkness 0.92, retaining only candidate components containing a 0.07-contrast seed |
| Fainter + wider · fine pen | Same recovery as Fainter + wider, rendered with a 0.15 mm pen |

New recovery variants keep runs down to 0.20 mm. They also reject an isolated
run/spur shorter than 2.5 times its median winning Gaussian scale, keeping
internal junction connectors. This catches short diagonal corner artifacts that
reappear when thresholds are relaxed; it is relative to mark scale, not an
absolute ranking that declares short marks unimportant. Fine marks can be short
and still qualify. Hysteresis follows already-present connected weak candidates;
it never invents a straight connector across empty paper. Weak isolated marks
without a strong seed can disappear in that variant. Expanded scales are still
finite: broad filled regions do not become an arbitrary whole-region skeleton.
Short brush marks and difficult junctions remain a limitation.

All new shading columns use the **Fainter + wider** recovery so the shading
comparisons keep the black geometry fixed. These are local geometric rules, not
recognition of eyebrows, faces or text. The fine-pen column tests physical overlap;
it does not manufacture pixels. The supplied Visitor rendition is 828 × 1071
pixels (analysis is capped at 1000 on the long edge); a finer pen cannot reconstruct
text detail absent from that source. A larger original would be a separate input
comparison, not upscaling presented as recovered information.

### AM/FM shading

| Column | Interior demand and screening |
| --- | --- |
| Full screen · 70% | Uniform 70% demand; reference for this view's ink index |
| Edges · 0% interior | Previous 0.25-inch fade, with 95% peak demand |
| Edges · 25% interior | `0.25 + 0.75 × edge_emphasis`, at 95% peak demand |
| 25% interior · scattered breaks | Same blend, with seeded interruptions along rows |
| Full screen · scattered breaks | Uniform 70% demand with the same distributed interruptions |

The 25% floor is a fraction of peak demand, not a promise of 25% painted paper.
Color and tone edges still get the maximum; region interiors receive a lighter
screen. The floor does not shade white source pixels. Five sources use CMY plus
recovered K. **Principal's Office, Game Night Sketch and The Visitor use K only**,
with recovered strokes and shading appended to the same channel. The palette
rule is inspectable: color requires over 1% of analysis pixels to have an RGB
channel span above 12/255. This tolerates minor JPEG chroma in monochrome sources.
Each result records the resolved palette, not just the column's `source` setting.
No semantic classifier is involved.

The distributed screen assigns reproducible random thresholds to independently
placed 2.5–7.5 mm intervals along each row (5 mm nominal), rather than one threshold
for the entire row. Even dark rows retain at most 92% expected duty; increased
wave arc length compensates where the finite-pen modulation ceiling permits.
Light regions use lower duty, and phase continues through lifts. Its near-white
cutoff is 0.01 instead of the original 0.04. White remains blank. This tests the
suggested consistent interruption texture; it can increase pen lifts and may
still look like short worms. It does not merge/split rows or claim blue-noise
optimality. Compare both full-screen versions to isolate this change from the
edge/interior mixture.

### Ink score

Every card retains total pen-down metres and run count. Active comparisons also
show an **Ink index**: 100 is Recovered ink in the stroke view, or Full screen · 70%
in the shading view. It is the sum of pen width × travel, divided by that row's
reference. Width matters: the fine-pen version uses roughly half the ink proxy
for the same recovered path length. The detail view and archived cards give the
absolute proxy in square inches. Overlaps and different pens count repeatedly;
this is not measured ink volume, union coverage, or predicted plot time. Actual
preview coverage remains a separate metric. An edge-emphasized image can exceed
100 if it spends more ink than the uniform 70% reference.

On the retained inputs, Game Night's reference recovery draws 3.13 m, Fainter
marks 7.48 m, and Fainter + wider 7.99 m. Visitor increases from 5.37 m to 9.00 m
with Fainter marks. Those are additional candidate marks, not a quality score.
For My Love, the full screen uses 127.9 m, edge-only 65.4 m, and the 25% interior
blend 91.9 m (Ink 100, 51 and 72 respectively). The scattered blend uses 90.2 m.
The rendering/mileage audit covers all 224 samples; the earlier 152 PNG/SVG/path
JSON outputs remain byte-identical. All 18 feedback notes and their saved files
were verified unchanged. Browser tests cover both studies, the folded archive,
resolved K-only palettes and persistent comment editing using disposable state.

## Sample comments

Comments are explicit saves to the dev server; they are not just browser state.
The **Best in the row** shortcut adds that phrase to the draft, ready to edit.
Notes can be edited, and previous text is retained in an edit history. A note
records the source SHA-256, experiment ID, exact result key/recipe, timestamp,
and normalized zoom/pan at submission. The grid shows comment counts; the detail
panel includes notes from previous versions, clearly labeled. **Restore view**
returns to the recorded crop for the current result. **Saved sample** opens the
preserved preview, including for an earlier result.

User feedback lives in durable, ignored `/root/plotline/state/raster-lab/comments/`:

```
<uuid>.json             note, version, edit history, sample and viewport
samples/<result-key>/  preview.png, result.json, and paths.svg for vector results
```

The sample is copied out of the disposable build cache on first comment. File
writes are locked, atomically replaced and flushed before success is returned;
retries are idempotent and stale edits receive a conflict. No destructive note
endpoint is provided. Unsaved drafts are kept per sample in browser localStorage
and restored when switching back; these drafts are not backed up on the server.
The notes and pinned samples fall under the existing backup root described below.

The API is `GET/POST /api/comments`, `PATCH /api/comments/<uuid>`, and read-only
`/comment-samples/<result-key>/{preview.png,paths.svg,result.json}`. Writes require
JSON, bounded text, valid source/result identifiers, and a matching browser
Origin. This is still a private, single-user development service, without a
production authentication system. It has no plotter-operation endpoints.

## Wave hatching experiments

The starting reference is Ahmed & Deussen, **Amplitude Modulated Line-Based
Halftoning** (2016):
https://graphics.uni-konstanz.de/publikationen/Ahmed2016AmplitudeModulatedLine/index.html

That paper motivates using wave arc length to encode tone, and points out that
an unmodulated carrier still deposits ink in white areas. Our implementation is
new project code; it does not incorporate third-party application code. The FM
and joint AM/FM variants are our experiments, rather than claimed reproductions
of that paper. All use the same source processing and physical preview pipeline.

| Column | What changes with darkness |
| --- | --- |
| AM · black | Sine-wave amplitude; frequency stays fixed |
| FM · black | Sine-wave frequency; amplitude stays fixed |
| AM × FM · black | Amplitude and frequency together, with gently bent rows |
| AM · zigzag | Triangular-wave amplitude, as an angular reference |
| AM × FM · CMY | Combined sine-wave modulation in three color separations |

The common settings are a 0.30 mm pen, 1.2 mm row spacing, maximum amplitude
0.42 mm, frequency range 0.08–1.25 cycles/mm (AM uses the maximum), 0.70 ink demand,
and 0.20 mm input smoothing. Black rows are horizontal. Color rows use 0°, 60°
and 120°. Combined modulation adds a shared 0.15 mm bend with a 24 mm period.
Row phases are staggered deterministically to avoid columns of aligned peaks.

A numeric lookup converts desired centerline-length ratio into amplitude and/or
frequency. For a sine wave, the slope amplitude is `2π × amplitude × frequency`;
for a triangle it is `4 × amplitude × frequency`. FM phase is the integral of
instantaneous frequency, avoiding the discontinuities caused by using `f(x)×x`.
Curves have at least 20 samples per shortest cycle and are simplified with a
0.02 mm error bound before the existing finite-width clipping.

All five lift the pen where the smoothed channel demand is at most 0.04. This
keeps white paper clear, while preserving connected wave rows through shaded
areas. The remaining carrier still imposes a minimum tone in non-white areas;
maximum modulation and finite pen overlap limit the dark end. The lookup uses
arc length, not calibrated optical coverage, and does not account for every
amplitude derivative or curved-row effect. These are visible experimental
tradeoffs, not a promise of pixel/color fidelity. Zero ink demand draws nothing.

On the retained Felicia 2020 challenge, the CMY wave experiment gives about
138 m over 841 paths, versus about 142 m over 6,071 paths for the original dense
CMY hatch. This is a path-count comparison, not a calibrated plotting-time or
quality claim. All 72 original baseline previews, path JSONs and SVGs remain
byte-identical after adding the new method. The 40 new cells take roughly
41 seconds of summed cell work on dev.

## Edge-prioritized color and recovered ink

The saved feedback distinguished three problems: washed-out global shading,
Canny's paired outlines around an ink band, and diagonal/cardinal branches
invented by skeletonizing broad dark regions. The new columns leave the earlier
recipes and outputs available as controls; saved notes retain their exact samples.

| Column | Comparison |
| --- | --- |
| Recovered ink | Narrow dark-line centers alone, with one black pen |
| AM × FM + ink | Full-image CMY waves plus recovered black lines |
| Edge CMY · 0.25″ | Color emphasis near boundaries, with no black lines |
| Edge CMY + ink · 0.25″ | Same color allocation plus recovered black lines |
| Edge CMY + ink · 0.5″ | Broader color fade plus the same recovered black lines |

**Color allocation:** `focus.py` detects local contrast in floating-point CIELAB,
using the principal direction of the color-gradient tensor, nonmaximum
suppression, 0.30 mm smoothing and probes 0.80 mm to either side. Thus a hue
boundary can attract ink even without a lightness change. Strength rises between
8 and 32 units of Lab distance. Eight strength levels and Euclidean distance
transforms construct a maximum of contrast-weighted cosine falloffs: full local
emphasis at a strong edge, half at half the fade distance, zero at the stated
0.25″ or 0.5″ distance **on each side**. Nearer weak edges cannot hide stronger
edges farther away. There is no contrast against invented white outside the
image, and no uniform interior floor. The distance grid uses the mean x/y pixel
pitch; input resizing can introduce subpixel aspect rounding.

The attention field scales the **local source color**, not a color averaged
across the boundary. CMY waves use 0.95 peak demand for the focused columns;
the full-image control uses 0.70. Their actual wandering positions are checked
for local ink support. Below a straight carrier's coverage floor, a deterministic
low-discrepancy threshold progressively omits rows, instead of leaving a gray
carrier everywhere. This can terminate rows and is not a guarantee of perfectly
smooth optical tone. Dense texture can attract ink throughout a region; these
are contrast cues, not semantic importance or segmentation. Every focused sample
has a downloadable **Ink emphasis map**: white means maximum allocation, black
means none. It is a demand map, not measured physical coverage.

**Black recovery:** multi-scale Hessian normals and local Taylor extrema find
centers of narrow dark valleys. Probes must find lighter pixels on *both* sides;
one-sided steps and broad flat fills are rejected. Scales are 0.14, 0.25, 0.40,
and 0.65 mm, with a minimum 0.65-pixel analysis sigma; the dark-center limit is
0.58 and two-sided contrast must exceed 0.12 on normalized grayscale. The
strongest normalized accepted response supplies a subpixel offset. Accepted
ridges are thinned, graph-traced and simplified to 0.025 mm. This is a newly
written, limited ridge experiment; related differential line-detection prior art
is [Steger's *An Unbiased Detector of Curvilinear Structures*](https://mv.in.tum.de/_media/members/steger/publications/1996/fgbv-96-03-steger.pdf).
It does not implement the paper's complete width/bias recovery or import its code.

Unlike Canny, an isolated thick line gives one centerline; unlike a whole-dark-mask
skeleton, a filled rectangle gives no invented internal branches. Diagonal and
closed-loop synthetic marks remain connected. The new tracer explicitly restores
the closing segment omitted by OpenCV's simplified closed-contour point list;
the original centerline/edge columns are frozen controls predating that correction.
Recovery is still incomplete:
weak strokes, large brush marks and junctions can disappear; nearby marks can
interfere at larger scales. All recovered marks use the same 0.30 mm pen, so
original stroke weight is not reconstructed. A silhouette that is only a
one-sided tone step receives color emphasis but no new black outline in these
columns. Black footprints reduce subsequent CMY demand; this can increase lifts.

Regression checks cover equal-lightness hue boundaries, contrast selectivity,
physical fade distance, actual finite-pen coverage decreasing toward region
interiors, per-side ink support, mileage reduction, absence of artificial frame
edges, single-centerline recovery across several widths, separate nearby marks,
diagonals and closed curves. Artistic quality remains for the portfolio review;
these checks do not claim to recover the illustrator's intent.

At this checkpoint all 152 samples pass independent path-bounds, SVG path-count
and recomputed-mileage audits. The 112 earlier PNG/SVG/path-JSON triples are
byte-identical, and the nine existing notes retain their pinned samples.
For **My Love**, full-image CMY waves use 132.95 m; focused CMY uses 66.66 m
with the 0.25″ fade. Adding recovered K gives 66.16 m (K replaces some CMY),
or 92.65 m with the broader 0.5″ fade. These are ink-distance comparisons,
not equivalent color-fidelity scores or plotting-time predictions.

## Dependencies: open source only

Use the Ubuntu/Debian **distribution build**, not the usual PyPI OpenCV wheel:

```sh
apt-get install --no-install-recommends python3-opencv python3-numpy python3-pil python3-cairo python3-skimage python3-shapely
python3 -c 'from raster_lab.engine import check_dependencies; check_dependencies()'
```

The initial PyPI trial was rejected because its OpenCV wheel included Intel IPP.
The served results were regenerated with the distribution build, which has
neither IPP nor OpenCV non-free algorithms enabled. The builder explicitly
rejects those build flags and disables OpenCL to avoid selecting an external
device driver. No DrawingBotV3 code, closed-source engine, Gurobi dependency,
research model or model weights were incorporated.

The used libraries are OpenCV/OpenCV-contrib (Apache-2.0), NumPy (BSD), Pillow
(HPND-style permissive license), Cairo/Pycairo (LGPL/MPL dual licensing), and
scikit-image/SciPy (BSD). The medial-axis experiment uses the distribution
scikit-image package, not a downloaded model or proprietary engine.
Path fitting adds Shapely (BSD) using GEOS (LGPL), both distribution packages.
Distribution package copyright files under `/usr/share/doc/` include the
specific bundled notices. This project adds no new proprietary dependency.
The audited package versions are recorded in `dependencies-ubuntu24.04.txt`.
Each result also records runtime library versions and the OpenCV build hash.

Intel documents IPP as a native performance library, distributed under its
own license; it is unrelated to sending an image to an analysis service:
https://www.intel.com/content/www/us/en/developer/articles/tool/intel-integrated-performance-primitives-faq.html

## Data and reproduction

The original supplied JPEG renditions and signed URL provenance are durable
private inputs at `/root/plotline/state/raster-lab/`:

```
inputs/<challenge>.jpg       exact downloaded bytes
sources.json                source URLs, dimensions, SHA-256, credit
source-urls.txt              original eight supplied URLs in portfolio order
```

Do not delete this directory as a cache. It is covered by the existing
`/root/plotline` root in `/root/mirror_manifest.json`; this only records backup
coverage, not that a later backup has completed. The art, signed URLs, comments and pinned samples stay
out of Git. The server never serves the URL/provenance files. Titles and artist
handles remain visible with the sources. The inputs total approximately 1.2 MB.

```sh
# First capture only; exact private URLs must be supplied by the operator.
python3 -m raster_lab.import_sources /root/plotline/state/raster-lab/source-urls.txt \
  --state /root/plotline/state/raster-lab

# From any checkout containing this code:
python3 -m raster_lab.build \
  --state /root/plotline/state/raster-lab --output build/raster-lab

# When adding new IDs while preserving existing controls verbatim:
python3 -m raster_lab.build \
  --state /root/plotline/state/raster-lab --output build/raster-lab --retain-previous

python3 -m raster_lab.server \
  --state /root/plotline/state/raster-lab --output build/raster-lab \
  --host 0.0.0.0 --port 9194
```

The builder verifies source hashes before processing. Output goes under ignored
`build/raster-lab/`, keyed by the source hash, full recipe, implementation hash,
and dependency versions. A matching cell is reused; a changed experiment creates
new artifacts. On a first build the index is published incrementally. Subsequent builds keep
the complete current grid available until the new index can be published
atomically; intermediate progress is in `building-portfolio.json`. Artifact directories contain the PNG, thumbnail, SVG,
physical path JSON, and result/recipe metadata. A missing source or a mismatched
checksum fails explicitly. Rebuilding the initial 72 cells took approximately
39 seconds of summed cell work on dev; a cached rebuild reuses all cells.

The live dev service runs from `/root/plotline/worktrees/raster-lab` as the
transient `plotline-raster-lab.service`. To recreate after a reboot or stop:

```sh
systemd-run --unit=plotline-raster-lab \
  --description='Plotline raster experiment lab (dev)' \
  --property=WorkingDirectory=/root/plotline/worktrees/raster-lab \
  --property=Restart=on-failure --property=NoNewPrivileges=true \
  --property=PrivateDevices=true \
  /usr/bin/python3 -m raster_lab.server \
  --state /root/plotline/state/raster-lab \
  --output /root/plotline/worktrees/raster-lab/build/raster-lab \
  --host 0.0.0.0 --port 9194
```

The server is for this private dev host. Its only mutations are local comment
creation/editing; it has no routes for modifying print jobs, downloading
arbitrary URLs or operating a plotter. It is not a production
deployment and does not change an IaC production marker.

## Validation

```sh
PYTHONPATH=tests python3 -m unittest test_raster_lab test_raster_comments test_raster_waves test_raster_focus test_raster_study test_raster_round3 test_raster_lettering test_raster_highlights test_raster_thinning test_raster_ink_fit test_raster_fill_join test_raster_paper test_raster_combined -v
cd controller && npm ci && cd ..
node raster_lab/browser-smoke.mjs
```

For an isolated worktree sharing the existing browser-test install:

```sh
PLOTLINE_PLAYWRIGHT_PACKAGE=/root/plotline/controller/package.json \
  node raster_lab/browser-smoke.mjs
```

One hundred fourteen regression tests cover skeleton continuity/no duplicate traversal,
junctions and loops, finite-width clipping and rendering, blank input, original
aspect ratio, increasing hatch density, preserved color overlaps, mileage,
source integrity, cache reuse, dependency rejection and HTTP path boundaries.
They also check modulation, continuous FM phase, monotone tone/coverage, wave
continuity, deterministic results, note persistence and exact-sample snapshots,
concurrent/retried saves, edit conflicts, invalid anchors and cross-origin writes.
New checks cover isolated dots, small-component thinning loss, short junction
arms, closed glyph loops, threshold links, broad-ink attachments, per-ink Cairo
and SVG widths, weighted ink accounting, and frozen historical preferences. Highlight checks cover bounded cap-aware ink
error, monotone finite-pen coverage, white barriers, uninterrupted dark runs,
deterministic row decorrelation, and distinct interpolation/pen-width effects.
Paper tests check aspect-preserving fit, orientation, centering, physical SVG
dimensions, unchanged pen/path lengths during placement, invalid margins, and
regenerating the fill for a wider pen instead of simply thickening old paths.
The browser smoke test checks all eight rows, four active studies and the
fifty-two archived columns, the upper-right bookmark, diagnostic downloads, filtering,
column visibility, comparison selection, pan/zoom, blinking, download links,
horizontal layout, absence of JavaScript errors and absence of external requests.
Screenshots are written to ignored `build/raster-lab-browser/` for local review;
they are not sent to an image-analysis service.

Audit generated physical paths, selected K geometry, source integrity and optional
before-build snapshots independently:

```sh
python3 -m raster_lab.check_portfolio \
  --state /root/plotline/state/raster-lab --output build/raster-lab \
  --before build/raster-lab-before-round10.json \
  --notes-before build/raster-notes-before-round10.json
```

Audit the matched combined samples independently as well:

```sh
python3 -m raster_lab.check_combined --output build/raster-lab
```

The optional `--before` file is a saved portfolio index; `--notes-before` maps
relative comment/snapshot filenames to their SHA-256 hashes. Preserve old
artifact folders to compare PNG/SVG/path JSON bytes. Neither snapshot includes
private source URLs. Omit those options in a fresh checkout.

The browser test can exercise real comment creation/editing using a separate
server with disposable state. Copy `sources.json` and link `inputs/` into a
directory under `build/`, then run that server on `127.0.0.1:9294`, pointing at
the usual generated output. Set `RASTER_LAB_TEST_WRITES=1` and
`RASTER_LAB_URL=http://127.0.0.1:9294` when running the smoke test. It refuses to
write on the live port 9194; the normal live smoke test does not create notes.
