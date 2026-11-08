"""Build checks must catch omissions even when the allowlist agrees with them."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ('controller/scripts/retention.py', 'controller/retention-policy.json',
           'controller/scripts/render-page.py')
CHECK = 'packaging/container-check-source.py'


class ContainerContextTests(unittest.TestCase):
    def run_check(self, omit=None, extra=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {'.dockerignore', CHECK, *HELPERS} - {omit}
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
            (root / CHECK).write_bytes((ROOT / CHECK).read_bytes())
            (root / '.dockerignore').write_text('**\n' + ''.join('!' + p + '\n' for p in sorted(files)))
            if extra:
                path = root / extra
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test fixture')
            return subprocess.run([sys.executable, root / CHECK], capture_output=True, text=True)

    def test_complete_runtime_contract_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_matching_omission_from_source_and_allowlist_fails(self):
        for name in HELPERS:
            with self.subTest(name=name):
                result = self.run_check(omit=name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Missing required runtime files', result.stderr)
                self.assertIn(name, result.stderr)

    def test_private_state_still_fails(self):
        result = self.run_check(extra='controller/state/private/test-connection.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpected=', result.stderr)


if __name__ == '__main__':
    unittest.main()
