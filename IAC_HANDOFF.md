# Plotline Studios: production publication

Application preparation only. Production publication, state migration and plotter
ownership transfer remain operator actions. No production guest, reverse proxy,
deployment marker or physical plotter was changed during this work.

## Boundary and publication input

Use the existing IaC `DevProject` publication lifecycle by composition:

- Home repository: `/mnt/dev-source/repos/plotline.git`.
- Publication input: `refs/heads/main`; mirror the intended release to the
  `github` remote, `jonhnet/plotline`, too.
- Operator entry point: `./deploy plotline publish` in colfax-config.
- IaC selects/locks/exports the revision, retains release tags, provisions the
  guest and retained mount, invokes installation, checks readiness, then promotes
  its production marker. Plotline never reads or writes deployment markers.
- Plotline owns dependencies, builds, its three services, application state
  compatibility, activation and readiness. IaC owns guest resources, DNS,
  firewall/multicast, SSH access, proxy, backups and release retention.

Export with `git archive` so committed `SOURCE_REVISION` expands to the full
40-character commit. Supply the export directory **without `.git`**, and that
same full revision. No development checkout, private historical repository,
existing virtual environment or build output is an installation input. Do not
reconnect the preserved private history or old worktrees to the public history.

## Guest and configuration

Supported: **Ubuntu 24.04 LTS, x86-64, systemd**, with Internet access for apt,
Node/Rust/Python/npm dependencies and optional public map sources. A full VM or
systemd guest is appropriate; this installer is separate from the preview-only
Podman quick-start. A restricted container must support systemd filesystem
namespaces to run the service hardening below.

Recommended initial allocation: **16 vCPU, 32 GiB RAM, 64 GiB root disk and a
128 GiB retained data mount at `/srv/plotline`**. No GPU, USB passthrough or serial
device in this guest is required. Keep at least 20 GiB free for temporary output;
monitor disk growth and retain headroom for the largest simultaneous job.

An 8-vCPU/16-GiB guest is a reasonable smaller baseline for ordinary documents
and maps. Large plots motivate the larger recommendation:

| Measured workload | Worker count | Elapsed | Peak RSS |
| --- | ---: | ---: | ---: |
| 44 × 34-inch Washington map, native PDF conversion | 8 | 110.71 s | 10.60 GiB |
| Same frozen input and identical output | 16 | 76.73 s | 13.00 GiB |
| Mountain map composition proof | 4 terrain threads | — | 809 MiB |
| Its draft composition | 4 terrain threads | — | 359 MiB |

These are retained development measurements, not guest throughput guarantees.
The conversion figures exclude PDF extraction, composition and web processes.
The large fixture's source and benchmark logs remain in the private development
evidence; they are not prerequisites for the exported-source acceptance tests.

IaC creates the retained mount, then writes `/etc/plotline/config.json` using
[config.example.json](packaging/production/config.example.json). The installer
refuses an ordinary unmounted directory rather than writing user data onto the
guest's disposable root disk.

| Input | Default / meaning |
| --- | --- |
| `data_root` | `/srv/plotline`, existing retained mount |
| `install_root` | `/opt/plotline`, rebuildable toolchains/builds |
| `service_uid`, `service_gid` | `970`, stable numeric ownership across recreation; reserve these or explicitly choose unused IDs |
| `public_url` | `https://plotline.tech.jonh.net`, browser origin, no path |
| `web_bind` | `0.0.0.0:9191`, guest-facing reverse-proxy upstream |
| `map_port` | `9193`, loopback only |
| `ipp_port` | `9192`, all guest IPv4/IPv6 interfaces |
| `ipp_hostname` | `plotline.local`, direct-reachable printer hostname, not necessarily the browser proxy hostname |
| `ipp_name` | `Pen Plotter`, DNS-SD instance name |
| `ipp_discovery` | `true`; `false` disables advertisement for isolated tests |
| `device_config` | `null` until explicit plotter cutover; otherwise an absolute connection JSON path within `data_root` |
| `conversion_workers` | `1` PDF inspection/conversion worker; configurable 1–4 |
| `map_workers` | `1` composition worker; configurable 1–4 |
| `geometry_workers` | `8`, both native conversion planning and visibility; configurable 1–16 |
| `map_geometry_threads` | `4`, native terrain geometry; configurable 1–16 |
| `build_jobs` | `16`, build concurrency; configurable 1–16 |

