# Production installer and isolated acceptance

The deployment interface and operator boundary are in
[IAC_HANDOFF.md](../../IAC_HANDOFF.md). This installer targets an Ubuntu 24.04
x86-64 systemd guest. It is independent of the preview-only root Dockerfile.

Fast checks, including from a source export with no Git metadata:

```sh
python3 packaging/production/test_install.py
python3 controller/scripts/test-retention.py
```

The acceptance test below is deliberately destructive to **its disposable test
guest only**. It requires an explicit marker, no physical connection and disabled
LAN discovery. It temporarily substitutes `controller/scripts/fake-plotter.py`,
which has no network or serial-device access. Never copy a real connection file
or SSH credentials into this test guest.

## Fresh guest

Run from the source checkout. Use new container/volume names; do not reuse a
volume containing user documents. The additional container capability permits
systemd to create the production units' protected filesystem namespaces. It is
not a capability needed by Plotline's runtime processes.

```sh
podman build -f packaging/production/test.Dockerfile -t plotline-production-test .
mkdir -p build/production-export
git archive HEAD | tar -x -C build/production-export
podman volume create plotline-acceptance-data
podman run -d --name plotline-acceptance --hostname plotline-isolated \
  --systemd=always --cap-add=SYS_ADMIN --security-opt seccomp=unconfined \
  -v plotline-acceptance-data:/srv/plotline \
  -p 127.0.0.1:19291:9191 -p 127.0.0.1:19292:9192 \
  plotline-production-test
podman cp build/production-export/. plotline-acceptance:/export
podman exec plotline-acceptance python3 -c '
import json
from pathlib import Path
c=json.loads(Path("/export/packaging/production/config.example.json").read_text())
c.update(public_url="http://127.0.0.1:19291", ipp_hostname="plotline-isolated.local",
         ipp_name="Plotline isolated acceptance", ipp_discovery=False, device_config=None)
Path("/etc/plotline").mkdir(exist_ok=True)
Path("/etc/plotline/config.json").write_text(json.dumps(c))
Path("/etc/plotline/ISOLATED_TEST").write_text("disposable-no-hardware\n")
'
podman exec plotline-acceptance sh -c '
python3 /export/packaging/production/install.py install \
  --source /export --revision "$(cat /export/SOURCE_REVISION)" \
  --config /etc/plotline/config.json
'
```

The source export is the only application input; the first install downloads
toolchains/dependencies and builds both Rust executables, terrain geometry and
IPP. `SOURCE_REVISION` is expanded by `git archive`, not invented by the test.
The test image contains only Ubuntu, systemd, Python and basic trust certificates.

## Runtime checks

Incremental-build regressions can also run without a production guest:

```sh
python3 packaging/production/test_build_cache.py
```

They check persistent dependency caches across different revisions, immutable
installed executables, environment invalidation, and IPP binary/input integrity.
With local Cargo available, an offline two-revision fixture verifies that the
unchanged dependency is actually reused. No network or plotter is needed.

Production build caches live under `/opt/plotline/build-cache`, outside user
data and backup selection. New revisions reuse Cargo/native caches and the
patched IPP artifact; pip/npm retain downloaded dependencies. Builds use the
configured `build_jobs` (16 by default) and stream per-step timings. A first
cache-aware publication seeds the caches; later changes reuse them. Root-disk
loss can rebuild everything from source.

```sh
podman exec plotline-acceptance python3 /export/packaging/production/test_install.py
podman exec plotline-acceptance python3 /export/packaging/production/smoke.py \
  --source /export --config /etc/plotline/config.json --map
curl --fail http://127.0.0.1:19291/ >/dev/null
curl --fail http://127.0.0.1:19291/map >/dev/null
podman cp plotline-acceptance:/srv/plotline/production-test-results.json \
  build/production-test-results.json
```

Omit `--map` for the provider-independent test. It still checks Map's service,
pages and admission protocol. With `--map`, it additionally fetches public tiles,
renders the default map and verifies an active composition refuses maintenance.
Map-provider availability is not an installation readiness requirement.

The test checks:

- All five Studio pages and exact revision readiness; repeated installation
  without restarting healthy processes.
- An exclusive maintenance lock rejects Rust HTTP writes, Map requests and real
  IPP Print-Job requests with HTTP 503, while maintenance reads remain available.
- IPP capture stops at PDF review. Explicit pen conversion produces an HPGL
  draft. Receiving, converting and readiness never open even the fake serial port.
- A fake plot waiting for physical completion makes installation return 75,
  leaving service PIDs unchanged. A simulated crash retains uncertain completion
  without replay; explicit fixture reset acknowledgment permits reconciliation.
- Safe stop/start retains the uploaded document, carousel and plot records.

Inspect failures with `podman logs plotline-acceptance` and
`podman exec plotline-acceptance journalctl -u plotline-web -u plotline-map -u plotline-ipp`.
The script is intended for fresh state, not repeated insertion of fixtures into
the same library. Preserve failed-test evidence before creating a fresh volume.
Clean up only these disposable resources when finished:

```sh
podman stop plotline-acceptance
podman rm plotline-acceptance
podman volume rm plotline-acceptance-data
```

The broader controller tests (`cargo test --locked --manifest-path
controller/Cargo.toml`) include fake serial completion, pause/cancel, restart,
receipt and no-replay regressions. They also run from an export after the build
dependencies have been installed. No development archaeology or private artwork
is required by the production checks above.
