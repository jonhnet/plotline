"""Restart guard tests using isolated files. Never invokes systemd."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location("dev_server", Path(__file__).with_name("dev-server.py"))
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)

class RestartGuard(unittest.TestCase):
    def test_household_ipp_start_is_rejected_before_systemd_or_network_access(self):
        ipp_spec=importlib.util.spec_from_file_location('dev_ipp',dev.ROOT/'tools/print_sink/dev-server.py')
        ipp=importlib.util.module_from_spec(ipp_spec);ipp_spec.loader.exec_module(ipp)
        for action in ['start','restart']:
            with self.subTest(action=action), patch('sys.argv',['dev-server.py',action]), \
                    patch.object(ipp,'run') as run, patch.object(ipp.subprocess,'run') as process, \
                    patch('sys.stderr'):
                with self.assertRaises(SystemExit) as error:ipp.main()
                self.assertEqual(error.exception.code,2)
                run.assert_not_called();process.assert_not_called()

    def test_session_never_inherits_production_or_remembered_configuration(self):
        with patch.dict('os.environ',{'PLOTLINE_IPP_INBOX':'/real/captures',
                'PLOTLINE_DEVICE_CONFIG':'/private/stain.json','PLOTLINE_MAP_UPSTREAM':'http://production',
                'PLOTLINE_ADMISSION_LOCK':'/real/admission.lock'}):
            env=dev.session_environment(Path('/tmp/isolated'),12345,12346)
        self.assertNotIn('PLOTLINE_IPP_INBOX',env)
        self.assertNotIn('PLOTLINE_DEVICE_CONFIG',env)
        self.assertEqual(env['PLOTLINE_MAP_UPSTREAM'],'http://127.0.0.1:12346')
        self.assertEqual(env['PLOTLINE_ADMISSION_LOCK'],'/tmp/isolated/admission.lock')

    def test_teardown_waits_for_map_work_then_stops_both_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);children=[Mock(pid=101),Mock(pid=102)]
            for child in children:child.poll.return_value=None
            with patch.object(dev,'read_status',side_effect=[{'jobs':['render']},{'jobs':[]}]), \
                    patch.object(dev.time,'sleep') as sleep, patch.object(dev.os,'killpg') as kill:
                dev.stop_session(children,root,'http://127.0.0.1:12345')
            sleep.assert_called_once_with(1)
            self.assertEqual([call.args[0] for call in kill.call_args_list],[102,101])
            for child in children:child.wait.assert_called_once_with(timeout=20)

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