On the recommended 32-GiB guest, `geometry_workers: 16` is a reasonable choice
for shorter large-map conversion times. Keep the PDF and map job limits at one
initially. They are independent, so one conversion and one map can overlap.
Maze generation has two worker slots; Pattern geometry is primarily generated
in the browser, while synchronous Pattern export requests can overlap on the
server. This is household scheduling, not a tenant-wide memory quota. The
installer does not set an OOM-inducing per-service `MemoryMax` or claim arbitrary
concurrent uploads will fit. BLAS/OpenMP threads are limited to one; map HTTP
fetches have four shared slots. Heavy jobs need CPU/RAM, not a GPU.

## Exact installer interface

Run as root **inside the application guest**. IaC supplies these values; the
example revision below means the selected full commit, not a branch name:

```sh
python3 /path/to/export/packaging/production/install.py install \
  --source /path/to/export --revision FULL_40_CHARACTER_COMMIT \
  --config /etc/plotline/config.json

python3 /path/to/export/packaging/production/install.py check \
  --revision FULL_40_CHARACTER_COMMIT --config /etc/plotline/config.json

python3 /opt/plotline/current/packaging/production/install.py idle \
  --config /etc/plotline/config.json

python3 /opt/plotline/current/packaging/production/install.py stop \
  --config /etc/plotline/config.json
```

Exit **0** means success. Exit **75** means temporary refusal, with the active
request/job/plot or unavailable observation and next step in stderr. Other
nonzero exits mean configuration/build/readiness failure. IaC must preserve the
old production marker on every failure. Do not bypass 75 with `systemctl restart`.

Installation is synchronous. Jon's first production publication completed in
about three minutes; that is an observation, not a machine-independent budget.
Each build step streams its elapsed time and compiler output. The installer
reuses dependency caches between commits, while a repeated completed revision
still bypasses compilation entirely.
Readiness retries for 30 seconds, with bounded three-second HTTP/IPP operations
and an eight-second IPP subprocess ceiling. Its final probe can extend that
deadline slightly. No map-provider or physical-device query is part of readiness.

The build uses Node 22.23.2, Rust 1.94.1, the Cargo/npm lockfiles and exact Python
requirements, including PyMuPDF Lite and Shapely/GEOS. It builds the native map
extension and the patched CUPS 2.4.20 PDF-only IPP server. Ubuntu packages come
from the configured 24.04 repositories, including security updates; their exact
installed versions are recorded. This is a repeatable fresh build, not a claim
of bit-identical Ubuntu package repositories. Runtime raster processing uses
distribution OpenCV; the existing check rejects Intel IPP/nonfree builds.

The unprivileged `plotline-build` account builds under
`/opt/plotline/builds/<revision>`. Completed executable trees become root-owned;
the separate `plotline` runtime user can write only retained state. Source hashes
exclude generated Python bytecode. A repeated revision must have identical
source bytes, and a healthy repeated reconciliation preserves running PIDs.
No source checkout or Git executable is required in the guest.

`/opt/plotline/build-cache` holds revision-independent Cargo and native geometry
targets, a verified patched-IPP binary, and pip/npm download caches. The native
cache namespace includes Rust/Node, installed package versions, architecture
and compiler flags. Cargo still validates changed source and dependencies.
The IPP cache checks builder/patch/environment identities and binary SHA-256.
Completed runtime executables are copied into each release and become root-owned;
building the next revision cannot rewrite a running release. Toolchains and
build caches survive publication, live outside retained user data, and should
not be backed up. A rebuild after root-disk loss can recreate them.

`/opt/plotline/current` points to the activated build. Each build has
`installed-build.json` with revision, source hash and tool/package versions.
`/srv/plotline/installation.json` records the observed installed revision,
configuration hash, state schema, readiness result and check time/error. This is
an observation, **not** IaC's intended-production marker. Failed activation can
leave the new revision installed but unready; reconciliation/rollback must use
the same installer. Inactive build directories can be removed according to
IaC's release-retention policy; never remove the active target. The installer
does not prune releases itself.

If an installer is interrupted during building, ensure its remaining build
subprocesses have exited before retrying. The next attempt discards and rebuilds
an incomplete inactive build. An incomplete active build refuses rather than
deleting running code. Interrupted apt/dpkg work may require `dpkg --configure -a`
before retrying. After an activation interruption, reconcile the selected
snapshot and inspect `installation.json`; do not infer success from the symlink
alone or automatically promote a marker.

## Supervision and safe publication

`plotline.target`, enabled at boot, groups three services:

