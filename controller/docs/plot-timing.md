# Plot timing capture

Implemented 2026-10-31. The first offline model was fitted on 2026-11-03; see
[results, validation, and estimator commands](timing-model-2026-11-03.md).
The [November 6 image/pattern review](timing-validation-2026-11-06.md) compares
11 additional full captures and candidate refits; it retains the original model.
The subsequent [acceleration/carriage experiments](timing-extensions-2026-11-06.md)
test two extra terms with whole drawing families excluded, and also retain v1.
The adopted model supplies estimated duration and live time remaining; the
progress bar keeps its byte metric. See the controller README for runtime use.
Pi captures are automatic for new plots once both ends have this update. Old
plots have no measurements to recover retrospectively.

## What is measured

Stain exposes `tx_queue_timing_v1` as an optional protocol-2 capability. After
the service's identity/paper queries and preparation, `timing` starts a fresh
monotonic epoch and byte counter. The bridge remains independent of HPGL.
Its only new input is the request to observe its serial transmit queue.

Each sample is `[elapsed_ns, previous_poll_ns, accepted_bytes,
kernel_pending_bytes]`. `accepted_bytes` counts successful `os.write` bytes
since timing began. `accepted_bytes - kernel_pending_bytes` is the boundary
that has left the Linux host queue. It is **not** a plotter receipt or a
completed motion. The generic USB serial driver's queue accounting includes
its software FIFO and outstanding USB transfers, not everything in the USB
adapter or plotter. See the [Linux driver implementation](https://github.com/torvalds/linux/blob/master/drivers/usb/serial/generic.c).
Initial pending bytes from before the epoch, if any, can produce a negative
boundary; retain this evidence rather than clamp it.

The observer samples after partial OS writes and during the existing drain
loop (20 ms sleeps; writable waits are at most 50 ms). It retains changed
counts and explicit start/end samples. Unchanged polls update `previous_poll_ns`
without adding duplicate rows, preserving the observation interval of the next
change. USB transfers may retire many bytes together. Do not interpolate those
into falsely precise individual byte timestamps. Nanosecond units do not imply
nanosecond observation accuracy. Scheduler delays can widen the intervals.

At most 64 samples share a telemetry frame; frames flush at write completion
and during long waits. Samples travel over the existing SSH connection before
the normal acknowledgement. A write remains at most 512 bytes, and its normal
acknowledgement still waits for the kernel queue to reach at most 64 bytes.
No byte-at-a-time transmission, HPGL parsing, SD-card logging, automatic replay,
or plotter commands are added on the Pi. Status heartbeats while paused and
closing the session also sample the queue. Closing does not wait for physical
completion; the last kernel/adapter/plotter buffers can still contain data.

## Stored artifacts and inspection

Each new execution stores `serial-timing.jsonl` beside `prepared.hpgl`, `plan.json`
and `record.json` under `controller/state/jobs/plots/<attempt>/`.
These are durable user data covered by the existing spool backup selection.
The log header binds the attempt/job, prepared HPGL SHA-256 and byte length,
coordinate units, and frozen physical pen settings to this capture.

The append-only log includes:

- `header`: schema `plotline.serial-timing.v1`, metadata, host wall time and
  whether this bridge advertised Pi timing support.
- `write`: intent, exact byte array, cumulative `stream_offset`,
  `prepared_offset`, and `kind` (`prepared`, `pause`, `resume`, or `cancel`).
  Injected controls advance the stream offset but not the prepared offset.
- `tx_queue_timing_v1`: raw Pi samples in the columns above.
- `reply`: transport acknowledgement, including the Pi clock/serial metadata
  from `timing`. Clock origin is Pi-local; do not subtract it from host clocks.
- `error`: failed transport operation, if the host could record it.

Every event additionally has a host monotonic elapsed time. This separates
network/host pacing from Pi queue observations without relying on synchronized
wall clocks. Requests are logged before transmission. Complete RPCs sync the
log on dev. Disconnects retain the collected prefix; a missing acknowledgement
never means its bytes were not delivered, and the missing suffix is not replayed.
A hard service/machine failure can leave a truncated final record. The Pi has
no independent persistent trace to retrieve after a broken connection.

Older protocol-2 bridges continue working but set `pi_timing_available: false`;
their host-only traces cannot supply Pi queue timings. Nothing in a timing log
is evidence that the pen physically finished. The usual operator confirmation
still controls completion.

```sh
python3 controller/scripts/inspect-timing.py \
  controller/state/jobs/plots/<attempt> --csv build/plot-timing.csv
```

This verifies the source checksum, prepared slices, offsets and acknowledgements,
reports partial/failed traces, and exports observed boundaries without interpolation.
It does not access the serial device. Preserve the complete attempt directory
when handing data to a fitting script, including interruptions and pen metadata.

## First fitting experiment (next plot)

Start with a small simulator: fixed 9600-baud 8N1 input (960 bytes/s maximum),
an effective downstream buffer, and command execution with distance, direction
changes, explicit `VS` values, pen lift/lower, and pen selection. A candidate
seven-parameter model is effective buffer capacity, a scale on commanded draw
speed, pen-up speed, one acceleration/cornering parameter, lift time, lower time,
and pen-change time. Treat force/width as recorded covariates first, not extra
free parameters. The effective buffer combines unobserved adapter and plotter
storage; do not report it as the plotter's measured hardware RAM capacity.

Fit the simulated arrival/backpressure curve to queue-boundary observations,
respecting observation intervals. Do not treat each byte or each sentence as
an independent stopwatch measurement: buffered commands and adjacent samples
are correlated. Account for actual host/SSH gaps and injected controls; exclude
operator pauses from ordinary movement fitting. The end of sending leaves an
unobserved execution tail, which the simulator must predict.

A varied long plot may constrain speed and cornering well. One plot at a single
speed, or with only two pen changes, may not independently identify buffer,
acceleration, and change-time parameters. Check sensitivity and parameter
ranges; hold out contiguous path sections, not random adjacent samples. Compare
against a simple distance-plus-lifts baseline. Only add live ETA after that check;
report uncertainty rather than a spurious exact countdown. Targeted calibration
plots are an option if the first ordinary plot leaves important ambiguities.

## Verification and rollout

On 2026-10-31, 29 controller Rust tests, 19 appliance tests, the dev restart
guard test, fmt/clippy, and the plotting browser suite passed. PTY tests cover
partial writes, bounded telemetry frames, queue-stall intervals, unchanged bytes
and no replay. Browser tests check exact HPGL/hash pairing, injected controls,
lost-acknowledgement prefixes, inspection/export validation and old-bridge fallback.

Installed on stain without a card rewrite, then restarted the dev service.
The live `status/open/timing/close` check returned zero accepted bytes and empty
kernel queues; it issued no serial data writes. Plotline Studios reports the timing
capability and retains the previous plot's confirmed-finished state. Installed
bridge SHA-256: `df4e7e4a22fd7be888994b3ffb480fde9c7e44e22751b1f7b535b8b5bbd8123e`.
The install report, rollback backup location and empty-capture evidence are in
`controller/state/diagnostics/2026-10-31/timing-install.json` (ignored durable state).
The 2026-11-02 Snoqualmie Pass capture now has an offline fitted model; see the
linked results above. An observed physical-finish timestamp remains next work.
