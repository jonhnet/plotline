# First timing model: Snoqualmie Pass, 2026-11-03

The first offline fit is available in
`controller/models/hp7585-snoqualmie-v1.json`. It predicts 61:29 of feeding for
the 61:59 capture (30 seconds / 0.81% low). This is a provisional model for
similar vector drawings, not a measurement of every mechanical constant.
No running service, Pi software, or physical plot was changed for this analysis.

**Adopted 2026-11-03:** Jon approved this model for the controller. The Rust
evaluator in `controller/src/timing.rs` reads these same embedded coefficients;
it reproduces this capture's feed and motion totals exactly. The UI now requests
an estimate in the background while preparing a plot and displays remaining
time during execution. See `controller/README.md` for runtime/API details.
The original fit results and limitations below are retained as calibration
evidence. No model coefficient was changed during adoption.

**Validated 2026-11-04:** independent Ravenna and Paine captures remain within
1% overall. Whole-drawing refits did not consistently improve predictions, so v1
was retained. See [the new-capture comparison](timing-validation-2026-11-04.md).

## Inputs and evidence

The complete attempt is `88cb8e31-bf26-46e0-8f7f-bb11bba67294`,
“Cabin - Snoqualmie Pass · page 1.hpgl”, started 2026-11-02 at 18:51 UTC.
The existing capture pipeline had already returned its data to dev:

- 1,817,685 prepared bytes, 148,608 commands, 17,951 Pi samples, 3,590 writes.
- All bytes acknowledged; closed trace; no transport errors, injected controls,
  or truncated records. Jon confirmed uninterrupted plotting.
- 59.59 m pen-down and 41.83 m known pen-up travel; 20,374 paths.
- Purple, red, black, turquoise; every pen uses `VS5` and `FS4`.
- Prepared SHA-256:
  `47387daef4f6a712dfac16fb4ef1f5e86c06b47012bc0c9077affec65d5052aa`.
- Trace SHA-256:
  `28650a04c58a0a091d61346f996ceb6b7e6ceaa8aa811bdd469b7200b650bf30`.

The raw sources remain in `controller/state/jobs/plots/<attempt>/`; they are
unique input data in the existing backup selection. The model JSON records both
hashes. Build artifacts and the isolated Python environment are reproducible.
The independently recorded, cancelled attempt
`5087d2b0-62c5-459a-9edd-6d5720e3332f` contains the same drawing's first 16,188
bytes. It is used only as a short-run comparison; its cancellation bytes are
excluded from geometry timing. No physical finish timestamp was recorded in
either run.

## Model

The selected version has six fitted quantities:

| Quantity | Estimate | Interpretation |
|---|---:|---|
| Effective buffer | 1,024 bytes | One fitted FIFO capacity; not a claim about an individual chip's RAM |
| Drawing-speed scale | 0.724 × commanded VS | About 36.2 mm/s at VS5; absorbs unresolved motion effects |
| Pen-up speed | 349 mm/s | Effective cruise cap; weakly constrained |
| Lift + lower cycle | 0.0340 s | Combined overhead; separate halves are not identifiable here |
| Pen change | 2.64 s | Weak estimate from very few transitions |
| Minimum vector time | 0.0177 s | Effective short-vector rate limit, about 56 vectors/s |

The 960-byte/s serial rate is fixed from 9600-baud 8N1 metadata. Host turnaround
is measured separately: a fixed 32.0 ms median from calibration-region packets.
It is not fitted against the completed plot's duration. The ordinary forecasting
command uses that fixed delay and the HPGL alone.

Commands are decoded into lengths, directions, pen-state changes, slot changes,
and VS values. The first carriage position is unknown, so its initial travel
is omitted. For the selected model, each movement costs the greater of its
cruise time and the minimum vector time. State changes add half the fitted
lift/lower cycle, and slot changes add the fitted pen-change cost. No coefficients
are fitted per color, feature, or arbitrary subsection.

Serial delivery and movement run concurrently. For command `i`, let `E[i]` be
its byte boundary, `C[i]` its execution cost, and `k` the first prior command
whose end reaches `E[i] - buffer_bytes`. The simulator uses:

```
arrival[i] = max(arrival[i-1] + sender_gap[i] + command_bytes[i]/960,
                 finish[k])
finish[i]  = max(finish[i-1], arrival[i]) + C[i]
```

The capacity constraint is absent before that much input has accumulated.
Commands free their source bytes when they finish. The service's whole-command
512-byte packet boundaries determine sender gaps. This is a deliberately small
FIFO/serial approximation, not an emulation of the plotter's CTS thresholds,
USB packets, or firmware.

The capture includes both write-entry and kernel-empty timestamps. Consecutive
entry times are the preceding drain plus the sender turnaround. Fitting uses
kernel-empty boundaries and measured turnaround to avoid confusing network
pacing with movement. `prediction.csv` contains both observed and predicted
entry and drain curves. Startup USB retirement can lead physical serial output;
no arbitrary fitted time-axis shift is applied to make the curves agree.

### Acceleration was tested, not established

Alternative fits use forward/backward acceleration limits with a trapezoidal
velocity profile and a geometric approximation to corner speed. Five scales
were compared, from 2,000 mm/s² through the no-correction limit. Once the short
vector rate limit is included, the no-correction version wins the separate
selection blocks. The JSON's numeric `acceleration_mm_s2: 1e9` is the limiting-case
sentinel used by the simulator, **not a measured acceleration**.

