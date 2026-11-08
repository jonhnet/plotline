#!/usr/bin/env python3
"""Inspect or explicitly write and verify a prepared image on a Linux card reader.

Without --confirm-device this only prints the destination and performs checks.
Never guesses a device, unmounts anything, or accepts a mounted disk/partition.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from build_image import sha256


def inspect_device(path, image_bytes):
    target = path.resolve(strict=True)
    info = target.stat()
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError("Destination must be a block device")
    # PATH does not enable lsblk's tree output (NAME would). Explicitly request
    # it so partitions are children, both for selection and mounted checks.
    result = json.loads(subprocess.check_output([
        "lsblk", "--json", "--tree", "--bytes", "--output", "PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS", str(target)], text=True))
    devices = result["blockdevices"]
    if len(devices) != 1 or devices[0]["type"] != "disk":
        raise ValueError("Select a whole card, not a partition")
    device = devices[0]
    if device["size"] < image_bytes:
        raise ValueError("Card is smaller than image")
    def mounted(node):
        return any(node.get("mountpoints") or []) or any(mounted(child) for child in node.get("children", []))
    if mounted(device):
        raise ValueError("Destination or a child is mounted/in use; no write attempted")
    return target, info.st_rdev, device


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--device", type=Path, required=True)
    parser.add_argument("--confirm-device", help="Repeat the exact canonical destination shown by inspection to erase it")
    args = parser.parse_args()
    image = args.image.resolve(strict=True)
    if not image.is_file():
        raise ValueError("Source must be a regular image file")
    manifest = json.loads(image.with_suffix(image.suffix+".json").read_text())
    if image.stat().st_size != manifest["image_bytes"] or sha256(image) != manifest["image_sha256"]:
        raise ValueError("Image checksum or size does not match its build manifest")
    target, device_number, info = inspect_device(args.device, image.stat().st_size)
    print(json.dumps(info, indent=2), flush=True)
    if args.confirm_device is None:
        print("Inspection only. Writing will ERASE " + str(target))
        print("To write this reviewed destination, repeat with --confirm-device " + str(target))
        return
    if args.confirm_device != str(target):
        raise ValueError("Confirmation does not match the canonical device")
    inspect_device(target, image.stat().st_size)
    fd = os.open(str(target), os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_rdev != device_number:
            raise ValueError("Device changed since inspection")
        with image.open("rb") as source:
            for block in iter(lambda: source.read(4*1024*1024), b""):
                remaining = memoryview(block)
                while remaining:
                    count = os.write(fd, remaining)
                    if count <= 0:
                        raise OSError("Card write made no progress")
                    remaining = remaining[count:]
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        remaining = image.stat().st_size
        while remaining:
            block = os.read(fd, min(4*1024*1024, remaining))
            if not block:
                raise OSError("Short card read-back")
            digest.update(block)
            remaining -= len(block)
        if digest.hexdigest() != manifest["image_sha256"]:
            raise ValueError("Card read-back checksum failed")
    finally:
        os.close(fd)
    print("Card written and read-back verified. Keep the original stain card for rollback.")


if __name__ == "__main__":
    main()
