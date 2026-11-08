# Run Plotline Studios

Start with the browser studios; a plotter is optional. The maintained setup is
Ubuntu 24.04, Python 3.12, Node 20 and a current stable Rust toolchain (tested
with Rust 1.94.1). Windows is untested.

The web server contains Map, Pattern, Maze, PDF and HPGL Studios. Map composition
runs in a second process. Optional IPP intake is a third process; a Raspberry Pi
can provide the serial connection to the plotter.

## Try it with Podman

From a checkout, build and launch all five Studios:

```sh
podman build -t plotline . && podman run --rm --name plotline -p 127.0.0.1:9191:9191 -v plotline-data:/data plotline
```

Open **http://localhost:9191/**. The first build downloads dependencies and
compiles the converters; subsequent builds reuse cached layers. This builds an
image locally, rather than pulling a published Plotline image. The same
Dockerfile and command work with Docker by substituting `docker` for `podman`.

The container includes Map, Pattern, Maze, PDF and HPGL Studios, PDF conversion,
raster screening and pen-scan analysis. It runs as an unprivileged user. Map
tiles and boundary searches need Internet access. Pattern/Maze generation and
local PDF conversion work offline.

This launcher has **no plotter connection and no IPP listener or advertisement**.
Upload PDFs in the browser, prepare pen previews and download PDF/HPGL output.
The source installation below supports optional IPP and a Pi serial bridge.

Ctrl-C or `podman stop plotline` stops both services. Run the command again to
reopen your documents: the named `plotline-data` volume survives container
removal. Back up that volume; `/data/studios` holds PDF/HPGL/Pattern/Maze
documents and carousel settings, `/data/maps` holds Map documents and source
snapshots, and `/data/cache` holds regenerable output. Do not run two containers
against the same volume simultaneously. Stop the container before backing it up.

For a different browser address, set `-e PLOTLINE_PUBLIC_URL=http://HOST:PORT`
and adjust `-p` to match (for example, `-p 127.0.0.1:9291:9191` for another local
port). For trusted LAN access, bind a LAN interface instead of `127.0.0.1` and
use that hostname in the public URL. There is no login: keep access on a trusted
network. The map worker remains internal to the container.

Container maintenance and validation are in [packaging/README.md](../packaging/README.md).

## Build from source

