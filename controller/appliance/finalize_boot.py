#!/usr/bin/env python3
"""Finish offline provisioning: persist native NM profiles, retire cloud-init.

Run only on a provisioned, idle appliance. Does not restart networking. The
root-only journal retains credentials for rollback; never print its contents.
"""
import argparse
import base64
import configparser
import json
import os
from pathlib import Path
import subprocess
import uuid

JOURNAL = "var/lib/plotline-provision/boot-finalization.json"


def replace(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".plotline-tmp")
    with temporary.open("xb") as stream:
        os.chmod(temporary, mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def encoded(data):
    return base64.b64encode(data).decode()


def apply(root=Path("/")):
    journal = root / JOURNAL
    if journal.exists():
        state = json.loads(journal.read_text())
        if state["state"] == "applied":
            return
        raise ValueError("An unfinished journal exists; roll back before retrying")
    plans = []
    profiles = sorted((root / "run/NetworkManager/system-connections").glob("netplan-*.nmconnection"))
    if not profiles:
        raise ValueError("No rendered Netplan connections; leave provisioning enabled")
    identifiers = set()
    for profile in profiles:
        data = profile.read_bytes()
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(data.decode())
        identifier = str(uuid.UUID(config["connection"]["uuid"]))
        if identifier in identifiers or not config["connection"].get("type"):
            raise ValueError("Duplicate or incomplete rendered connection")
        identifiers.add(identifier)
        # Keep every setting and UUID; only the filename changes. The distro's
        # NM patch only round-trips filenames starting with 'netplan-' to YAML.
        target = "etc/NetworkManager/system-connections/plotline-" + identifier + ".nmconnection"
        if (root / target).exists():
            raise ValueError("Native destination already exists")
        plans.append({"path": target, "before": None, "after": encoded(data), "mode": 0o600})
    yaml_files = sorted(set((root / "etc/netplan").glob("*.yaml")) | set((root / "etc/netplan").glob("*.yml")))
    if not yaml_files:
        raise ValueError("No persistent Netplan configuration to migrate")
    for path in yaml_files:
        if path.is_symlink():
            raise ValueError("Refusing a symlink in Netplan configuration")
        plans.append({"path": str(path.relative_to(root)), "before": encoded(path.read_bytes()),
                      "after": None, "mode": path.stat().st_mode & 0o777})
    marker = root / "etc/cloud/cloud-init.disabled"
    plans.append({"path": str(marker.relative_to(root)),
                  "before": encoded(marker.read_bytes()) if marker.exists() else None,
                  "after": encoded(b"Provisioned by Plotline Studios; see /opt/plotline/finalize_boot.py\n"),
                  "mode": 0o600})
    journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(journal.parent, 0o700)
    state = {"format": 1, "state": "prepared", "files": plans}
    replace(journal, (json.dumps(state, indent=2) + "\n").encode())
    try:
        for entry in plans:
            path = root / entry["path"]
            if entry["after"] is None:
                path.unlink()
            else:
                replace(path, base64.b64decode(entry["after"]), entry["mode"])
        state["state"] = "applied"
        replace(journal, (json.dumps(state, indent=2) + "\n").encode())
    except BaseException:
        rollback(root)
        raise


def rollback(root=Path("/")):
    journal = root / JOURNAL
    state = json.loads(journal.read_text())
    # Refuse to overwrite subsequent operator changes. Validate the whole set
    # first, so a conflict never produces a partial rollback.
    for entry in state["files"]:
        path = root / entry["path"]
        current = encoded(path.read_bytes()) if path.exists() else None
        if current not in (entry["before"], entry["after"]):
            raise ValueError("Configuration changed since finalization: " + entry["path"])
    for entry in reversed(state["files"]):
        path = root / entry["path"]
        if entry["before"] is None:
            path.unlink(missing_ok=True)
        else:
            replace(path, base64.b64decode(entry["before"]), entry["mode"])
    journal.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "rollback"))
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the appliance")
    if args.action == "apply":
        for service in ("NetworkManager", "ssh", "plotline-device"):
            subprocess.run(["systemctl", "is-active", "--quiet", service], check=True)
        apply()
    else:
        rollback()
    os.sync()
    print("Boot finalization " + args.action + " complete; takes effect next boot")


if __name__ == "__main__":
    main()
