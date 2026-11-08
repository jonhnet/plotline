import base64
import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("build_image", Path(__file__).with_name("build_image.py"))
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


class ImageTests(unittest.TestCase):
    def config(self):
        config = json.loads(Path(__file__).with_name("example.json").read_text())
        # Valid SSH wire encoding with test-only public bytes; no private material.
        wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
        key = "ssh-ed25519 " + base64.b64encode(wire).decode()
        config["admin_keys"] = [key]
        config["service_keys"] = [key]
        return config

    def test_seed_has_offline_payload_and_restricted_byte_bridge(self):
        config = self.config()
        config["network"]["wifis"] = {"wlan0": {"dhcp4": True, "access-points": {"a 'quoted' network": {"password": "test-only\nvalue"}}}}
        seed, manifest = image.make_seed(config)
        user = json.loads(seed["user-data"].split("\n", 1)[1])
        self.assertFalse(user["ssh_pwauth"])
        self.assertFalse(user["package_update"])
        self.assertNotIn("packages", user)
        service = user["users"][1]
        self.assertNotIn("sudo", service)
        self.assertFalse(service.get("system", False))  # cloud-init forbids keys on system users
        self.assertTrue(service["ssh_authorized_keys"][0].startswith('restrict,command="'))
        files = {f["path"]: base64.b64decode(f["content"]) for f in user["write_files"]}
        self.assertEqual(json.loads(files["/etc/plotline/device.json"]), config['device'])
        self.assertEqual(files["/opt/plotline/device.py"], Path(__file__).with_name("device.py").read_bytes())
        self.assertEqual(files["/opt/plotline/finalize_boot.py"], Path(__file__).with_name("finalize_boot.py").read_bytes())
        self.assertEqual(user["runcmd"][-1], ["python3", "/opt/plotline/finalize_boot.py", "apply"])
        self.assertNotIn("/etc/cloud/cloud-init.disabled", files)  # only after successful provisioning
        self.assertEqual(json.loads(seed["network-config"])["network"], config["network"])
        self.assertNotIn("test-only", json.dumps(manifest))

    def test_rejects_credentials_in_wrong_fields_and_drops_legacy_plot_policy(self):
        for key, value in [("hostname", "stain;rm"), ("admin_user", "root"),
                           ("service_keys", ["ssh-ed25519 x\ncommand=oops"]),
                           ("admin_keys", ["-----BEGIN OPENSSH PRIVATE KEY-----"])]:
            config = self.config()
            config[key] = value
            with self.assertRaises(ValueError):
                image.make_seed(config)
        config = self.config()
        config["device"]["plotting_enabled"] = True
        config['device']['limits'] = [-100, -100, 100, 100]
        seed, _ = image.make_seed(config)
        files = json.loads(seed['user-data'].split('\n',1)[1])['write_files']
        device = next(f for f in files if f['path']=='/etc/plotline/device.json')
        self.assertEqual(json.loads(base64.b64decode(device['content'])), {'device':config['device']['device']})

    def test_boot_partition_bounds_are_checked_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "disk.img"
            mbr = bytearray(512)
            mbr[510:] = b"\x55\xaa"
            mbr[450] = 0x0C
            struct.pack_into("<II", mbr, 454, 2048, 8192)
            with path.open("wb") as file:
                file.write(mbr)
                file.truncate((2048+8192)*512)
            self.assertEqual(image.boot_offset(path), 2048*512)
            with path.open("r+b") as file:
                file.truncate(4096)
            with self.assertRaises(ValueError):
                image.boot_offset(path)


if __name__ == "__main__":
    unittest.main()
