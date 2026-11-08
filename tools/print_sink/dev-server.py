#!/usr/bin/env python3
"""Manage the discoverable IPP intake on this development guest only."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
UNIT = 'plotline-pdf-sink'
UNIT_PATH = Path('/etc/systemd/system') / f'{UNIT}.service'
STATE = ROOT / 'state/print-sink'


def run(*args):
    subprocess.run(args, check=True)


def require_idle():
    if subprocess.run(['systemctl', 'is-active', '--quiet', UNIT]).returncode:
        return
    result = subprocess.run(['ipptool', '-tv', 'ipp://127.0.0.1:9192/ipp/print',
                             '/usr/share/cups/ipptool/get-printer-attributes.test'],
                            capture_output=True, text=True, timeout=15)
    if result.returncode or not re.search(r'printer-state \(enum\) = idle\b', result.stdout):
        raise SystemExit('IPP intake is busy or cannot be checked. Left it running; retry after capture finishes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'restart', 'stop', 'status'])
    args = parser.parse_args()
    if args.action == 'status':
        run('systemctl', 'status', '--no-pager', UNIT)
        return
    require_idle()
    if args.action == 'stop':
        run('systemctl', 'disable', '--now', UNIT)
        return
    if any(c.isspace() or c in '%"\\' for c in str(ROOT)):
        parser.error('Checkout path cannot contain whitespace or systemd metacharacters')
    run('cargo', 'build', '--locked', '--release', '--manifest-path', str(ROOT/'controller/Cargo.toml'))
    run('python3', str(ROOT/'tools/print_sink/build_server.py'))
    run('systemctl', 'is-active', 'avahi-daemon')
    STATE.mkdir(parents=True, exist_ok=True)
    require_idle()
    # Replace the original experiment's transient unit with a restartable dev unit.
    fragment = subprocess.run(['systemctl', 'show', '-p', 'FragmentPath', '--value', UNIT],
                              capture_output=True, text=True, check=True).stdout.strip()
    if '/run/systemd/transient/' in fragment:
        run('systemctl', 'stop', UNIT)
    UNIT_PATH.write_text(f'''[Unit]
Description=Plotline Studios discoverable PDF intake — development only
After=network.target avahi-daemon.service
Wants=avahi-daemon.service

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={ROOT}/tools/print_sink/run.sh
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
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK

[Install]
WantedBy=multi-user.target
''')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', UNIT)
    require_idle()
    run('systemctl', 'restart', UNIT)
    run('systemctl', 'is-active', UNIT)
    print('Plotline Studios HP7585: ipp://10.110.44.18:9192/ipp/print; review http://dev.iac.jonh.net:9191/')


if __name__ == '__main__':
    main()
