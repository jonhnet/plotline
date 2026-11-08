#!/usr/bin/env python3
"""Make a Pi card image from a pinned stock image and offline cloud-init payload.

This only writes regular image files. No loop devices, mounts, chroots, package
downloads on first boot, or block-device access are needed. Requires mtools.
"""
import argparse
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = json.loads((HERE / "base-image.json").read_text())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_key(key):
    # Take bare public keys only; this script owns authorized_keys restrictions.
    if not isinstance(key, str) or "\n" in key or "\r" in key:
        raise ValueError("Public keys must be single lines")
    fields = key.split()
    if len(fields) < 2 or fields[0] not in ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"):
        raise ValueError("Expected an OpenSSH public key, not a key path or private key")
    decoded = base64.b64decode(fields[1], validate=True)
    if len(decoded) < 32 or decoded[:4] != struct.pack(">I", len(fields[0])) or decoded[4:4+len(fields[0])] != fields[0].encode():
        raise ValueError("Invalid public key")
    if fields[0] == "ssh-ed25519" and (len(decoded) != 51 or decoded[15:19] != struct.pack(">I", 32)):
        raise ValueError("Invalid Ed25519 public key")
    return key


def make_seed(config):
    hostname = config["hostname"]
    admin = config.get("admin_user", "pi")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", hostname):
        raise ValueError("Use a simple lowercase hostname")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", admin) or admin in ("root", "plotline"):
        raise ValueError("Invalid operator account")
    if not config["admin_keys"] or not config["service_keys"]:
        raise ValueError("Both operator and service public keys are required")
    admin_keys = [validate_key(key) for key in config["admin_keys"]]
    service_keys = ['restrict,command="/usr/bin/python3 /opt/plotline/device.py client" ' + validate_key(key)
                    for key in config["service_keys"]]
    if config["network"].get("version") != 2:
        raise ValueError("Network configuration must use Netplan version 2")
    device = {"device": config["device"]["device"]}
    if not device.get("device", "").startswith("/dev/serial/by-id/"):
        raise ValueError("Use the adapter's stable /dev/serial/by-id path")
    payload = {
        "/opt/plotline/device.py": ((HERE / "device.py").read_bytes(), "0755"),
        "/opt/plotline/finalize_boot.py": ((HERE / "finalize_boot.py").read_bytes(), "0755"),
        "/etc/systemd/system/plotline-device.service": ((HERE / "plotline-device.service").read_bytes(), "0644"),
        "/etc/plotline/device.json": ((json.dumps(device, indent=2) + "\n").encode(), "0644"),
    }
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", str(HERE)], cwd=ROOT, text=True))
    manifest = {"format": 1, "source_commit": revision, "source_dirty": dirty,
                "base_image": BASE, "payload_sha256": {path: hashlib.sha256(data).hexdigest() for path, (data, _) in payload.items()}}
    user_data = {
        "hostname": hostname, "manage_etc_hosts": True, "timezone": config.get("timezone", "Etc/UTC"),
        "enable_ssh": True, "ssh_pwauth": False, "disable_root": True,
        "ssh_deletekeys": True, "ssh_genkeytypes": ["ed25519", "rsa"],
        "package_update": False, "package_upgrade": False,
        "users": [
            {"name": admin, "groups": ["adm", "sudo"], "shell": "/bin/bash", "lock_passwd": True,
             "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": admin_keys},
            {"name": "plotline", "homedir": "/var/lib/plotline", "shell": "/bin/sh",
             "lock_passwd": True, "ssh_authorized_keys": service_keys},
        ],
        "write_files": [{"path": path, "owner": "root:root", "permissions": mode, "encoding": "b64",
                         "content": base64.b64encode(data).decode()} for path, (data, mode) in payload.items()],
        "runcmd": [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", "--now", "plotline-device.service"],
            ["python3", "/opt/plotline/finalize_boot.py", "apply"],
        ],
    }
    if config.get("host_key_files"):
        keys = config["host_key_files"]
        private_key = Path(keys["ed25519_private"]).read_text()
        public_key = validate_key(Path(keys["ed25519_public"]).read_text().strip())
        if not private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----") or not public_key.startswith("ssh-ed25519 "):
            raise ValueError("Expected a dedicated OpenSSH Ed25519 host key pair")
        derived = subprocess.check_output(["ssh-keygen", "-y", "-P", "", "-f", keys["ed25519_private"]], text=True)
        if derived.split()[:2] != public_key.split()[:2]:
            raise ValueError("Host private and public keys do not match")
        user_data["ssh_keys"] = {"ed25519_private": private_key, "ed25519_public": public_key}
        manifest["ssh_host_public_key"] = public_key
    user_data["write_files"].append({"path": "/etc/plotline/image-manifest.json", "owner": "root:root",
                                    "permissions": "0644", "encoding": "b64",
                                    "content": base64.b64encode((json.dumps(manifest, indent=2)+"\n").encode()).decode()})
    # JSON is a YAML subset; avoid templating shell text or Wi-Fi secrets.
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    meta = {"dsmode": "local", "instance_id": "plotline-" + hostname + "-" + config_digest[:12]}
    seed = {"user-data": "#cloud-config\n" + json.dumps(user_data, indent=2) + "\n",
            "meta-data": json.dumps(meta, indent=2) + "\n",
            "network-config": json.dumps({"network": config["network"]}, indent=2) + "\n"}
    return seed, manifest