| Unit | Process | Listener |
| --- | --- | --- |
| `plotline-web` | Rust server: landing, PDF, HPGL, Pattern, Maze, Map proxy, conversion/intake workers | configured web bind |
| `plotline-map` | Python composition service and bounded subprocess workers | `127.0.0.1:9193` |
| `plotline-ipp` | Patched `ippeveprinter`, capture callback | `0.0.0.0:9192` and IPv6 |

Map and IPP start after web; IPP also starts Avahi when discovery is enabled.
Each uses `Restart=on-failure`, a three-second delay and a 60-second stop timeout.
They require the retained mount, run as `plotline`, and use a protected read-only
filesystem except that mount, private `/tmp` and `/dev`, no new privileges,
umask 0077, 8192 file descriptors and 512 tasks per service. Logs go to journald.
Generated `/etc/plotline/runtime.env` and systemd units are application-owned;
change inputs in `config.json`, not generated files. IaC owns Avahi interface
policy and host configuration.

The installer takes an exclusive flock on the stable
`<data_root>/admission.lock`. It retries nonblocking acquisition for up to ten
seconds so an ordinary short browser request can finish instead of spuriously
deferring publication. A sustained holder still refuses with exit 75, without
stopping services; the reason identifies lock contention rather than claiming
a particular kind of work is active. HTTP handlers/background conversion tasks and IPP
mutations use shared locks. While installation owns the gate, new work receives
HTTP 503; queued background work waits. Maintenance/status reads remain available.
It checks PDF records, Map renders/thumbnails/preparations/handoffs, IPP unfinished
jobs and the durable active-plot record before any stop. Map cancellation is
still busy until its child process exits. A plot remains busy after feeding while
motion completion is unconfirmed, and also after a service crash.

The gate remains closed during building and activation. Existing idle processes
stay running until the complete new build passes dependency checks. A failed
build leaves them running and releases the gate. Installation never cancels
work to make itself idle. This intentionally means updates have a maintenance
interval; schedule them when nobody is authoring. Another operation's active
request can cause an immediate 75 even without a long job; retry after it settles.

Health checks, installation, IPP receipt and reboot **never start physical
plotting**. Normal plotting requires an explicit operator action in HPGL Studio.
After unexpected interruption, inspection/conversion can recover or be retried
from durable inputs. A physical run is marked interrupted; uncertain bytes are
never automatically replayed. The operator resolves/reset-acknowledges that run
in HPGL Studio after checking the actual plotter before publication proceeds.

Use `stop` above before an IaC reboot, shutdown, backup or recreation. It checks
and closes admission before stopping all services. A raw hypervisor shutdown or
direct `systemctl stop` cannot promise graceful completion of an active plot;
the 60-second unit timeout is a failure bound, not a way to drain an eight-hour
plot. IaC must honor exit 75 before destroying or rebooting the guest.

## Browser proxy and access

Public browser origin: **https://plotline.tech.jonh.net/**. IaC reuses the existing
“Pen Plotter” tile and `plotter-pen.png`, changing only the tile destination.

Proxy the entire origin to guest TCP 9191, including `/map`, API and downloads.
Preserve `Host` and `Origin`; pass the ordinary forwarded scheme/address headers.
The Map proxy removes `/map` internally. No WebSockets or SSE are currently used;
progress is polled JSON. Preserve streaming/download bodies and the
`X-Plotline-Request`, `X-Plotline-Upload` and `Idempotency-Key` headers.
Set an upload ceiling of **600 MiB** and read/send timeouts of **300 seconds**.
Application limits remain smaller by type: PDF/HPGL 64 MiB, print intent 32 MiB,
Pattern path transfer 128 MiB, calibration scan 512 MiB, map JSON 8 MB.
Long composition/conversion jobs use asynchronous status URLs. Proxy temporary
upload storage is IaC's responsibility if request buffering is enabled.

The application has **no login or per-user authorization**. Same-origin write
checks are not authentication. Restrict access to the household/VPN, or put IaC's
authentication at the reverse proxy before exposing it beyond that trust zone.
IPP should be reachable only by trusted household clients. Do not expose its
port or the hardware-control API anonymously to the Internet. Decide proxy
authentication/access policy on the IaC side; do not make the Map worker public.

## IPP and discovery

Queue: **`ipp://<direct-guest-hostname>:9192/ipp/print`**, PDF input only.
The advertised review/admin URL is `https://plotline.tech.jonh.net/`; submitted
jobs open in PDF Studio for artistic interpretation, never automatic plotting.
The browser proxy hostname must not be used for direct IPP unless it also routes
TCP 9192 to this guest. Set `ipp_hostname` to the guest's direct LAN DNS name.
It is an accepted HTTP Host alias, **not** an override of Avahi's SRV target.
IaC should name the guest/Avahi host `plotline` (giving `plotline.local`) or supply
another resolvable host and document that queue URI.

