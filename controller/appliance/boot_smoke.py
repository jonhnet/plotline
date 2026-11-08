#!/usr/bin/env python3
"""Boot a disposable ARMv6 copy under QEMU and check provisioning and agent RPC.

This cannot verify stain's real Wi-Fi dongle, USB adapter, cable or plotter.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from build_image import boot_offset

CHECK = r'''
import hashlib,json,pathlib,socket,subprocess,time,traceback
result={"ok":False}
try:
    for _ in range(100):
        if pathlib.Path("/run/plotline/device.sock").exists(): break
        time.sleep(.1)
    subprocess.run(["systemctl","is-active","--quiet","plotline-device"],check=True)
    subprocess.run(["systemctl","is-active","--quiet","ssh"],check=True)
    with socket.socket(socket.AF_UNIX) as s:
        s.connect("/run/plotline/device.sock")
        s.sendall(b'{"protocol":2,"op":"status"}\n')
        with s.makefile("rb") as r: response=json.loads(r.readline())
    assert response["ok"] and response["result"]["protocol"] == 2
    assert response["result"]["busy"] is False
    assert pathlib.Path("/etc/cloud/cloud-init.disabled").exists()
    finalized=json.loads(pathlib.Path("/var/lib/plotline-provision/boot-finalization.json").read_text())
    assert finalized["state"]=="applied"
    assert list(pathlib.Path("/etc/NetworkManager/system-connections").glob("plotline-*.nmconnection"))
    assert not list(pathlib.Path("/etc/netplan").glob("*.yaml"))
    manifest=json.loads(pathlib.Path("/etc/plotline/image-manifest.json").read_text())
    for name,digest in manifest["payload_sha256"].items():
        assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()==digest,name
    key=pathlib.Path("/var/lib/plotline/.ssh/authorized_keys").read_text()
    assert 'restrict,command="/usr/bin/python3 /opt/plotline/device.py client"' in key
    if pathlib.Path("/root/test-service-key").exists():
        # A supplied remote shell command must still run the forced RPC client.
        ssh=subprocess.run(["ssh","-T","-i","/root/test-service-key","-o","IdentitiesOnly=yes",
            "-o","BatchMode=yes","-o","StrictHostKeyChecking=accept-new","plotline@127.0.0.1","id"],
            input='{"protocol":2,"op":"status"}\n',capture_output=True,text=True,timeout=20)
        assert ssh.returncode==0,ssh.stderr
        assert json.loads(ssh.stdout)["result"]["protocol"] == 2
        result["restricted_ssh_rpc"]=True
    host=manifest.get("ssh_host_public_key")
    if host:
        assert pathlib.Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()[:2]==host.split()[:2]
    cloud=subprocess.run(["cloud-init","status","--format","json"],capture_output=True,text=True)
    cloud_status=json.loads(cloud.stdout)
    assert not cloud_status["errors"],cloud_status
    warnings=cloud_status.get("recoverable_errors",{})
    assert all(kind=="WARNING" and all("Could not find module named cc_netplan_nm_patch" in text for text in values)
               for kind,values in warnings.items()),warnings
    result.update(ok=True,agent=response["result"],cloud_init=cloud_status,
                  operator=subprocess.check_output(["getent","passwd","pi"],text=True).strip())
except Exception:
    result["error"]=traceback.format_exc()
    result["service_log"]=subprocess.check_output(["journalctl","-u","plotline-device","-n","30","--no-pager"],text=True)
out=pathlib.Path("/boot/firmware/plotline-smoke.json")
out.write_text(json.dumps(result,indent=2)+"\n")
subprocess.run(["sync"],check=True)
subprocess.run(["systemctl","poweroff","--no-block"],check=True)
'''

UNIT = """[Unit]
Description=Disposable image boot test
After=cloud-final.service plotline-device.service
Requires=plotline-device.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/plotline/boot-smoke.py
[Install]
WantedBy=multi-user.target
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--service-identity", type=Path,
                        help="Optional matching test SSH identity, embedded ONLY in the disposable copy")
    parser.add_argument("--qemu8-workaround", action="store_true",
                        help="Disable unimplemented power-controller nodes in the emulator-only DTB")
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "boot-test.img"
    if target.exists():
        raise SystemExit("Boot-test image exists; choose a new output directory")
    shutil.copyfile(args.image, target)
    with target.open("r+b") as file:
        file.truncate(4*1024**3)  # QEMU's SD card model requires a power-of-two size.
    offset = boot_offset(target)
    fat = str(target) + "@@" + str(offset)
    for name in ["kernel.img", "bcm2708-rpi-zero.dtb", "initramfs"]:
        subprocess.run(["mcopy", "-o", "-i", fat, "::/" + name, str(args.output / name)], check=True)
    if args.qemu8_workaround:
        # QEMU 8.2 cannot provide the current downstream kernel's PM resources.
        # This DTB is passed directly to QEMU; the card image is not patched.
        for node in ["/soc/watchdog@7e100000", "/soc/power"]:
            subprocess.run(["fdtput", "-t", "s", str(args.output / "bcm2708-rpi-zero.dtb"), node, "status", "disabled"], check=True)
    user = subprocess.check_output(["mtype", "-i", fat, "::/user-data"]).decode()
    config = json.loads(user.split("\n", 1)[1])
    if args.service_identity:
        config["write_files"].append({"path":"/root/test-service-key","owner":"root:root","permissions":"0600",
                                      "encoding":"b64","content":base64.b64encode(args.service_identity.read_bytes()).decode()})
    for path, content in [("/opt/plotline/boot-smoke.py", CHECK),
                          ("/etc/systemd/system/plotline-boot-test.service", UNIT)]:
        config["write_files"].append({"path": path, "owner": "root:root", "permissions": "0644",
                                      "encoding": "b64", "content": base64.b64encode(content.encode()).decode()})
    config["runcmd"].append(["systemctl", "enable", "--now", "--no-block", "plotline-boot-test.service"])
    with tempfile.TemporaryDirectory(prefix="plotline-boot-test-") as temp:
        seed = Path(temp) / "user-data"
        seed.write_text("#cloud-config\n" + json.dumps(config) + "\n")
        subprocess.run(["mcopy", "-o", "-i", fat, str(seed), "::/user-data"], check=True)
    log = args.output / "console.log"
    command = ["qemu-system-arm", "-M", "raspi0", "-m", "512", "-display", "none", "-monitor", "none",
               "-serial", "file:"+str(log), "-no-reboot", "-kernel", str(args.output / "kernel.img"),
               "-dtb", str(args.output / "bcm2708-rpi-zero.dtb"), "-initrd", str(args.output / "initramfs"),
               "-drive", "file="+str(target)+",format=raw,if=sd",
               "-append", "console=ttyAMA0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 rw rootwait fsck.repair=yes dwc_otg.fiq_fsm_enable=0 systemd.mask=systemd-networkd-wait-online.service systemd.mask=NetworkManager-wait-online.service"]
    print("Booting ARMv6 image; console: " + str(log), flush=True)
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
        try:
            deadline = time.monotonic() + args.timeout
            while process.poll() is None and time.monotonic() < deadline:
                if log.exists() and b"Power off not available: System halted instead" in log.read_bytes():
                    # With the old-QEMU PM workaround, the guest cannot power off
                    # its emulated board. It has synced/unmounted at this point.
                    process.terminate()
                    break
                time.sleep(1)
            if process.poll() is None and time.monotonic() >= deadline:
                process.terminate()
                process.communicate(timeout=10)
                raise SystemExit("Boot test timed out; inspect " + str(log))
            _, error = process.communicate(timeout=10)
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.communicate()
            raise
        if process.returncode not in (0, -15):
            raise SystemExit(error.decode())
    result = subprocess.check_output(["mtype", "-i", fat, "::/plotline-smoke.json"])
    (args.output / "result.json").write_bytes(result)
    parsed = json.loads(result)
    print(json.dumps(parsed, indent=2))
    if not parsed["ok"]:
        raise SystemExit("Guest provisioning test failed")


if __name__ == "__main__":
    main()
