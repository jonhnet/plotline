#!/bin/sh
set -eu
sink_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$sink_dir/../.." && pwd)
# Separate transport directories prevent CUPS job IDs reused after a restart
# from overwriting an older submission. The callback also uses unique IDs.
sink_state=${PLOTLINE_SINK_STATE:-"$repo_dir/state/print-sink"}
export PLOTLINE_SINK_STATE="$sink_state"
export PLOTLINE_SINK_INTAKE=${PLOTLINE_SINK_INTAKE:-1}
export PLOTLINE_REVIEW_URL=${PLOTLINE_REVIEW_URL:-http://dev.iac.jonh.net:9191/}
export PLOTLINE_SINK_HOST_ALIAS=${PLOTLINE_SINK_HOST_ALIAS:-dev.iac.jonh.net}
mkdir -p "$sink_state/spool"
"$repo_dir/.venv/bin/python" "$sink_dir/capture.py" --clean-spool
sink_spool=$(mktemp -d "$sink_state/spool/session-XXXXXXXX")
sink_binary="$repo_dir/build/print-sink/ippeveprinter"
if [ ! -x "$sink_binary" ]; then
  echo 'Build the IPP server first: python3 tools/print_sink/build_server.py' >&2
  exit 1
fi
python3 "$sink_dir/capabilities.py" > "$sink_spool/printer.conf"
# No -n: CUPS otherwise requires the hostname to resolve for both IPv4
# and IPv6. Bind all interfaces; the explicit alias admits dev's ordinary DNS
# name in addition to CUPS's numeric/localhost/mDNS Host names.
exec "$sink_binary" -p "${PLOTLINE_SINK_PORT:-9192}" -r "${PLOTLINE_SINK_DISCOVERY:-_print}" -f application/pdf \
  -a "$sink_spool/printer.conf" -F application/pdf -k \
  -d "$sink_spool" \
  -c "$sink_dir/capture.sh" --no-web-forms "${PLOTLINE_SINK_NAME:-Plotline Studios HP7585}"
