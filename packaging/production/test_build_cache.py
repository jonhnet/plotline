"""Revision-independent caches and immutable release output; no real user data."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import build as builder

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ipp_builder', ROOT/'tools/print_sink/build_server.py')
ipp_builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipp_builder)


class CacheTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT/'build/production-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = dict(install_root=str(self.root/'install'), data_root=str(self.root/'data'), build_jobs=16)
        self.calls = []
        self.versions = 'libc6=1\ngcc=1\n'

    def source(self, revision):
        root = self.root/('source-'+revision)
        root.mkdir()
        (root/'SOURCE_REVISION').write_text(revision)
        (root/'main.rs').write_text(revision)
        return root

    def fake_run(self, *args, **kwargs):
        if args[0] == 'dpkg-query':
            return SimpleNamespace(stdout=self.versions)
        self.calls.append((args, kwargs))
        command = args[4:]
        root = kwargs['cwd']
        env = kwargs['env']
        target = Path(env['CARGO_TARGET_DIR'])
        if command[0] == 'cargo':
            (target/'release').mkdir(parents=True, exist_ok=True)
            for binary in ('plotline', 'plotline-studios'):
                (target/'release'/binary).write_text(root.name)
            (target/'dependency-sentinel').write_text('keep compiled dependencies')
        if command[-1] == 'tools/build_cartographer_geometry.py':
            (root/'build/native').mkdir(parents=True)
            Path(env['PLOTLINE_GEOMETRY_TARGET_DIR']).joinpath('dependency-sentinel').write_text('keep geometry')
        if command[-1] == 'tools/print_sink/build_server.py':
            sink = Path(env['PLOTLINE_IPP_BUILD_DIR'])
            if not (sink/'ippeveprinter').exists():
                (sink/'ippeveprinter').write_text('cached IPP binary')
                (sink/'build.json').write_text('{}')
        return SimpleNamespace(returncode=0)

    def build(self, source, revision):
        owner = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        with patch.object(builder, 'run', side_effect=self.fake_run), \
                patch.object(builder.pwd, 'getpwnam', return_value=owner), \
                patch.object(builder.os, 'chown'), contextlib.redirect_stdout(io.StringIO()):
            return builder.build(source, revision, self.c, {})

    def test_next_revision_reuses_caches_but_cannot_change_installed_executables(self):
        first = self.build(self.source('a'*40), 'a'*40)
        original = (first/'target/release/plotline').read_bytes()
        cache = builder.cache_paths(self.c, self.versions, {})
        marker = cache['cargo']/'dependency-sentinel'
        inode = marker.stat().st_ino
        (Path(self.c['install_root'])/'current').symlink_to(first)
        second = self.build(self.source('b'*40), 'b'*40)
        self.assertEqual(marker.stat().st_ino, inode)
        self.assertEqual((first/'target/release/plotline').read_bytes(), original)
        self.assertNotEqual((second/'target/release/plotline').read_bytes(), original)
        self.assertTrue((cache['geometry']/'dependency-sentinel').exists())
        for _, kwargs in self.calls:
            self.assertEqual(kwargs['env']['CARGO_TARGET_DIR'], str(cache['cargo']))
            self.assertEqual(kwargs['env']['PLOTLINE_IPP_BUILD_DIR'], str(cache['ipp']))
        receipt = json.loads((second/'installed-build.json').read_text())
        self.assertEqual(receipt['build_jobs'], 16)
        self.assertTrue(all(step['seconds'] >= 0 for step in receipt['timings']))
        self.assertTrue(any(step['command'][0] == 'cargo' for step in receipt['timings']))
        self.assertTrue(any(step['command'][-1] == 'tools/print_sink/build_server.py' for step in receipt['timings']))
        self.assertEqual(receipt['build_cache'], cache['key'])
        before = len(self.calls)
        self.build(self.root/('source-'+'b'*40), 'b'*40)
        self.assertEqual(len(self.calls), before, 'The same completed revision must bypass builds')

    def test_os_or_flags_isolate_cache_but_parallelism_does_not(self):
        a = builder.cache_paths(self.c, self.versions, {})
        for versions, env in (('gcc=2\n', {}), (self.versions, {'RUSTFLAGS':'-C opt-level=2'})):
            self.assertNotEqual(builder.cache_paths(self.c, versions, env)['cargo'], a['cargo'])
        self.assertEqual(builder.cache_paths(self.c|{'build_jobs':4}, self.versions, {})['cargo'], a['cargo'])
        self.assertNotIn(self.c['data_root'], str(a['cargo']))

    def test_ipp_cache_validates_inputs_and_binary_bytes(self):
        binary, receipt = self.root/'ippeveprinter', self.root/'build.json'
        identity = dict(cups='fixture', patch_sha256='one', environment='one')
        binary.write_bytes(b'binary')
        receipt.write_text(json.dumps(identity|{'binary_sha256':hashlib.sha256(b'binary').hexdigest()}))
        self.assertTrue(ipp_builder.reusable(binary, receipt, identity))
        self.assertFalse(ipp_builder.reusable(binary, receipt, identity|{'patch_sha256':'two'}))
        self.assertFalse(ipp_builder.reusable(binary, receipt, identity|{'environment':'two'}))
        binary.write_bytes(b'corrupt')
        self.assertFalse(ipp_builder.reusable(binary, receipt, identity))
        receipt.write_text('{interrupted')
        self.assertFalse(ipp_builder.reusable(binary, receipt, identity))

    def test_real_cargo_reuses_unchanged_dependency_across_source_directories(self):
        cargo = shutil.which('cargo')
        if not cargo:
            self.skipTest('Local Cargo required for real cache reuse check')
        probe = subprocess.run([cargo, '--version'], capture_output=True, text=True)
        if probe.returncode:
            self.skipTest('No installed local Rust toolchain')
        shared = self.root/'shared'
        (shared/'src').mkdir(parents=True)
        (shared/'Cargo.toml').write_text('[package]\nname="stable_helper"\nversion="0.1.0"\nedition="2021"\n')
        (shared/'src/lib.rs').write_text('pub fn value() -> u8 { 1 }\n')
        target = builder.cache_paths(self.c, '', {})['cargo']
        fresh = []
        for name in ('one', 'two'):
            root = self.root/name
            (root/'src').mkdir(parents=True)
            (root/'Cargo.toml').write_text('[package]\nname="example"\nversion="0.1.0"\nedition="2021"\n'
                                          '[dependencies]\nstable_helper={path="../shared"}\n')
            (root/'src/main.rs').write_text(f'fn main() {{ println!("{name}:{{}}", stable_helper::value()); }}\n')
            result = subprocess.run([cargo, 'build', '--release', '--offline', '--message-format=json'],
                                    cwd=root, env=dict(os.environ, CARGO_TARGET_DIR=str(target)),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            artifacts = [json.loads(line) for line in result.stdout.splitlines()]
            fresh.append(next(item['fresh'] for item in artifacts if item.get('reason') == 'compiler-artifact'
                              and item.get('target',{}).get('name') == 'stable_helper'))
        self.assertEqual(fresh, [False, True])


if __name__=='__main__':
    unittest.main()
