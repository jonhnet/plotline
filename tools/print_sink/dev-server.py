#!/usr/bin/env python3
"""Inspect or retire the old dev IPP service. Starting it is no longer supported."""
import argparse
import re
import subprocess

UNIT = 'plotline-pdf-sink'


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
    if args.action in ('start', 'restart'):
        parser.error('The household IPP queue belongs to production. Use controller/scripts/ipp-flow-smoke.mjs for an isolated, non-advertising test.')
    require_idle()
    if args.action == 'stop':
        run('systemctl', 'disable', '--now', UNIT)
        return


if __name__ == '__main__':
    main()
