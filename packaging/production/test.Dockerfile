# Disposable Ubuntu guest for the exported-snapshot installation checks.
FROM docker.io/library/ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive container=podman
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv dbus python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