This does not mean the plotter ignores corners. It means this one VS5 drawing
does not justify separately inferring its g limit with this model. Acceleration,
cycle overhead, and effective speed trade off. Likewise, nearly every lift has
a matching lower, so splitting their times would add an unidentifiable parameter.
Pen-up and pen-change estimates are fragile: one sensitivity refit has almost no
pen-change evidence and pushes its value near zero. Do not interpret every fitted
coefficient as a physical specification.

## Validation

Fit section durations, not just an hour-long cumulative curve. Consecutive,
disjoint intervals span eight writes (~4 KiB). Contiguous 64-KiB regions are
assigned by region-number modulo five: 0/1/2 calibrate the coefficients, 3 select
the model family, and 4 audit the selected model. A 4-KiB guard keeps intervals
away from partition boundaries and exceeds the fitted buffer. Startup and the
final tail are excluded from parameter fitting. Audit timings do not rank the
candidate models or fit their coefficients.

The earlier exploratory fits used a different split and helped define the model
families. These are internal, blocked validation results on one drawing, not a
blind new-plot accuracy claim. Another actual drawing is the external test.

Using a standalone forecast with fixed host turnaround:

- Audit short-section MAE: **0.68 s**, RMS: **0.85 s**, median absolute relative
  error: **6.5%**, over 65 sections (553 seconds of observed time).
- Five disjoint guarded audit stretches of ~1½–2½ minutes have errors:

| Observed (s) | Predicted (s) | Error |
|---:|---:|---:|
| 118.75 | 116.73 | −1.7% |
| 94.90 | 96.24 | +1.4% |
| 113.78 | 116.10 | +2.0% |
| 117.31 | 122.39 | +4.3% |
| 147.97 | 130.73 | **−11.7%** |

The late black-ink region is a real model miss; it is retained in the charts and
metrics. Global errors partly cancel. Therefore “0.81% total error” is not an
honest blanket accuracy claim for time remaining.

- The cancelled run's first 16,188 bytes took **33.916 s**; the model predicts
  **33.921 s** without refitting. That extremely close agreement is one startup
  comparison of identical geometry, not evidence of millisecond accuracy.
- Leaving out calibration groups gives full-feed predictions of about
  **60:42–61:59**, with geometry/acceleration family held fixed. This is a
  sensitivity range, not a statistical prediction interval for new jobs.
- Predicted remaining motion after feeding: **4.2 s**. The actual parked-pen
  time was not observed, so the physical-finish estimate remains unvalidated.

## Reproduce and reuse

```sh
python3 -m venv build/timing-venv
build/timing-venv/bin/pip install -r requirements-timing.txt
build/timing-venv/bin/python -m unittest discover -s tests -p test_plot_timing.py -v

PYTHONPATH=tools build/timing-venv/bin/python -m plot_timing.fit fit \
  controller/state/jobs/plots/88cb8e31-bf26-46e0-8f7f-bb11bba67294 \
  --check-prefix controller/state/jobs/plots/5087d2b0-62c5-459a-9edd-6d5720e3332f \
  --output build/timing-snoqualmie-2026-11-02

PYTHONPATH=tools build/timing-venv/bin/python -m plot_timing.fit predict \
  controller/state/jobs/plots/88cb8e31-bf26-46e0-8f7f-bb11bba67294/prepared.hpgl \
  --model controller/models/hp7585-snoqualmie-v1.json
```

Output includes `index.html`, PNG/SVG charts, parameter profiles, model/diagnostics
JSON, and CSV curves/section measurements. The checked-in model is the selected
calibration fit, not a subsequent refit using audit times. Predictions on VS
values other than 5 are explicitly flagged as extrapolation. The math uses
bounded [SciPy least squares](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html)
and a Numba-compiled causal simulator; it never contacts hardware.

Tests cover analytic straight-motion time, subdivision invariance, corner
slowdown in the optional kinematic model, the vector-rate floor, FIFO blocking,
wire pacing, source checksum binding, and rejecting unsegmented pause/resume
captures. Runtime regression tests compare all packet checkpoints against an
offline-generated fixture; browser tests cover pre-plot estimates without device
access, immutable saved predictions, pause/reload, and estimate expiry while
still awaiting OA/OS confirmation.

## Recording the physical end next time

The separate `plot-completion` branch (`d8ffb1e`) subsequently implemented
OA/OS waiting on the feeding connection and was merged during model adoption.
The timing inspector/fitter now recognizes its terminal `completion-query`
writes, validates them after all prepared bytes, and excludes them from modeled
drawing boundaries. It still rejects pause/resume captures for unsegmented
calibration. Completion evidence is retained separately from the estimate.
The following notes record the original analysis's open question:

Jon suggested querying the plotter after the final park command. The preserved
HP *HP-GL Programmer's Reference Manual* (1984), printed page 23 / PDF page 26,
lists `OA` (actual position and pen status) for the HP 7585B. This is a reasonable
candidate to append after `PU;SP0;` on the same connection. The available reference
table confirms support, but does not establish whether its reply is a physical
completion barrier, especially for carousel parking.

A follow-up should verify that ordering with an attended plot before labeling
its reply “pen parked.” Record query transmission and reply timestamps/bytes,
retain the connection while waiting, and preserve an explicit unknown result on
timeout or lost reply. Keep this tail probe separate from prepared drawing bytes
and never replay a drawing to recover it. The present capture can be fitted
without assuming an unobserved physical finish timestamp.
