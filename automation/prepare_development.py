#!/usr/bin/env python3
"""Freeze public authoring inputs for one documentation version; never publish."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import quote
from release_source import (API, SHA, configured_sources, read_public, read_ref, release_identity,
                            select_source, validate_frozen)
from verify_stable_package import MAX_CRATE_BYTES, verify_published_package

RAW = 'https://raw.githubusercontent.com/pubky/paykit-rs'
PACKAGES = ('paykit-lib', 'paykit-sdk', 'paykit-ffi')
MAX_BYTES = 2_000_000


def empty_output(output):
    if (not output.is_absolute() or '..' in output.parts or any(p.is_symlink() for p in (output, *output.parents))
            or (output.exists() and (not output.is_dir() or any(output.iterdir())))):
        raise ValueError('Output must be an empty task-owned absolute directory without symlinks')


def require_source_path(path):
    parts = path.split('/')
    if (len(path) > 512 or not re.fullmatch(r'[A-Za-z0-9_./-]+', path)
            or any(part in ('', '.', '..', '.git') for part in parts)
            or parts[0] in ('previous', 'registry')
            or not (path.endswith(('.rs', '.md')) or parts[-1] == 'Cargo.toml')):
        raise ValueError('Unsafe source path: ' + path)


def prepare_development(root, output, commit, selection=None, *, version='coming-soon'):
    root, output = Path(root), Path(output)
    empty_output(output)
    read = lambda path: json.loads((root / path).read_bytes())
    policies = configured_sources(read('docs-generation.json'))
    if version not in policies:
        raise ValueError('Unsupported documentation version or missing source policy')
    policy = policies[version]
    stable = version == 'released'
    if stable and selection is None:
        raise ValueError('Released preparation requires a frozen selection')
    document = (root / f'content/docs/{version}.json').read_bytes()
    previous = json.loads(document)['version']['commit']
    blueprint = read(f'automation/blueprints/{version}.json')
    if any(not isinstance(sha, str) or not SHA.fullmatch(sha) for sha in (commit, previous)):
        raise ValueError('Invalid commit: expected literal 40 lowercase hexadecimal characters')
    if selection is None:
        frozen = select_source(policy, lambda *args: commit)
    else:
        gate = json.loads(Path(selection).read_bytes())
        tracks = gate.get('selections', {version: gate}) if isinstance(gate, dict) else None
        frozen = tracks.get(version) if isinstance(tracks, dict) else None
        if not isinstance(frozen, dict) or frozen.get('version', version) != version:
            raise ValueError('Frozen selection belongs to a different documentation version')
        if (frozen.get('documentationCommit', previous) != previous
                or ('documentationContentHash' in frozen and
                    frozen['documentationContentHash'] != hashlib.sha256(document).hexdigest())):
            raise ValueError('Documentation baseline changed since the frozen selection')
    validate_frozen(policy, frozen, commit, require_object=stable or (selection is not None and 'released' in policies))
    source = frozen['sourceSelection']
    kind, ref = source['kind'], source['ref']
    artifacts = {}

    def fetch(url, name, missing=False, limit=MAX_BYTES):
        status, data, _ = read_public(url, limit=limit, missing=missing)
        if name.endswith(('.rs', '.md', 'Cargo.toml')) and '\x00' in data.decode('utf-8'):
            raise ValueError('Source file is binary, not text: ' + name)
        artifacts[name] = data
        return status, data

    def record(url, name):
        return json.loads(fetch(url, name)[1])

    if 'selection' in policy:
        release = frozen['release']
        receipt = record(API + '/releases/' + str(release['id']), 'release.json')
        if receipt['draft'] is not False or release_identity(receipt) != release:
            raise ValueError('Public release no longer matches the frozen selection')
    identity = read_ref(source['repository'], kind, ref, record, expected_object=frozen.get('sourceObject'))
    if identity['sourceCommit'] != commit:
        raise ValueError('Source ref moved or did not resolve to the expected commit')
    qualified, object_sha = identity['qualifiedRef'], identity['sourceObject']

    compare = record(API + f'/compare/{previous}...{commit}', 'compare.json')
    if compare['base_commit']['sha'] != previous or len(compare['files']) >= 300:
        raise ValueError('Wrong compare base or potentially truncated compare (300-file API limit)')
    retired = {row['filename'] for row in compare['files'] if row['status'] == 'removed'}
    retired.update(row['previous_filename'] for row in compare['files'] if row['status'] == 'renamed')
    # Only the compare receipt can remove a previous evidence path; a surprise 404 still fails.
    paths = {row['path'] for row in blueprint['evidence']} - retired
    paths.update(('README.md', 'CHANGELOG.md', 'skills/paykit/SKILL.md'))
    paths.update(package + '/Cargo.toml' for package in PACKAGES)
    paths.update(row['filename'] for row in compare['files'] if row['status'] != 'removed'
                 and (row['filename'].endswith(('.rs', '.md')) or Path(row['filename']).name == 'Cargo.toml'))
    for path in paths:
        require_source_path(path)
    files = {}

    def source_at(path):
        require_source_path(path)
        if path not in files:
            url = f'{RAW}/{commit}/' + quote(path, safe='/')
            data = fetch(url, path)[1]
            files[path] = {'path': path, 'url': url, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        return artifacts[path]

    for path in sorted(paths):
        source_at(path)
    old_path = 'previous/paykit-lib/Cargo.toml'
    old_url = f'{RAW}/{previous}/paykit-lib/Cargo.toml'
    old_data = fetch(old_url, old_path)[1]
    metadata = {'label': ref, 'ref': ref, kind: ref, 'kind': kind, 'qualifiedRef': qualified,
                'object': object_sha, 'commit': commit,
                'sourceUrl': f'https://github.com/pubky/paykit-rs/tree/{commit}', 'packages': {}}
    if 'selection' in policy:
        metadata['release'] = frozen['release']
    if stable:
        metadata['releaseUrl'] = frozen['release']['html_url']
    observations = []
    for package in PACKAGES:
        manifest = tomllib.loads(artifacts[package + '/Cargo.toml'].decode())
        info = manifest['package']
        if (info['name'] != package or not all(isinstance(info[key], str) and info[key] for key in ('version', 'rust-version'))
                or not re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,127}', info['version'])):
            raise ValueError('Unsupported package manifest identity or inherited version')
        version_component = quote(info['version'], safe='')
        url = f'https://crates.io/api/v1/crates/{package}/{version_component}'
        body_path = f'registry/{package}.body'
        status, data = fetch(url, body_path, missing=True)
        observation = {'url': url, 'status': status, 'bodyPath': body_path}
        observations.append(observation)
        details = {'version': info['version'], 'rustVersion': info['rust-version'],
                   'dependencies': manifest.get('dependencies', {}), 'published': status == 200}
        metadata['packages'][package] = details
        if status == 200:
            registry = json.loads(data)['version']
            if (registry['crate'] != package or registry['num'] != info['version']
                    or type(registry['yanked']) is not bool or not re.fullmatch('[0-9a-f]{64}', registry['checksum'])):
                raise ValueError('Registry returned a different or malformed package')
            details.update(yanked=registry['yanked'], checksum=registry['checksum'])
            if stable and details['yanked']:
                details['published'] = False  # An existing registry record is not an available stable package.
            else:
                details['referenceUrl'] = f'https://docs.rs/{package}/{version_component}/{package.replace("-", "_")}/'
        if stable and package == 'paykit-lib' and not details['published']:
            raise ValueError('Required Released paykit-lib package is unavailable or yanked')
        if stable and details['published']:
            download_url = f'https://static.crates.io/crates/{package}/{package}-{version_component}.crate'
            archive_path = f'registry/{package}.crate'
            data = fetch(download_url, archive_path, limit=MAX_CRATE_BYTES)[1]
            details.update(downloadUrl=download_url,
                           verifiedSource=verify_published_package(package, details, commit, data, source_at))
            observation.update(archivePath=archive_path, archiveUrl=download_url)
    index = {**source, 'publicOnly': True, 'commit': commit, 'object': object_sha, 'previousCommit': previous,
             'files': [files[path] for path in sorted(files)], 'version': metadata,
             'previousLibraryManifest': {'commit': previous, 'path': old_path, 'url': old_url,
                                         'sha256': hashlib.sha256(old_data).hexdigest()}}
    if 'selection' in policy:
        index['sourcePolicy'] = policy
    artifacts['index.json'] = (json.dumps(index, indent=2) + '\n').encode()
    artifacts['registry-observations.json'] = (json.dumps(observations, indent=2) + '\n').encode()
    empty_output(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    return {'action': 'prepared', 'sources': str(output), 'commit': commit, 'files': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Empty task-owned absolute source directory')
    parser.add_argument('--version', choices=('released', 'coming-soon'), default='coming-soon')
    parser.add_argument('--commit', required=True, help='Expected exact 40-character commit from the weekly check')
    parser.add_argument('--selection', type=Path, help='Gate JSON: reuse its frozen release, never rediscover during preparation')
    args = parser.parse_args()
    try:
        result = prepare_development(Path(__file__).resolve().parents[1], args.output, args.commit, args.selection,
                                     version=args.version)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': 'source_preparation_failed', 'detail': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
