# Container packaging

The root Dockerfile builds all five Studios on Ubuntu 24.04, with Node 22 and
Rust 1.94.1 in the build stages. The runtime contains the locked Python/npm
dependencies, distribution OpenCV, converters, native map geometry extension
and source. Rust and C/C++ build tools stay in the build stage. There is no
prebuilt registry image.

Validated with Podman 4.9.3 on Ubuntu 24.04, x86-64. Docker and other host
platforms have not been tested. This development guest lacks `/dev/net/tun`,
so its build uses `podman build --network=host`; runtime checks use ordinary
bridge networking and a published loopback port.

`container-entrypoint.py` supervises the web server and map worker. Tini handles
PID 1 and orphan reaping. Either service exiting stops its peer and returns a
failure; SIGINT/SIGTERM stops both process groups, including converters. The
healthcheck helper checks both HTTP listeners without contacting a map provider or
plotter. The worker binds loopback; only port 9191 is exposed. Configure links
back to the authoring Studios with `PLOTLINE_PUBLIC_URL` when changing the
browser address.

The launcher accepts no device arguments. It has no connection JSON, SSH keys,
serial-device mounts, or IPP process. Hardware integration remains an explicit
source-installation step. The real transport boundary is
[`controller/src/serial.rs`](../controller/src/serial.rs); the Pi's restricted
key forwards that connection to [`device.py`](../controller/appliance/device.py).

`.dockerignore` is a deny-by-default allowlist of individual public source files.
This also protects builds from an existing development tree with private state
and archived inputs. After staging new runtime files, refresh it:

```sh
python3 packaging/container-context.py
```

The image never imports the developer's binaries, virtualenv, state, carousel or
SSH configuration. Source remains readable under `/opt/plotline`; persistent
documents and caches are under `/data`. Back up the named volume while stopped.

Check a running container with:

```sh
podman exec plotline python3 packaging/container-entrypoint.py --healthcheck
```

For the isolated integration check:

```sh
podman build -t plotline .
npm ci --prefix controller
npx --prefix controller playwright install chromium
node controller/scripts/container-smoke.mjs
```

This checks offline startup, non-root execution, source exclusions, PDF
inspection/conversion and HPGL registration, mixed raster/vector conversion,
all five browser pages, Pattern/Maze exports, a live map preview, document
persistence across container replacement and shutdown when a worker fails.
It creates and removes its own volume, requires Internet access for the map,
and never contacts a real plotter.
