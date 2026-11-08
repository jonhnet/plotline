#!/usr/bin/env python3
"""Manage only the Plotline Studios development service; never installs on production."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = "plotline-studios-dev"
LEGACY_UNIT = "squidprint-dev"
UNIT_PATH = Path("/etc/systemd/system") / f"{UNIT}.service"
STATE = ROOT / "controller/state/jobs"
CONNECTION = ROOT / "controller/state/dev-device-config.json"


def run(*args):
    subprocess.run(args, check=True)


def require_idle(state=STATE):
    """Never interrupt PDF work, a live feed, or unconfirmed physical completion."""
    for kind in ('documents', 'conversions'):
        for path in (state / kind).glob('*/record.json'):
            record = json.loads(path.read_text())
            if record.get('status') in ('received', 'inspecting', 'queued', 'running'):
                raise SystemExit(f"Plotline Studios has active PDF work ({kind}/{path.parent.name}). Wait for it to finish before restarting; the service was left running.")
    active = state / "plots/active.json"
    if not active.exists():
        return
    attempt = json.loads(active.read_text())
    # Only canonical UUIDs are valid spool paths.
    from uuid import UUID
    if str(UUID(attempt)) != attempt:
        raise SystemExit("Invalid active plot ID; inspect the spool before restarting")
    record = json.loads((state / "plots" / attempt / "record.json").read_text())
    execution = record.get("execution")
    phase = record.get("phase")
    if ((execution and execution.get("state") in ("finished", "reset_acknowledged"))
            or (phase == "failed" and not execution)):
        return
    status = (execution or {}).get("state", phase)
    raise SystemExit(f"Plotline Studios is {status}. Finish the current plot and confirm its pen is parked before restarting; the service was left running.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "restart", "stop", "status"])
    connection = parser.add_mutually_exclusive_group()
    connection.add_argument("--device-config", type=Path, help="Enable the SSH plotter connection and remember it for restarts")
    connection.add_argument("--preview-only", action="store_true", help="Remove the remembered plotter connection")
    args = parser.parse_args()
    if args.action in ("start", "restart"):
        require_idle()
        # Paths are embedded in systemd syntax. Refuse ambiguous installations.
        if any(c.isspace() or c in "%\"\\" for c in str(ROOT)):
            parser.error("The development checkout path must not contain whitespace, quotes, backslashes or %")
        run("cargo", "build", "--locked", "--release", "--manifest-path", str(ROOT / "Cargo.toml"))
        run("cargo", "build", "--locked", "--release", "--manifest-path", str(ROOT / "controller/Cargo.toml"))
        STATE.mkdir(parents=True, exist_ok=True)
        require_idle()
        if args.preview_only:
            CONNECTION.unlink(missing_ok=True)
        if args.device_config:
            selected = args.device_config.resolve(strict=True)
            if any(c.isspace() or c in "%\"\\" for c in str(selected)):
                parser.error("Device configuration path cannot contain systemd metacharacters or whitespace")
            CONNECTION.write_text(json.dumps({"connection": str(selected)}) + "\n")
        hardware = ""
        if CONNECTION.exists():
            selected = Path(json.loads(CONNECTION.read_text())["connection"]).resolve(strict=True)
            if any(c.isspace() or c in "%\"\\" for c in str(selected)):
                parser.error("Invalid saved device configuration path")
            hardware = f" --device-config {selected}"
        UNIT_PATH.write_text(f"""[Unit]
Description=Plotline Studios HPGL controller — development only
After=network.target

[Service]
Type=simple
WorkingDirectory={ROOT}
Environment=PLOTLINE_IPP_INBOX={ROOT}/state/print-sink/captures
ExecStart={ROOT}/controller/target/release/plotline-studios --listen 0.0.0.0:9191 --state {STATE}{hardware}
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={STATE}
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
""")
        run("systemctl", "daemon-reload")
        require_idle()
        # Retire the old unit only after all build and idle checks succeed.
        if (UNIT_PATH.parent / f"{LEGACY_UNIT}.service").exists():
            run("systemctl", "disable", "--now", LEGACY_UNIT)
        run("systemctl", "enable", UNIT)
        require_idle()
        run("systemctl", "restart", UNIT)
        run("systemctl", "is-active", UNIT)
        print("Plotline Studios: http://dev.iac.jonh.net:9191/ — " + ("plotter connection configured" if hardware else "preview only"))
    elif args.action == "stop":
        require_idle()
        for unit in (UNIT, LEGACY_UNIT):
            if (UNIT_PATH.parent / f"{unit}.service").exists():
                run("systemctl", "disable", "--now", unit)
    else:
        run("systemctl", "status", "--no-pager", UNIT if UNIT_PATH.exists() else LEGACY_UNIT)


if __name__ == "__main__":
    main()