`ippeveprinter` owns the dynamic advertisement via Avahi's D-Bus API; Avahi owns
multicast transport. Instance name: **Pen Plotter**. Main service:
`_ipp._tcp`, subtype `_print._sub._ipp._tcp`, port 9192. The TLS-enabled CUPS build
also advertises `_ipps._tcp`; that uses the IPP server's certificate, not the
browser proxy certificate. A printer web-service record and zero-port
`_printer._tcp` reservation can also appear; they do not create an LPD listener.

Relevant TXT attributes: `txtvers=1`, `qtotal=1`, `rp=ipp/print`,
`ty=Plotline Studios HP7585`, `pdl=application/pdf`, `Color=T`, `Duplex=F`,
`adminurl=https://plotline.tech.jonh.net/`, generated printer `UUID`, and `TLS=1.2`
for the TLS-capable build. This is not a claim of IPP Everywhere certification.

Allow household clients to reach TCP 9192 and UDP 5353 on the intended interface.
DNS-SD requires IPv4 224.0.0.251 / IPv6 ff02::fb multicast on the clients' link,
or an IaC-managed reflector across the guest/household VLAN boundary. Avahi's
interface allowlist and reflector/firewall policy belong to IaC. Stop the dev
advertisement at cutover to avoid duplicate printers or automatic name suffixes.
Explicit queue configuration works without discovery.

## Physical connection and ownership transfer

The existing serial-server Pi is **stain.jonh.net**, an original Raspberry Pi
Model B Rev 2 with a PL2303 USB adapter. Studios uses **SSH TCP 22**, currently
the restricted **`squidprint`** service account retained on the already-paired
Pi. SSH stdio carries protocol-2 newline-framed JSON byte-bridge requests; the
forced command connects to a local Unix socket. There is no publicly listening
raw serial TCP port. Serial is 9600 baud, raw 8N1 with RTS/CTS, on
`/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller-if00-port0`.
See the [bridge protocol](controller/appliance/README.md).

Current dev inputs, all ignored and private:

- `controller/state/private/stain-connection.json`
- `controller/state/private/stain-service-ed25519`
- `controller/state/private/stain-known-hosts`

Production configuration should use, for example:

```json
{
  "destination": "squidprint@stain.jonh.net",
  "identity_file": "/srv/plotline/private/stain-service-ed25519",
  "known_hosts_file": "/srv/plotline/private/stain-known-hosts",
  "plotting_enabled": true
}
```

Store this as `/srv/plotline/private/stain-connection.json` and point
`device_config` at it. Provision files separately, owned/readable by the stable
`plotline` UID: private directory 0700, key/config 0600, pinned host file 0600 or
0644. The installer checks readability but never reads private-key contents or
contacts stain. No keys or credential values are included in source or handoff.

OpenSSH uses a supplied Ed25519 identity, `IdentitiesOnly`, `BatchMode` and
`StrictHostKeyChecking` with the supplied host-key file. Establish/verify that
host key out of band; do not replace it with unchecked `ssh-keyscan` output.
The Pi authorized key forces the byte-bridge client and disallows shell and
forwarding. Prefer a dedicated production key, authorized explicitly on the Pi
during cutover; retaining the existing restricted account does not require
renaming or rebuilding the Pi. Private-key provisioning is IaC/operator work.

One Pi connection owns serial at a time, but that lock is not ownership policy:
dev could win the next connection. Before enabling production plotting:

1. Observe the old controller's plot completed and pen parked; resolve any
   uncertain run. Stop its services with its own idle-safe mechanism.
2. Stop dev's `plotline-studios-dev`, `plotline-cartographer` and
   `plotline-pdf-sink` after they are idle. Also retire/disable any older
   print_server controller that can use this Pi; coordinate that with IaC.
3. Perform the explicit state copy below. Remove the physical connection from
   dev, and preferably revoke its service key when a dedicated production key
   is authorized. Dev can continue preview-only on isolated development state.
4. Enable the production connection and publish/reconcile. Readiness does not
   touch hardware; an operator subsequently confirms loaded paper/pens and
   initiates the first plot. This handoff does not authorize those actions.

## Retained state, backup and recovery

**Every user-owned application artifact lives beneath the one retained mount.**
Its existence across guest recreation is required; it is not itself a backup.

