"""Progress must be cheap without losing the last operation or durable documents."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cartographer.progress import Progress
from cartographer.storage import atomic_json


class RuntimeTests(unittest.TestCase):
    def test_progress_is_atomic_without_fsync_but_saved_state_stays_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            with patch('cartographer.storage.os.fsync', side_effect=AssertionError('Progress forced disk flush')):
                with Progress(path) as progress:
                    progress('drawing', 'Starting')
                    progress('drawing', 'Latest count', 99, 100, 'lines')
                self.assertEqual(json.loads(path.read_text())['completed'], 99)
            with patch('cartographer.storage.os.fsync') as sync:
                atomic_json(Path(directory) / 'saved-document.json', {'revision': 1})
                self.assertEqual(sync.call_count, 2, 'Saved state syncs both file and directory')
            self.assertFalse(progress.publisher.is_alive())

    def test_inner_operations_coalesce_and_trailing_update_survives_a_long_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            published = threading.Event(); values = []
            def capture(path, value, **kwargs):
                values.append(dict(value))
                atomic_json(path, value, **kwargs)
                if value['detail'] == 'Long geometry call':
                    published.set()
            with patch('cartographer.progress.atomic_json', side_effect=capture), Progress(path, interval=.1) as progress:
                progress('drawing', 'Starting')
                for i in range(1000):
                    # Changing descriptions and tiny operation completions must
                    # not bypass the reporting cadence.
                    progress('drawing', f'Hatch {i}', 1, 1, 'rows')
                progress('drawing', 'Long geometry call')
                # No more callbacks: simulate the worker entering native GEOS.
                self.assertTrue(published.wait(2), 'Trailing operation never reached the UI')
                self.assertLess(len(values), 10)
                self.assertEqual(json.loads(path.read_text())['detail'], 'Long geometry call')
                progress('pdf', 'Writing PDF')
                self.assertEqual(json.loads(path.read_text())['stage'], 'pdf')
                progress('complete', 'Done')
                self.assertEqual(json.loads(path.read_text())['stage'], 'complete')
            self.assertFalse(progress.publisher.is_alive())

    def test_progress_closes_on_failure_and_preserves_original_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            with self.assertRaisesRegex(ValueError, 'geometry failed'):
                with Progress(path) as progress:
                    progress('drawing', 'Starting')
                    progress('drawing', 'Failed operation')
                    raise ValueError('geometry failed')
            self.assertFalse(progress.publisher.is_alive())
            self.assertEqual(json.loads(path.read_text())['detail'], 'Failed operation')

    def test_pdf_engine_import_does_not_load_ml_runtime(self):
        script = """import json,sys,pymupdf
print(json.dumps([name for name in sys.modules if name.split('.')[0] in ('onnxruntime','pymupdf4llm') or name=='pymupdf.layout']))
"""
        loaded = json.loads(subprocess.check_output([sys.executable, '-c', script], text=True))
        self.assertEqual(loaded, [], 'Use the isolated PyMuPDF Lite environment')

    def test_background_write_failure_is_reported_and_thread_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            attempted = threading.Event()
            def fail_latest(path, value, **kwargs):
                if value['detail'] == 'Latest':
                    attempted.set()
                    raise OSError('progress disk failure')
                atomic_json(path, value, **kwargs)
            with patch('cartographer.progress.atomic_json', side_effect=fail_latest):
                progress = Progress(Path(directory) / 'progress.json', interval=.1)
                try:
                    progress('drawing', 'Starting')
                    progress('drawing', 'Latest')
                    self.assertTrue(attempted.wait(2))
                    with self.assertRaisesRegex(OSError, 'progress disk failure'):
                        progress('drawing', 'More')
                finally:
                    with self.assertRaisesRegex(OSError, 'progress disk failure'):
                        progress.close()
            self.assertFalse(progress.publisher.is_alive())


if __name__ == '__main__':
    unittest.main()
