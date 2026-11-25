"""Resolve demo dependencies without compiling or running application code."""
import errno
import json
import os
from pathlib import Path
import re
import math
import signal
import stat
import subprocess
import tempfile
import tomllib

REGISTRY = 'registry+https://github.com/rust-lang/crates.io-index'
GIT = 'git+https://github.com/pubky/paykit-rs'


def run_resolver(argv, cwd, env, timeout):
    with subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as proc:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise ValueError('Cargo lock resolution exceeded its time budget') from None
        return proc


def validate_toolchain(cargo_bin):
    """Require a protected toolchain tree, including its relative runtime libraries."""
    def protected(path, kind, executable=False):
        info = path.lstat()
        if (not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or (stat.S_ISREG(info.st_mode) and info.st_mode & (stat.S_ISUID | stat.S_ISGID))
                or (executable and (not info.st_size or not os.access(path, os.X_OK)))):
            raise ValueError
        return info

    def scan_failed(error):
        raise error

    try:
        if (os.geteuid() == 0 or cargo_bin.name != 'bin'
                or cargo_bin.resolve(strict=True) != cargo_bin):
            raise ValueError
        for path in (cargo_bin, *cargo_bin.parents):
            protected(path, stat.S_ISDIR)
        for name in ('cargo', 'rustc'):
            tool = cargo_bin / name
            protected(tool, stat.S_ISREG, executable=True)
            try:
                os.getxattr(tool, 'security.capability', follow_symlinks=False)
            except OSError as error:
                if error.errno not in (errno.ENODATA, errno.ENOTSUP):
                    raise
            else:
                raise ValueError
        library = cargo_bin.parent / 'lib'
        protected(library, stat.S_ISDIR)
        have_driver = False
        for directory, dirs, files in os.walk(library, onerror=scan_failed):
            directory = Path(directory)
            for name in dirs:
                protected(directory / name, stat.S_ISDIR)
            for name in files:
                info = protected(directory / name, stat.S_ISREG)
                if directory == library and name.startswith('librustc_driver-') and name.endswith('.so'):
                    have_driver |= info.st_size > 0
        if not have_driver:
            raise ValueError
    except (OSError, ValueError, RuntimeError):
        raise ValueError('Cargo lock resolution requires a trusted root-owned toolchain and an unprivileged runner') from None


def select_installed_toolchain():
    """Read the approved updater's pointer once and verify the concrete pair."""
    store = Path('/root/.paykit-rust-stable')
    pointer = store / 'current.json'
    try:
        for directory in (*reversed(store.parents), store):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError
        info = pointer.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or not 1 <= info.st_size <= 4096):
            raise ValueError
        version = json.loads(pointer.read_bytes())['version']
        if not isinstance(version, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}', version):
            raise ValueError
        selected = store / version / 'bin'
        validate_toolchain(selected)
        with tempfile.TemporaryDirectory(prefix='paykit-toolchain-', dir='/tmp') as home:
            for name in ('cargo', 'rustc'):
                output = subprocess.check_output([str(selected / name), '--version'], cwd=Path('/'),
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': home, 'CARGO_HOME': home},
                    timeout=10, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if not output.startswith(f'{name} {version} '.encode('ascii')):
                    raise ValueError
        return selected
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise ValueError('No verified protected Paykit Cargo/rustc pair is selected') from None


def generate_lock(manifest, cargo_bin, timeout, runner=run_resolver):
    if not cargo_bin.is_absolute() or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Cargo lock resolution requires a trusted absolute toolchain bin and time budget')
    validate_toolchain(cargo_bin)
    # Cargo searches for config from its working directory, not --manifest-path.
    # Stay above the shared scratch directory and never load repository config.
    resolver_cwd = Path('/')
    if any(os.path.lexists(resolver_cwd / '.cargo' / name) for name in ('config', 'config.toml')):
        raise ValueError('Cargo lock runner has an ambient Cargo configuration')
    with tempfile.TemporaryDirectory(prefix='paykit-demo-lock-', dir='/tmp') as tmp:
        cwd = Path(tmp)
        (cwd / 'Cargo.toml').write_bytes(manifest)
        (cwd / 'src').mkdir()
        (cwd / 'src/main.rs').touch()
        env = {
            'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': tmp,
            'CARGO_HOME': str(cwd / 'cargo-home'), 'RUSTC': str(cargo_bin / 'rustc'),
            'CARGO_NET_RETRY': '1', 'CARGO_HTTP_TIMEOUT': '30',
            'CARGO_NET_GIT_FETCH_WITH_CLI': 'true', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_ALLOW_PROTOCOL': 'https',
        }
        try:
            result = runner([str(cargo_bin / 'cargo'), 'generate-lockfile', '--quiet',
                             '--manifest-path', str(cwd / 'Cargo.toml')], resolver_cwd, env, timeout)
        except OSError:
            raise ValueError('Could not start the trusted Cargo toolchain') from None
        if result.returncode != 0:
            raise ValueError(f'Cargo lock resolution failed (exit {result.returncode})')
        if (cwd / 'target').exists() or not (cwd / 'Cargo.lock').is_file():
            raise ValueError('Cargo did not produce a metadata-only lockfile')
        return (cwd / 'Cargo.lock').read_bytes()


def validate_lock(body, track, commit, package):
    try:
        data = tomllib.loads(body.decode())
        rows = data['package']
        lib = [row for row in rows if row['name'] == 'paykit-lib']
        if data['version'] != 4 or len(lib) != 1 or lib[0]['version'] != package['version']:
            raise ValueError
        pinned = f'{GIT}?rev={commit}#{commit}'
        if track == 'released':
            if (lib[0]['source'] != REGISTRY or lib[0]['checksum'] != package['checksum']
                    or not re.fullmatch('[0-9a-f]{64}', package['checksum'])):
                raise ValueError
        elif track != 'coming-soon' or lib[0]['source'] != pinned:
            raise ValueError
        allowed = (None, REGISTRY, pinned) if track == 'coming-soon' else (None, REGISTRY)
        if any(row.get('source') not in allowed for row in rows):
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('Cargo lock does not match the selected package and allowed sources') from None