| Retained path | Unique content |
| --- | --- |
| `studios/` | Uploaded PDFs/HPGL, inspections, print intents, immutable conversions, PDF/HPGL recipes, Pattern/Maze recipes/thumbnails/source snapshots, carousel, calibration scans/results, job settings |
| `studios/plots/` | Immutable attempts, exact prepared programs, receipts, interruption/completion evidence and timing traces |
| `maps/` | Named maps and revisions, thumbnails, preparations/submission receipts, retained export recipes and source provenance |
| `maps/map-sources-http/` | Downloaded vector/terrain/boundary inputs retained for local reuse; excluded from this deployment's backups |
| `ipp/` | Original captures and intake receipts; unmatched spool files must be preserved |
| `private/` | Separately provisioned connection and SSH files |
| `state-schema.json`, `installation.json` | Application compatibility and observed-installation records |
| `cache/` | Rebuildable exports, decoded geometry, raster intermediates and XDG cache; exclude from backups |

`home/` is the service home (including any SSH control socket/CUPS runtime
configuration). Include it conservatively; no user documents should be placed
there. `admission.lock` is coordination, not a user artifact; never replace its
inode while services run. Preserve stable UID/GID ownership in backups and use
the same configured numeric IDs on recreation. The mount root can remain
root-owned; application directories are `plotline`-owned, mode 0750, and files
are normally private via umask 0077.

The current cleanup policy caps rebuildable Studio previews at 512 MiB/7 days
and Map exports/decoded geometry at 512 MiB each/7 days, with a 15-minute active
grace period. Raw timing logs have a 64-MiB/90-day retention policy. Documents,
exact plot programs, receipts, user scans and retained map source inputs are
not a bounded cache and will grow. Back up timing traces sooner if their full
history is desired. Leave scratch capacity beyond those steady-state cache caps;
active tasks and intermediate results can be much larger.

For a consistent backup, use the installer `stop` action, honor exit 75, snapshot
the retained mount, then `systemctl start plotline.target`. A filesystem snapshot
is preferable to a prolonged file copy. Retain `/etc/plotline/config.json` with
the backup or recreate it from IaC. Exclude `cache/` and `/opt/plotline` from data
backup; source releases and toolchains can be rebuilt. Jon's IaC also excludes
`maps/map-sources-http/`: provider downloads can be refetched after loss and are
not treated as unique user data. Providers can change, so a restored recipe may
use different provider bytes. Authored documents, recipes, inventory/calibration,
prepared plot programs and physical receipts remain protected. Do not claim a live
directory copy is transactionally consistent across multi-file job publication.

State envelope version is currently **1**. The installer rejects other versions;
it does not silently down-migrate. Existing application migrations are additive
and retain immutable source/attempt records. Rollback is supported between
releases declaring this compatible envelope and using the same configuration;
an older release predating this installer/admission protocol is not an automatic
rollback target. Future incompatible migrations must bump the envelope, provide
an explicit migration/backup plan, and may require restoring the matching state
snapshot. Never replay a physical plot to recover a service.

### Initial migration: separate, not automatic

After the authorized idle/cutover window, copy (do not delete) these current dev
roots into the retained mount, preserving documents and receipts:

- `/root/plotline/controller/state/jobs/` → `studios/`
- `/root/plotline/worktrees/artistic-cartographer/state/cartographer/` → `maps/`
- `/root/plotline/state/print-sink/` → `ipp/`
- The explicitly selected private connection files → `private/`, repathing JSON.

The old worktree path above is still the Map service's **data path**; copying
its data does not reconnect its Git history. Preserve the old worktrees/private
archive unchanged. Do not copy old absolute cache symlinks. Start with empty
`cache/`; Map startup will create its link to `maps/map-sources-http`.
Apply production ownership to the copied files while both sides are stopped.
Inventory/calibration values come from the copied state, never a demo carousel.
Verify document counts, recipe reopening, inventory and receipt IDs before
enabling production plotting. Existing browser local storage belongs to the old
origin and does not transfer; saved server documents do. Reopen them through Open.

## Acceptance and remaining IaC decisions

The reproducible isolated guest procedure is in
[packaging/production/README.md](packaging/production/README.md). It installs an
exported revision with real systemd, tests repeated reconciliation, HTTP/IPP
admission, retained documents and inventory, and uses a fake transport for
completion/interruption safety. It must never run against production state.

IaC still needs to choose/provision the direct guest hostname/IP and multicast
path, browser access policy, backup schedule/retention, service UID/GID if 970
is occupied, and the coordinated old-controller/state/key cutover window.
Household discovery and the real SSH/plotter connection require that later
operator acceptance; neither is probed by installation or health checks.
