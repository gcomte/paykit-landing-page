#!/usr/bin/env python3
"""Recreate development dependencies inside the existing sandbox only."""
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUSTUP_SHA256 = "20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c"

def main():
    subprocess.run(["npm", "ci"], cwd=ROOT, check=True, timeout=300)
    subprocess.run(["npx", "--no-install", "playwright", "install", "chromium"], cwd=ROOT, check=True, timeout=300)
    rustup = Path.home() / ".cargo/bin/rustup"
    if not rustup.exists():
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise SystemExit("Bootstrap currently supports Linux x86_64; use Rust 1.91.1 on other targets")
        url = "https://static.rust-lang.org/rustup/archive/1.28.2/x86_64-unknown-linux-gnu/rustup-init"
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != RUSTUP_SHA256:
            raise SystemExit("rustup archive checksum mismatch")
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "rustup-init"
            installer.write_bytes(data)
            installer.chmod(0o700)
            subprocess.run([str(installer), "-y", "--profile", "minimal", "--default-toolchain", "1.91.1", "--no-modify-path"], check=True, timeout=300)
    subprocess.run([str(rustup), "toolchain", "install", "1.91.1", "--profile", "minimal", "--no-self-update"], check=True, timeout=300)
    subprocess.run([str(Path.home() / ".cargo/bin/rustc"), "+1.91.1", "--version"], check=True)

if __name__ == "__main__":
    main()
