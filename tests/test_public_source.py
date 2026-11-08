"""Publication boundary checks; no external repository or live service access."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('public_source', Path(__file__).resolve().parents[1]/'tools/package_public_source.py')
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'source'
        self.root.mkdir()
        self.output = Path(self.tmp.name)/'export'
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.config = {'include': ['README.md', 'app.py'], 'exclude': [],
                       'forbidden_prefixes': ['state/', 'archealogy/'], 'open_decisions': []}
        self.samples = {'drawing': {'image': 'sheet.jpg', 'files': [['Recipe', 'recipe.json']]}}
        self.put('packaging/public-source.json', json.dumps(self.config))
        self.put('docs/readme/samples.json', json.dumps(self.samples))
        self.put('docs/readme/examples/drawing/sheet.jpg', b'fixture image')
        self.put('docs/readme/examples/drawing/recipe.json', '{}')
        self.put('README.md', '[Recipe](docs/readme/examples/drawing/recipe.json)')
        self.put('app.py', 'print("committed")\n')
        (self.root/'app.py').chmod(0o755)
        self.put('state/private.json', 'private state')
        self.put('archealogy/old.pdf', 'private archive')
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

    def put(self, name, data):
        p = self.root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture')

    def export(self):
        with patch.object(package, 'ROOT', self.root):
            package.export('HEAD', self.output)

    def test_committed_bytes_only_with_examples_and_no_private_history(self):
        self.put('app.py', 'UNCOMMITTED\n')
        self.put('new-secret.txt', 'untracked')
        self.export()
        self.assertEqual((self.output/'app.py').read_text(), 'print("committed")\n')
        self.assertTrue((self.output/'app.py').stat().st_mode & 0o111)
        self.assertTrue((self.output/'docs/readme/examples/drawing/recipe.json').is_file())
        for name in ('.git', 'state', 'archealogy', 'new-secret.txt'):
            self.assertFalse((self.output/name).exists())
        record = json.loads((self.output/'SOURCE_MANIFEST.json').read_text())
        self.assertEqual(set(record['files']), {'README.md', 'app.py',
            'docs/readme/examples/drawing/recipe.json', 'docs/readme/examples/drawing/sheet.jpg'})
        with self.assertRaisesRegex(ValueError, 'refusing to replace'):
            self.export()

    def test_adding_private_prefix_to_allowlist_fails(self):
        self.config['include'].append('state/')
        self.put('packaging/public-source.json', json.dumps(self.config)); self.commit()
        with self.assertRaisesRegex(ValueError, 'Private path'):
            self.export()
        self.assertFalse(self.output.exists())

    def test_missing_download_or_readme_target_fails(self):
        self.put('README.md', '<a href="state/private.json">secret</a>'); self.commit()
        with self.assertRaisesRegex(ValueError, 'README link missing'):
            self.export()

    def test_symlink_cannot_escape_snapshot(self):
        (self.root/'app.py').unlink()
        (self.root/'app.py').symlink_to('/etc/passwd'); self.commit()
        with self.assertRaisesRegex(ValueError, 'Unexpected archive member'):
            self.export()
        self.assertFalse(self.output.exists())

    def test_uncommitted_example_and_parent_traversal_fail(self):
        paths = {'docs/readme/examples/drawing/sheet.jpg'}
        with self.assertRaisesRegex(ValueError, 'not committed'):
            package.selection(paths, self.config, self.samples)
        self.samples['drawing']['files'] = [['Leak', '../../private.json']]
        with self.assertRaisesRegex(ValueError, 'Unsafe example path'):
            package.selection(paths, self.config, self.samples)


if __name__ == '__main__':
    unittest.main()
