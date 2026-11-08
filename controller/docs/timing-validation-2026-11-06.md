# Timing review: images, patterns and calibration sheets

**Keep the deployed `hp7585-snoqualmie-v1` model.** The broader data supports
roughly 1–3% accuracy for these longer drawings, rather than a universal 1%
claim. Three candidate adjustments improve some examples but worsen others.
No runtime coefficients, services, Pi configuration or physical plots changed.

[Tables and curves](http://dev.iac.jonh.net:11088/build/timing-validation-2026-11-06/)
include the full numerical results. The compact
[numerical snapshot](timing-validation-2026-11-06.json) preserves input hashes,
excluded attempts, coefficients and per-plot scores after raw-log retention.
The original [November 4 review](timing-validation-2026-11-04.md) remains intact.

## Evidence

The local spool contains 33 attempt records through November 6. Fourteen are
complete, uninterrupted, distinct HPGL captures: the three previously reviewed
maps plus **11 additional runs**. The other 19 records are explicitly excluded:
pre-instrumentation plots, unexecuted or cancelled attempts, and recovery suffixes.
Recoveries are not independent complete drawings, even when their suffix finishes.
Two differently sized Sierpiński plots are reported separately but belong to the
same pattern family. We do not count them as independent families.

Every included trace is closed, with all prepared bytes acknowledged, matching
HPGL hashes, complete Pi packet-boundary observations, no reported transport
errors and no injected pause/cancel/resume commands. Completion-query bytes are
excluded from drawing offsets. The 12 captured pre-plot estimates match the
recomputed v1 predictions to floating-point rounding (maximum difference below
0.000000001 seconds). Snoqualmie and Ravenna predate saved estimates and completion
queries; their comparisons remain feeding-only, using unchanged coefficients.

The 11 newer runs all have OA/OS-confirmed finish timestamps. These measure
completion replies on the service's monotonic clock, including the short query
latency, not a separate sensor watching the pen. Timing starts after initial
connection/paper checks and preparation. Percentages below use unrounded times.
Positive error means the prediction was too long.

| New run | Predicted | Recorded finish | Error |
| --- | ---: | ---: | ---: |
| Principal's office | 8:52 | 8:45 | +7 s / +1.34% |
| IMG_0456 | 5:00:19 | 5:08:54 | −8:36 / −2.78% |
| Shrek / Felicia | 1:56:00 | 1:54:43 | +1:18 / +1.13% |
| Sierpiński, larger sheet | 16:46 | 16:38 | +8 s / +0.79% |
| Branching plant | 6:36 | 6:42 | −6 s / −1.61% |
| Sierpiński, smaller sheet | 3:30 | 3:19 | +10 s / +5.17% |
| Hilbert curve | 6:17 | 6:24 | −7 s / −1.79% |
| Happy Shreksgiving | 1:52:06 | 1:51:06 | +1:00 / +0.90% |
| Calibration 374756 | 1:09 | 0:57 | +12 s / +21.31% |
| Calibration 443263 | 1:18 | 1:04 | +14 s / +21.05% |
| Calibration 252190 | 1:18 | 1:05 | +13 s / +20.12% |

Median absolute percentage error across the eight new artwork runs is **1.47%**.
The short calibration-sheet errors look large as percentages but cost only
12–14 seconds. The 3:19 triangle similarly has a 5.17% error of just ten seconds.

All included motion remains at VS5. Sender gaps have medians around 25–36 ms,
with no gap exceeding 0.79 seconds. Substituting each trace's measured gaps for
the forecast's fixed turnaround barely changes the errors: IMG_0456's feeding
error is −2.79% with either treatment. Long Wi-Fi stalls are not evident in these
captures. This does not identify which unmodeled mechanical effect causes the
remaining error.

Live remaining-time replay is also useful: Shreksgiving's median absolute error
at packet boundaries is 16.8 s, with 95th percentile 62.4 s. IMG_0456 remains the
weakest example, with median 5:56 and 95th percentile 8:31. These replay the
existing byte-boundary countdown, not browser polling or rounded minute labels.

## Candidate fits and later checks

Use a chronological cutoff at **2026-11-06 00:00 UTC**. Fit the original guarded
calibration sections on the five sufficiently long older captures: Snoqualmie,
Ravenna, Paine, IMG_0456 and Shrek/Felicia. Each drawing has equal total weight.
Principal's office and calibration sheets are too short for the original
64-KiB calibration/selection/audit split and are not forced into it.

Two candidate families are specified before scoring later plots:

1. Refit only drawing-speed scale and minimum vector time; hold everything else
   at v1, including the 1-KiB buffer.
2. Refit the same six-parameter family as before, with acceleration still at the
   selected no-correction limit and sender turnaround fixed. Profile buffer
   capacity over 256–4,096 bytes, refining at 32-byte spacing.

A separate diagnostic fits only the pen-change coefficient to the two older
calibration sheets' completion times. The third calibration sheet is held out,
and ordinary artwork checks whether that apparent correction generalizes.

| Coefficient | Deployed v1 | Speed/vector-only fit | Six-parameter fit |
| --- | ---: | ---: | ---: |
| Draw speed scale | 0.724122 | 0.777341 | 0.786556 |
| Pen-up speed, mm/s | 349.112 | unchanged | 382.385 |
| Lift/lower cycle, s | 0.033959 | unchanged | 0.038775 |
| Pen selection/change, s | 2.644667 | unchanged | 1.954153 |
| Minimum vector time, s | 0.017711 | 0.018338 | 0.017964 |
| Effective buffer, bytes | 1,024 | unchanged | 992 |

The buffer and vector floor remain close to the original fit. These are effective
parameters, not independent hardware measurements. The calibration-only fit
reduces pen change to 0.928586 s, but the following checks show why that is not a
general replacement for the existing coefficient.

No November 6 timing contributes to any of these fits:

| Later plot | Deployed v1 | Speed/vector fit | Six-parameter fit | Calibration-only fit |
| --- | ---: | ---: | ---: | ---: |
| Larger Sierpiński | +0.79% | −1.31% | +1.91% | +0.44% |
| Branching plant | −1.61% | −3.12% | −2.96% | −3.32% |
| Smaller Sierpiński | +5.17% | +0.26% | +0.90% | +3.45% |
| Hilbert | −1.79% | −2.23% | −5.25% | −5.37% |
| Shreksgiving | +0.90% | +2.04% | +1.74% | +0.69% |
| Third calibration sheet | +20.12% | +15.86% | +6.32% | −1.11% |

The two-parameter fit improves mean absolute percentage error across the five
later artwork runs from 2.05% to 1.79%, mainly by fixing the smaller triangle.
It worsens four of those five runs, increases the median from 1.61% to 2.04%,
and more than doubles the Shreksgiving error. The full refit worsens mean error
to 2.55%. The calibration-only adjustment predicts the held-out calibration
within 0.7 s, but worsens Branching plant and Hilbert substantially.

This is evidence against changing one global set of coefficients now. A future
model change needs to improve varied complete drawings, not just correct the
one-minute calibration special case or trade one geometry's error for another.
All later data has now been examined; future candidate selection should obtain
fresh holdout plots rather than keep tuning against these same six runs.

## Reproduce

Use the isolated environment from `requirements-timing.txt`. This command reads
local state only; it cannot contact the plotter or promote a fitted model:

```sh
PYTHONPATH=tools build/timing-venv/bin/python -m plot_timing.review \
  --before 2026-11-07 --train-before 2026-11-06 \
  --output build/timing-validation-2026-11-06

build/timing-venv/bin/python -m unittest discover -s tests -p test_plot_timing.py -v
```

All nine timing tests pass, including short-job scoring without invented audit
blocks and exclusion of completed recovery suffixes/cancelled prefixes from
independent validation. Existing checksum, pause, FIFO, packetization and
fragmented completion-reply checks remain intact.

Generated outputs are `index.html`, `plots.csv`, `curves.png`, `curves.svg`,
`results.json` and `snapshot.json`. The last file is copied here as the dated
numerical snapshot. The source captures remain governed by existing retention;
this review does not extend their lifetime. A later reproduction after expiry
must restore the matching captures or use the frozen numerical evidence, not
silently substitute a different set of plots.
