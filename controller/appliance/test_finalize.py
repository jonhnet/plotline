import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("finalize_boot", Path(__file__).with_name("finalize_boot.py"))
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "etc/netplan/90-NM-wifi.yaml"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("private original configuration\n")
        self.profile = self.root / "run/NetworkManager/system-connections/netplan-wifi.nmconnection"
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("[connection]\nid=wifi\nuuid=62a41874-aa05-30e1-90cd-c40f15b43ef1\ntype=wifi\n[wifi-security]\npsk=private % value\n")

    def native(self):
        return list((self.root / "etc/NetworkManager/system-connections").glob("*.nmconnection"))

    def test_round_trip_keeps_exact_credentials_and_protects_backup(self):
        original = self.source.read_bytes()
        boot.apply(self.root)
        boot.apply(self.root)  # idempotent; backup remains the pre-migration state
        self.assertFalse(self.source.exists())
        self.assertEqual(len(self.native()), 1)
        self.assertEqual(self.native()[0].read_bytes(), self.profile.read_bytes())
        self.assertEqual(self.native()[0].stat().st_mode & 0o777, 0o600)
        journal = self.root / boot.JOURNAL
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        self.assertEqual(journal.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(journal.read_text())["state"], "applied")
        boot.rollback(self.root)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(self.native())
        self.assertFalse((self.root / "etc/cloud/cloud-init.disabled").exists())

    def test_missing_profile_leaves_network_and_cloud_init_alone(self):
        self.profile.unlink()
        with self.assertRaises(ValueError):
            boot.apply(self.root)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / boot.JOURNAL).exists())

    def test_rollback_refuses_later_operator_edits_before_touching_anything(self):
        boot.apply(self.root)
        self.native()[0].write_text("later operator change")
        with self.assertRaises(ValueError):
            boot.rollback(self.root)
        self.assertFalse(self.source.exists())
        self.assertTrue((self.root / "etc/cloud/cloud-init.disabled").exists())

    def test_write_failure_restores_configuration(self):
        replace = boot.replace
        def fail_once(path, data, mode=0o600):
            if path.name == "cloud-init.disabled":
                raise OSError("injected write failure")
            replace(path, data, mode)
        with patch.object(boot, "replace", fail_once), self.assertRaises(OSError):
            boot.apply(self.root)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.native())
        self.assertFalse((self.root / boot.JOURNAL).exists())


if __name__ == "__main__":
    unittest.main()
