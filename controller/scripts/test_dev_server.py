"""Restart guard tests using isolated files. Never invokes systemd."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("dev_server", Path(__file__).with_name("dev-server.py"))
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)

class RestartGuard(unittest.TestCase):
    def test_pdf_work_blocks_restart_even_without_a_physical_plot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for kind, states in [('documents', ['received', 'inspecting']),
                                 ('conversions', ['queued', 'running'])]:
                path = root/kind/'example'/'record.json'
                path.parent.mkdir(parents=True)
                for status in states:
                    original = json.dumps(dict(status=status))
                    path.write_text(original)
                    with self.assertRaisesRegex(SystemExit, 'active PDF work'):
                        dev.require_idle(root)
                    self.assertEqual(path.read_text(), original)
                for status in ['ready', 'unsupported', 'error']:
                    path.write_text(json.dumps(dict(status=status)))
                    dev.require_idle(root)

    def test_active_feed_and_unconfirmed_completion_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dev.require_idle(root)
            attempt = "00000000-0000-0000-0000-000000000001"
            record = root / "plots" / attempt / "record.json"
            record.parent.mkdir(parents=True)
            (root / "plots/active.json").write_text(json.dumps(attempt))
            for status in ("connecting", "feeding", "paused", "finishing", "completion_unknown", "submitted", "cancelled", "uncertain", "interrupted"):
                original = json.dumps({"phase":"accepted", "execution":{"state":status}})
                record.write_text(original)
                with self.assertRaisesRegex(SystemExit, "left running"):
                    dev.require_idle(root)
                self.assertEqual(record.read_text(), original)
            for status in ("finished", "reset_acknowledged"):
                record.write_text(json.dumps({"phase":"accepted", "execution":{"state":status}}))
                dev.require_idle(root)
            record.write_text(json.dumps({"phase":"failed", "execution":None}))
            dev.require_idle(root)

if __name__ == "__main__":
    unittest.main()
