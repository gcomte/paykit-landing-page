"""Cargo dependency resolution only; no builds or demo runs."""
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))


class DemoLockTests(unittest.TestCase):
    def test_installed_toolchain_freezes_selection_before_protected_version_checks(self):
        import demo_locks
        import stat
        store = Path('/root/.paykit-rust-stable')
        pointer = store / 'current.json'
        selected = store / '1.100.0' / 'bin'
        def metadata(path):
            mode = stat.S_IFREG | 0o644 if path == pointer else stat.S_IFDIR | 0o755
            return SimpleNamespace(st_mode=mode, st_uid=0, st_size=23)
        with patch.object(Path, 'lstat', metadata), \
                patch.object(Path, 'read_bytes', side_effect=[b'{"version":"1.100.0"}']) as read, \
                patch('demo_locks.validate_toolchain') as validate, \
                patch('demo_locks.subprocess.check_output', side_effect=[b'cargo 1.100.0 (test)\n', b'rustc 1.100.0 (test)\n']) as version:
            self.assertEqual(getattr(demo_locks, 'select_installed_toolchain', lambda: None)(), selected)
        read.assert_called_once_with()
        validate.assert_called_once_with(selected)
        self.assertEqual([c.args[0] for c in version.call_args_list],
                         [[str(selected / n), '--version'] for n in ('cargo', 'rustc')])
        for c in version.call_args_list:
            self.assertEqual(c.kwargs['cwd'], Path('/'))
            self.assertEqual(set(c.kwargs['env']), {'PATH', 'LANG', 'HOME', 'CARGO_HOME'})

    def test_installed_toolchain_refuses_bad_selection_before_launching_a_binary(self):
        import demo_locks
        import stat
        for pointer_mode, body in ((stat.S_IFLNK | 0o777, b'{"version":"1.100.0"}'),
                (stat.S_IFREG | 0o666, b'{"version":"1.100.0"}'),
                (stat.S_IFREG | 0o644, b'{"version":"01.100.0"}'),
                (stat.S_IFREG | 0o644, b'{"version":"1.100.0-rc.1"}'),
                (stat.S_IFREG | 0o644, b'{"version":"../bin"}')):
            with self.subTest(mode=pointer_mode, body=body), \
                    patch.object(Path, 'lstat', lambda path: SimpleNamespace(st_uid=0, st_size=len(body),
                        st_mode=pointer_mode if path.name == 'current.json' else stat.S_IFDIR | 0o755)), \
                    patch.object(Path, 'read_bytes', return_value=body), \
                    patch('demo_locks.validate_toolchain') as validate, \
                    patch('demo_locks.subprocess.check_output') as version:
                with self.assertRaisesRegex(ValueError, 'No verified protected Paykit'):
                    getattr(demo_locks, 'select_installed_toolchain', lambda: None)()
                validate.assert_not_called()
                version.assert_not_called()

    def test_user_writable_or_empty_toolchain_refused_before_execution(self):
        import tempfile
        import demo_locks
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / 'bin'
            bindir.mkdir()
            def no_execution(*args):
                self.fail('Untrusted toolchain reached the process runner')
            with self.assertRaisesRegex(ValueError, 'trusted.*toolchain'):
                demo_locks.generate_lock(b'[package]\n', bindir, 8, runner=no_execution)
            for name in ('cargo', 'rustc'):
                tool = bindir / name
                tool.write_bytes(b'inert test placeholder')
                tool.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'trusted.*toolchain'):
                demo_locks.generate_lock(b'[package]\n', bindir, 8, runner=no_execution)

    def test_incomplete_or_privileged_root_owned_toolchain_is_refused(self):
        import errno
        import stat
        import demo_locks
        bindir = Path('/trusted/rust/bin')
        library = bindir.parent / 'lib'
        driver = library / 'librustc_driver-fixture.so'
        directories = {bindir, *bindir.parents, library}
        trusted = {path: (stat.S_IFDIR | 0o755, 4096) for path in directories}
        trusted.update({bindir / name: (stat.S_IFREG | 0o755, 4096) for name in ('cargo', 'rustc')})
        trusted[driver] = (stat.S_IFREG | 0o644, 4096)
        cases = {
            'setuid Cargo': (bindir / 'cargo', stat.S_ISUID | stat.S_IFREG | 0o755, 4096),
            'setgid rustc': (bindir / 'rustc', stat.S_ISGID | stat.S_IFREG | 0o755, 4096),
            'empty Cargo executable': (bindir / 'cargo', stat.S_IFREG | 0o755, 0),
            'empty lib': (driver, None, 0),
            'empty driver': (driver, stat.S_IFREG | 0o644, 0),
            'capability marked executable': (bindir / 'cargo', stat.S_IFREG | 0o755, 4096),
        }
        for label, mutation in [('valid control', None), *cases.items()]:
            layout = dict(trusted)
            if mutation:
                path, mode, size = mutation
                if mode is None:
                    layout.pop(path)
                else:
                    layout[path] = (mode, size)
            def fake_stat(path):
                if path not in layout:
                    raise FileNotFoundError(path)
                mode, size = layout[path]
                return SimpleNamespace(st_uid=0, st_mode=mode, st_size=size)
            def capability(path, key, **_):
                self.assertEqual(key, 'security.capability')
                if label == 'capability marked executable' and path == bindir / 'cargo':
                    return b'synthetic file attribute'
                raise OSError(errno.ENODATA, 'attribute absent')
            with self.subTest(layout=label), patch.object(Path, 'lstat', fake_stat), \
                    patch.object(Path, 'resolve', lambda path, **_: path), \
                    patch('demo_locks.os.geteuid', return_value=1000), \
                    patch('demo_locks.os.access', return_value=True), \
                    patch('demo_locks.os.getxattr', side_effect=capability), \
                    patch('demo_locks.os.walk', return_value=[(library, [], [driver.name] if driver in layout else [])]):
                if mutation is None:
                    demo_locks.validate_toolchain(bindir)
                else:
                    with self.assertRaisesRegex(ValueError, 'trusted.*toolchain'):
                        demo_locks.validate_toolchain(bindir)

    def test_resolver_uses_an_empty_workspace_and_no_inherited_credentials(self):
        import demo_locks
        fake_toolchain = Path('/trusted/rust/bin')
        calls = []
        expected = b'version = 4\n'

        def observe(argv, cwd, env, timeout):
            calls.append(argv[1:3])
            self.assertEqual(cwd, Path('/'))
            self.assertEqual(argv[3], '--manifest-path')
            manifest = Path(argv[4])
            self.assertEqual(manifest.name, 'Cargo.toml')
            scratch = manifest.parent
            self.assertEqual(scratch.parent, Path('/tmp'))
            self.assertNotIn('GITHUB_TOKEN', env)
            self.assertNotIn('CARGO_REGISTRIES_CRATES_IO_TOKEN', env)
            self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')
            self.assertEqual(Path(env['HOME']), scratch)
            self.assertEqual(Path(env['CARGO_HOME']), scratch / 'cargo-home')
            self.assertFalse((scratch / 'src' / 'main.rs').read_bytes())
            self.assertFalse((scratch / 'target').exists())
            (scratch / 'Cargo.lock').write_bytes(expected)
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic', 'CARGO_REGISTRIES_CRATES_IO_TOKEN': 'synthetic'}), \
                patch('demo_locks.tempfile.tempdir', str(Path(__file__).parent)), \
                patch('demo_locks.validate_toolchain'):
            got = demo_locks.generate_lock(b'[package]\n', fake_toolchain, 8, runner=observe)
        self.assertEqual(got, expected)
        self.assertEqual(calls, [['generate-lockfile', '--quiet']])

    def test_lock_requires_exact_direct_package_source_and_checksum(self):
        import demo_locks
        import json
        rev = 'b' * 40
        reg = 'registry+https://github.com/rust-lang/crates.io-index'
        git = f'git+https://github.com/pubky/paykit-rs?rev={rev}#{rev}'
        def lock(source, checksum=None, transitive=reg):
            return (f'version = 4\n[[package]]\nname="demo"\nversion="0.0.0"\n'
                    f'[[package]]\nname="paykit-lib"\nversion="1.0.0"\nsource={json.dumps(source)}\n'
                    + (f'checksum="{checksum}"\n' if checksum else '')
                    + f'[[package]]\nname="serde"\nversion="1.0.0"\nsource={json.dumps(transitive)}\n').encode()
        packaged = {'version': '1.0.0', 'checksum': 'c' * 64}
        demo_locks.validate_lock(lock(reg, 'c' * 64), 'released', rev, packaged)
        demo_locks.validate_lock(lock(git), 'coming-soon', rev, packaged)
        for body, track in [(lock(reg, 'd' * 64), 'released'), (lock(git), 'released'),
                            (lock(reg, 'c' * 64), 'coming-soon'),
                            (lock(git.replace(rev, 'a' * 40)), 'coming-soon'),
                            (lock(git, transitive='git+https://unapproved.invalid/code'), 'coming-soon')]:
            with self.subTest(track=track, body=hashlib.sha256(body).hexdigest()), self.assertRaisesRegex(ValueError, 'lock'):
                demo_locks.validate_lock(body, track, rev, packaged)

    def test_nonzero_resolver_does_not_forward_raw_transport_stderr(self):
        import demo_locks
        def observe(*args, **kwargs):
            return SimpleNamespace(returncode=101, stdout='', stderr='synthetic sensitive input')
        with patch('demo_locks.validate_toolchain'), self.assertRaisesRegex(ValueError, r'^Cargo lock resolution failed \(exit 101\)$'):
            demo_locks.generate_lock(b'[package]\n', Path('/trusted/rust/bin'), 8, runner=observe)