def boot_offset(path):
    with Path(path).open("rb") as image:
        mbr = image.read(512)
    if mbr[510:512] != b"\x55\xaa":
        raise ValueError("Not an MBR disk image")
    partition = mbr[446:462]
    start, size = struct.unpack_from("<II", partition, 8)
    if partition[4] not in (0x0B, 0x0C) or not start or size < 8192:
        raise ValueError("Expected the first partition to be FAT32 boot")
    if (start + size) * 512 > Path(path).stat().st_size:
        raise ValueError("Boot partition extends beyond image")
    return start * 512


def cached_base(cache):
    cache.mkdir(parents=True, exist_ok=True)
    compressed = cache / "raspios-lite-armhf-2026-10-06.img.xz"
    image = compressed.with_suffix("")
    if not compressed.exists():
        temporary = compressed.with_suffix(".download")
        print("Downloading pinned Raspberry Pi OS Lite image", flush=True)
        with urllib.request.urlopen(BASE["url"], timeout=60) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        os.replace(str(temporary), str(compressed))
    if sha256(compressed) != BASE["download_sha256"]:
        raise ValueError("Downloaded OS checksum mismatch; cache was not used")
    if not image.exists():
        temporary = image.with_suffix(".extracting")
        print("Extracting stock image", flush=True)
        with lzma.open(compressed) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        os.replace(str(temporary), str(image))
    if image.stat().st_size != BASE["extract_size"] or sha256(image) != BASE["extract_sha256"]:
        raise ValueError("Extracted OS checksum mismatch")
    return image


def build(config_path, output, cache):
    os.umask(0o077)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; choose a new filename")
    if not shutil.which("mcopy") or not shutil.which("mtype"):
        raise ValueError("Install mtools before building: apt-get install mtools")
    config = json.loads(config_path.read_text())
    seed, manifest = make_seed(config)
    base = cached_base(cache)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".building")
    if temporary.exists():
        raise ValueError("An earlier .building file exists; inspect it before retrying")
    # Only ordinary files; this function never accepts a physical disk target.
    with temporary.open("xb") as destination, base.open("rb") as source:
        shutil.copyfileobj(source, destination, 1024 * 1024)
    try:
        offset = boot_offset(temporary)
        mtools_image = str(temporary) + "@@" + str(offset)
        with tempfile.TemporaryDirectory(prefix="plotline-seed-") as directory:
            for name, content in seed.items():
                local = Path(directory) / name
                local.write_text(content)
                os.chmod(str(local), 0o600)
                subprocess.run(["mcopy", "-o", "-i", mtools_image, str(local), "::/" + name], check=True)
                observed = subprocess.check_output(["mtype", "-i", mtools_image, "::/" + name])
                if observed != content.encode():
                    raise ValueError("Boot payload read-back failed: " + name)
        with temporary.open("rb") as image:
            os.fsync(image.fileno())
        manifest.update(image_sha256=sha256(temporary), image_bytes=temporary.stat().st_size,
                        hostname=config["hostname"], network_configuration="private; embedded in image",
                        hardware_boot_verified=False)
        os.replace(str(temporary), str(output))
        manifest_path = output.with_suffix(output.suffix + ".json")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        os.chmod(str(output), 0o600)
        print(str(output))
        print("SHA-256 " + manifest["image_sha256"])
        print("First boot provisions SSH and the device service; physical plotting remains disabled.")
        return manifest
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Private JSON configuration, outside Git")
    parser.add_argument("--output", type=Path, required=True, help="New .img regular file")
    parser.add_argument("--cache", type=Path, default=ROOT / "build/controller-image")
    args = parser.parse_args()
    build(args.config, args.output, args.cache)


if __name__ == "__main__":
    main()
