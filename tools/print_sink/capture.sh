#!/bin/sh
set -eu
sink_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$sink_dir/../../.venv/bin/python" "$sink_dir/capture.py" "$@"
