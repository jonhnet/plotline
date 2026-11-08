FROM docker.io/library/node:22-bookworm-slim AS node
FROM docker.io/library/rust:1.94.1-bookworm AS rust

FROM docker.io/library/ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Raster processing uses distribution OpenCV, never the Intel-IPP-enabled wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tini python3-venv python3-pil python3-numpy python3-scipy \
    python3-skimage python3-shapely python3-opencv python3-cairo \
    inkscape fonts-dejavu-core mupdf-tools \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/ /usr/local/
WORKDIR /opt/plotline

FROM runtime AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libgeos-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
ENV PATH=/usr/local/cargo/bin:$PATH RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
COPY requirements-*.txt package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund \
    && python3 -m venv .venv \
    && .venv/bin/pip install --no-cache-dir -r requirements-map.txt \
       -r requirements-contracts.txt -r requirements-cartographer.txt \
    && .venv/bin/pip check \
    && python3 -m venv /opt/calibration \
    && /opt/calibration/bin/pip install --no-cache-dir -r requirements-calibration.txt \
    && /opt/calibration/bin/pip check
# .dockerignore admits only public source files, never the host's state or keys.
COPY . .
RUN python3 packaging/container-check-source.py
ENV CARGO_TARGET_DIR=/tmp/plotline-target
RUN cargo build --locked --release \
    && cargo build --locked --release --manifest-path controller/Cargo.toml \
    && .venv/bin/python tools/build_cartographer_geometry.py \
    && mkdir -p target/release controller/target/release \
    && cp /tmp/plotline-target/release/plotline target/release/ \
    && cp /tmp/plotline-target/release/plotline-studios controller/target/release/ \
    && rm -rf build

FROM runtime
LABEL org.opencontainers.image.title="Plotline Studios" \
      org.opencontainers.image.source="https://github.com/jonhnet/plotline" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=build /opt/plotline /opt/plotline
COPY --from=build /opt/calibration /opt/calibration
RUN useradd --create-home --uid 10001 plotline \
    && mkdir -p /data/cache /data/studios /data/maps \
    && chown -R plotline:plotline /data \
    && ln -s /data/cache /opt/plotline/build
ENV PLOTLINE_PDF_PROJECT=/opt/plotline \
    PLOTLINE_MAP_UPSTREAM=http://127.0.0.1:9193 \
    PLOTLINE_CALIBRATION_PYTHON=/opt/calibration/bin/python \
    XDG_CACHE_HOME=/data/cache/xdg
USER plotline
EXPOSE 9191
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "packaging/container-entrypoint.py"]
