#!/usr/bin/env python3
"""Upload generated HPGL to Plotline Studios's library; never send it to a plotter.

Only Python's standard library is required. Stdout is one JSON result, suitable
for a generator's build log; errors go to stderr and exit nonzero.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


MAX_BYTES = 64 * 1024 * 1024
LAYOUT_FIELDS = {
    "paper_x_mm", "paper_y_mm", "margins_mm", "window", "rotation", "facing",
}


def request(server, path, *, method="GET", data=None, content_type=None):
    headers = {"X-Plotline-Request": "1"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(server + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hpgl", type=Path)
    parser.add_argument("--server", default="http://dev.iac.jonh.net:9191",
                        help="Plotline Studios base URL (default: dev on port 9191)")
    parser.add_argument("--name", help="Library name; defaults to the source filename")
    parser.add_argument("--units-per-mm", type=float, default=40,
                        help="Source coordinate scale; default 40, no rescaling")
    parser.add_argument("--layout", type=Path,
                        help="JSON paper/orientation defaults, applied to new jobs only")
    parser.add_argument("--update-layout", action="store_true",
                        help="Also apply --layout to a reused job, preserving ink controls")
    args = parser.parse_args()
    server = args.server.rstrip("/")
    url = urllib.parse.urlsplit(server)
    if (url.scheme not in ("http", "https") or not url.hostname
            or url.query or url.fragment or url.username or url.password):
        parser.error("--server must be an http(s) base URL without credentials, query or fragment")
    if not math.isfinite(args.units_per_mm) or args.units_per_mm <= 0:
        parser.error("--units-per-mm must be finite and positive")
    if args.update_layout and not args.layout:
        parser.error("--update-layout requires --layout")

    layout = None
    if args.layout:
        layout = json.loads(args.layout.read_text())
        if not isinstance(layout, dict) or not layout or set(layout) - LAYOUT_FIELDS:
            raise ValueError("Layout must be a nonempty JSON object containing only: "
                             + ", ".join(sorted(LAYOUT_FIELDS)))
    # Read once so the hash describes exactly the bytes sent, even if a generator
    # replaces its output during the request. Bound memory before uploading.
    with args.hpgl.open("rb") as source:
        body = source.read(MAX_BYTES + 1)
    if not 1 <= len(body) <= MAX_BYTES:
        raise ValueError("HPGL file must be between 1 byte and 64 MiB")
    digest = hashlib.sha256(body).hexdigest()
    jobs = request(server, "/api/jobs")
    job = next((j for j in jobs if j["sha256"] == digest
                and j["units_per_mm"] == args.units_per_mm), None)
    reused = job is not None
    if not reused:
        query = urllib.parse.urlencode({"name": args.name or args.hpgl.name,
                                        "units_per_mm": args.units_per_mm})
        job = request(server, "/api/jobs?" + query, method="POST", data=body,
                      content_type="application/octet-stream")
    preview_url = server + "/hpgl#" + job["id"]
    layout_applied = layout is not None and (not reused or args.update_layout)
    if layout_applied:
        try:
            path = "/api/jobs/" + job["id"] + "/setup"
            setup = request(server, path)
            setup.update(layout)
            request(server, path, method="PUT", data=json.dumps(setup).encode(),
                    content_type="application/json")
        except (OSError, ValueError, RuntimeError) as error:
            raise RuntimeError(f"HPGL is in the library at {preview_url}, but layout was not saved: "
                               f"{error}. Fix the layout and retry with --update-layout.") from error
    print(json.dumps({
        "id": job["id"], "name": job["name"], "url": preview_url,
        "sha256": digest, "reused": reused, "layout_applied": layout_applied,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"import-job: {error}", file=sys.stderr)
        sys.exit(1)
