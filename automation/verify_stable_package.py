"""Verify a public crate without extracting files or running the package."""
import gzip
import hashlib
import io
import json
import tarfile
import zlib

MAX_CRATE_BYTES = 8_000_000
MAX_EXPANDED_BYTES = 32_000_000


def verify_published_package(package, info, commit, data, source_at):
    if hashlib.sha256(data).hexdigest() != info['checksum']:
        raise ValueError('Published package checksum mismatch: ' + package)
    prefix = f'{package}-{info["version"]}/'
    vcs_name = prefix + '.cargo_vcs_info.json'
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
        if len(expanded) > MAX_EXPANDED_BYTES:
            raise ValueError('Expanded public crate is too large: ' + package)
        with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
            rust, vcs = {}, None
            for member in archive:
                name = member.name
                if name == vcs_name:
                    if vcs is not None or not member.isfile() or not 0 <= member.size <= 16_000:
                        raise ValueError('Invalid package VCS receipt: ' + package)
                    vcs = json.load(archive.extractfile(member))
                    if not isinstance(vcs, dict) or not isinstance(vcs.get('git'), dict):
                        raise ValueError('Invalid package VCS receipt: ' + package)
                elif name.startswith(prefix + 'src/') and name.endswith('.rs'):
                    if name in rust or not member.isfile() or not 0 <= member.size <= 2_000_000 or len(rust) >= 2000:
                        raise ValueError('Invalid packaged Rust file: ' + name)
                    rust[name] = member
            if vcs is None:
                raise ValueError('Missing package VCS receipt: ' + package)
            if vcs['git'].get('sha1') != commit or vcs.get('path_in_vcs', package) != package:
                raise ValueError('Published package came from a different commit or package path: ' + package)
            if not rust:
                raise ValueError('Published package contains no Rust implementation: ' + package)
            for name, member in sorted(rust.items()):
                path = f'{package}/{name[len(prefix):]}'
                if archive.extractfile(member).read() != source_at(path):
                    raise ValueError('Published implementation differs from release source: ' + path)
    except (EOFError, gzip.BadGzipFile, tarfile.TarError, zlib.error) as error:
        raise ValueError('Invalid public crate archive: ' + package) from error
    return {'sourceCommit': commit, 'verifiedRustFiles': len(rust)}