Run these commands from the checkout root, as your normal user. Install Rust
with Cargo, rustfmt and Clippy using [rustup](https://rustup.rs/) first.

```sh
sudo apt-get install build-essential pkg-config libgeos-dev nodejs npm \
  python3-venv python3-pil python3-numpy python3-scipy python3-skimage \
  python3-shapely python3-opencv python3-cairo inkscape fonts-dejavu-core \
  openssh-client mupdf-tools
npm ci --ignore-scripts --no-audit --no-fund
python3 -m venv .venv
.venv/bin/pip install -r requirements-map.txt -r requirements-contracts.txt \
  -r requirements-cartographer.txt
.venv/bin/pip check
cargo build --locked --release
cargo build --locked --release --manifest-path controller/Cargo.toml
.venv/bin/python tools/build_cartographer_geometry.py
```

Use a fresh virtual environment. The raster worker deliberately uses distribution
Python and OpenCV; do not substitute the PyPI OpenCV wheel containing Intel IPP.
The native map geometry extension speeds up terrain; its Python implementation
also works if you omit the last command.

The controller build also generates and embeds Pattern Studio's gallery images
using Node and the root npm dependencies installed above. No browser is needed
for this build step; the running server serves ordinary cacheable PNGs.

Optional scan calibration uses its own environment:

```sh
python3 -m venv build/calibration-venv
build/calibration-venv/bin/pip install -r requirements-calibration.txt
```

## Start the studios

In one terminal, from the checkout root:

```sh
PLOTLINE_PDF_PROJECT="$PWD" PLOTLINE_MAP_UPSTREAM=http://127.0.0.1:9193 \
  controller/target/release/plotline-studios \
  --listen 127.0.0.1:9191 --state controller/state/jobs
```

In another:

```sh
PLOTLINE_MAP_PUBLIC_URL=http://localhost:9191/map \
  .venv/bin/python -m cartographer.service \
  --host 127.0.0.1 --port 9193 \
  --state state/cartographer --build build/cartographer \
  --studios http://127.0.0.1:9191 --studios-public http://localhost:9191
```

Open **http://localhost:9191/**. Start in Pattern or Maze Studio, or upload a PDF.
Define some pen colors and diameters in HPGL Studio's carousel to try conversion.
No hardware connection is configured by these commands. Preparing a plot never
starts the physical plotter.

To try a README example, download its recipe file. In Pattern or Maze Studio,
choose **Open → Import recipe**; in Map Studio choose **Open → Import document**.
Imports open a new unsaved tab, keeping the file's paper, colors and pen widths.
Adjust those inks to suit your pens, then Save. Pattern and Maze offer
**Download recipe** under **PDF & other options** for sharing your own designs.

Map Studio fetches and caches OpenStreetMap vector tiles. Uncached data and
boundary search require Internet access; no API key is needed. Terrain and
administrative-boundary coverage depend on the providers used by Map Studio.

To use another computer's browser, choose a trusted LAN hostname, bind the
Studios server to that host's interface, and use that hostname in both public
URLs above. Keep the map worker on loopback. The application has no user login;
do not expose it directly to the public Internet.

Keep `controller/state/` and `state/cartographer/` backed up: they hold documents,
recipes, carousel settings and plot records. `build/` holds regenerable output
and caches. Closing the browser does not stop a conversion or a physical plot;
keep the Studios host awake while plotting.

## Add desktop printing over IPP

PDF upload works without IPP. For desktop applications, build the bundled CUPS
intake tool and enable Avahi on the Studios host:

```sh
sudo apt-get install patch libavahi-client-dev avahi-daemon avahi-utils \
  libssl-dev zlib1g-dev cups-ipp-utils cups-filters
python3 tools/print_sink/build_server.py
```

Start the Studios server with `PLOTLINE_IPP_INBOX="$PWD/state/print-sink/captures"`
in its environment. Then, in another terminal (replace `plotline.local` with
your host's LAN name):

```sh
PLOTLINE_SINK_HOST_ALIAS=plotline.local \
PLOTLINE_REVIEW_URL=http://plotline.local:9191/ \
  tools/print_sink/run.sh
```

The printer advertises over mDNS/DNS-SD on the local network. Allow TCP 9192 and
UDP 5353 on that trusted network. Discovery does not usually cross routers; the
explicit queue URI is `ipp://plotline.local:9192/ipp/print`. Submitted PDFs stop
in PDF Studio for conversion choices. They never start physical plotting.

See the [IPP guide](../tools/print_sink/README.md) for capture and discovery details.

## Connect a plotter

The tested device is an HP 7585B, using a Raspberry Pi and a USB-to-serial
adapter with RTS/CTS flow control. The Studios host connects to the Pi over
SSH; serial traffic does not pass through the browser.

The [Pi bridge guide](../controller/appliance/README.md#build-a-fresh-card)
includes a pinned image builder and configuration example. Supply your own
network settings, public keys and `/dev/serial/by-id/…` adapter path. Keep keys,
connection files and provisioned images outside Git. Existing Raspberry Pi
installations can use the bridge installer described in that guide.

After pairing, start the Studios server with `--device-config /path/to/connection.json`.
In HPGL Studio record the pens and loaded sheet, review placement, and click
**Plot**. Other plotters need their own validated device control and paper profiles.

## Development checks

```sh
cargo test --locked
cargo test --locked --manifest-path controller/Cargo.toml
npm test
.venv/bin/python -m unittest discover -s tests -p test_print_contract.py
.venv/bin/python -m unittest discover -s tests -p test_pdf_driver.py
.venv/bin/python -m unittest discover -s tests -p 'test_cartographer*.py'
python3 -m unittest discover -s tests -p test_pdf_raster_renderer.py
```

Browser checks use `npm ci --prefix controller` and Playwright; see
[controller validation](../controller/README.md#validation). They use isolated
state and fake transports. The development archive and its historical PDF
corpus are not part of the public source package.
