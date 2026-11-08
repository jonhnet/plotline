import copy
import json
from pathlib import Path
import shutil
import stat
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import flash_image


CARD = {"path": "/dev/sdb", "type": "disk", "size": 64 * 1024**3,
        "mountpoints": [None], "children": [
            {"path": "/dev/sdb1", "type": "part", "size": 63 * 1024**3,
             "mountpoints": [None]}]}


class FlashTests(unittest.TestCase):
    def inspect(self, devices):
        path = Mock()
        target = path.resolve.return_value
        target.stat.return_value = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=123)
        with patch.object(flash_image.subprocess, "check_output", return_value=json.dumps({"blockdevices": devices})):
            return flash_image.inspect_device(path, 3 * 1024**3)

    def test_accepts_unmounted_whole_card_with_existing_partition(self):
        _, number, info = self.inspect([CARD])
        self.assertEqual(number, 123)
        self.assertEqual(info, CARD)

    def test_refuses_mounted_disk_partition_or_descendant(self):
        for depth in range(3):
            with self.subTest(depth=depth):
                card = copy.deepcopy(CARD)
                card["children"][0]["children"] = [{"type": "crypt", "mountpoints": [None]}]
                node = card
                for _ in range(depth):
                    node = node["children"][0]
                node["mountpoints"] = ["/run/media/jonh/card"]
                with self.assertRaisesRegex(ValueError, "mounted/in use"):
                    self.inspect([card])

    def test_refuses_partition_ambiguous_list_and_small_card(self):
        cases = [[CARD["children"][0]], [CARD, CARD["children"][0]],
                 [dict(CARD, size=1024)]]
        for devices in cases:
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                self.inspect(devices)

    @unittest.skipUnless(shutil.which("lsblk"), "Linux lsblk is required")
    def test_real_lsblk_output_keeps_partitions_nested(self):
        # Read-only integration with lsblk's actual JSON contract. No device
        # nodes are opened: only the Path metadata is substituted. Querying
        # all devices also works inside dev, which exposes sysfs but no disks.
        run = subprocess.check_output
        reference = json.loads(run(["lsblk", "--json", "--tree", "--output", "PATH,TYPE"], text=True))
        disk = next((d for d in reference["blockdevices"] if d["type"] == "disk" and d.get("children")), None)
        if disk is None:
            self.skipTest("No partitioned disk visible in sysfs")
        selected = {disk["path"], *(c["path"] for c in disk["children"])}
        def read_devices(command, **kwargs):
            result = json.loads(run(command[:-1], **kwargs))
            # Match what lsblk DEVICE returns, preserving its chosen shape.
            result["blockdevices"] = [d for d in result["blockdevices"] if d["path"] in selected]
            def clear_mounts(node):
                node["mountpoints"] = [None]  # this test concerns JSON shape
                for child in node.get("children", []):
                    clear_mounts(child)
            for node in result["blockdevices"]:
                clear_mounts(node)
            return json.dumps(result)
        target = Path(disk["path"])
        with patch.object(Path, "resolve", return_value=target), \
             patch.object(Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=123)), \
             patch.object(flash_image.subprocess, "check_output", side_effect=read_devices):
            _, _, observed = flash_image.inspect_device(target, 1)
        self.assertEqual(observed["path"], disk["path"])
        self.assertTrue(observed["children"])


if __name__ == "__main__":
    unittest.main()
