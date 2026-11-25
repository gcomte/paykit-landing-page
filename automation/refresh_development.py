#!/usr/bin/env python3
"""Refresh one complete documentation snapshot from pinned public author inputs."""
import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tarfile
import time
import tomllib

import demo_locks
from release_source import validate_frozen
from refresh_glossary import _validate_resources

AUDIENCES = ('wallet', 'point-of-sale')
LIB = 'paykit-lib/Cargo.toml'
PREVIOUS_LIB = 'previous/' + LIB


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def lock_plan(sources, index, previous, metadata, version, cargo_bin, deadline):
    same_source = metadata['commit'] == previous['version']['commit']
    old_data, new_data = ((sources / path).read_bytes() for path in (PREVIOUS_LIB, LIB))
    if cargo_bin is not None or version == 'released':
        receipt = index.get('previousLibraryManifest', {})
        if (receipt.get('path') != PREVIOUS_LIB or receipt.get('commit') != index.get('previousCommit')
                or receipt.get('sha256') != digest(old_data)
                or (not same_source and receipt.get('commit') != previous['version']['commit'])
                or not any(row['path'] == LIB and row['sha256'] == digest(new_data) for row in index.get('files', []))):
            raise ValueError('Cargo lock inputs do not match the frozen source receipts')
    old_library, new_library = (tomllib.loads(body.decode()) for body in (old_data, new_data))
    pkg = metadata['packages']['paykit-lib']
    old_pkg = previous['version']['packages']['paykit-lib']
    if ((pkg['version'], pkg['rustVersion'], pkg['dependencies']) !=
        (new_library['package']['version'], new_library['package']['rust-version'], new_library.get('dependencies', {}))):
        raise ValueError('Cargo lock package metadata differs from the frozen library manifest')
    if same_source:
        lock_fields = ('version', 'dependencies', 'rustVersion')
        if version == 'released':
            lock_fields += ('checksum',)
        if any(pkg.get(k) != old_pkg.get(k) for k in lock_fields):
            raise ValueError('Cargo lock metadata changed without a new source commit')
        return 'reuse'
    old_library['package']['version'] = new_library['package']['version']
    if (version == 'coming-soon' and cargo_bin is None and not index.get('object') and old_library == new_library
            and all(pkg[k] == old_pkg[k] for k in ('dependencies', 'rustVersion'))):
        return 'legacy_repin'  # Older manual CLI, only after confirming the manifest.
    if cargo_bin is None or deadline is None:
        raise ValueError('A new Cargo lock resolution needs a trusted toolchain and the original run deadline')
    # A new git revision can change workspace/transitive inputs even with a byte-identical direct manifest.
    return 'resolve'


def repin_legacy(locked, commit, version):
    parts = locked.decode().split('[[package]]')
    for i, part in enumerate(parts[1:], 1):
        package = tomllib.loads('[[package]]' + part)['package'][0]
        if package['name'] == 'paykit-lib':
            src = f'{demo_locks.GIT}?rev={commit}#{commit}'
            parts[i] = part.replace(package['source'], src).replace(
                f'version = "{package["version"]}"', f'version = "{version}"', 1)
    return '[[package]]'.join(parts).encode()


def example_outputs(root, version, metadata, plan, cargo_bin, deadline, bound):
    pkg = metadata['packages']['paykit-lib']
    commit, rust = metadata['commit'], pkg['rustVersion']
    if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', rust):
        raise ValueError('Cargo lock rust-version must be a numeric Rust version')
    if version == 'released':
        dependency = f'{{ version = "={pkg["version"]}" }}'
    else:
        dependency = f'{{ git = "https://github.com/pubky/paykit-rs", rev = "{commit}" }}'
    outputs, resolved = {}, []
    for audience in AUDIENCES:
        base = f'public/docs-examples/{version}'
        prefix = f'{base}/{audience}'
        manifest = (f'[package]\nname = "paykit-{audience}-demo"\nversion = "0.0.0"\nedition = "2021"\n'
                    f'rust-version = "{rust}"\n\n[workspace]\nresolver = "3"\n\n[dependencies]\npaykit-lib = {dependency}\n').encode()
        original_manifest = root / prefix / 'Cargo.toml'
        if plan == 'resolve' or (bound and (not original_manifest.is_file() or original_manifest.read_bytes() != manifest)):
            if cargo_bin is None or deadline is None:
                raise ValueError('A changed demo manifest needs Cargo lock resolution and the original deadline')
            locked = demo_locks.generate_lock(manifest, cargo_bin, min(300, deadline - time.time()))
            resolved.append(audience)
        else:
            locked = (root / prefix / 'Cargo.lock').read_bytes()
            if plan == 'legacy_repin':
                locked = repin_legacy(locked, commit, pkg['version'])
        demo_locks.validate_lock(locked, version, commit, pkg)
        files = {'Cargo.toml': manifest, 'Cargo.lock': locked,
                 'rust-toolchain.toml': f'[toolchain]\nchannel = "{rust}"\nprofile = "minimal"\n'.encode(),
                 'src/main.rs': (root / f'automation/recipes/{audience}.rs').read_bytes()}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for name, data in sorted(files.items()):
                entry = tarfile.TarInfo(f'paykit-{audience}-demo/{name}')
                entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
                tar.addfile(entry, io.BytesIO(data))
                outputs[f'{prefix}/{name}'] = data
        outputs[f'{base}/paykit-{audience}-{version}.tar.gz'] = gzip.compress(archive.getvalue(), mtime=0)
    return outputs, resolved


def refresh_development(root, sources, *, version='coming-soon', cargo_bin=None, deadline=None):
    if version not in ('coming-soon', 'released'):
        raise ValueError('Invalid documentation version')
    if deadline is not None and (not math.isfinite(deadline) or deadline <= time.time()):
        raise ValueError('Documentation generation time budget expired')
    root, sources = Path(root), Path(sources)
    read = lambda path: json.loads(path.read_bytes())
    index = read(sources / 'index.json')
    metadata = copy.deepcopy(index['version'])
    blueprint = read(root / f'automation/blueprints/{version}.json')
    document_path = f'content/docs/{version}.json'
    previous_bytes = (root / document_path).read_bytes()
    previous = json.loads(previous_bytes)
    config = read(root / 'docs-generation.json')
    source = config['releasedSource' if version == 'released' else 'source']
    bound = 'releasedSource' in config
    frozen = {'sourceSelection': {k: index[k] for k in ('repository', 'kind', 'ref')},
              'sourcePolicy': index.get('sourcePolicy'), 'sourceCommit': index['commit'],
              'release': metadata.get('release')}
    if bound or 'object' in index:
        frozen.update(sourceObject=index.get('object'), qualifiedRef=metadata.get('qualifiedRef'))
    validate_frozen(source, frozen, index['commit'], require_object=bound)
    if (blueprint['sourceCommit'] != index['commit'] or metadata['commit'] != index['commit']
            or not re.fullmatch('[0-9a-f]{40}', index['commit'])
            or metadata['kind'] != index['kind'] or metadata['ref'] != index['ref']
            or (bound and metadata['object'] != index['object'])):
        raise ValueError('Documentation inputs do not match the selected source')
    source_field = 'tag' if index['kind'] == 'tag' else 'branch'
    expected_identity = {source_field: index['ref'], 'label': index['ref'],
        'qualifiedRef': 'refs/' + ('tags/' if index['kind'] == 'tag' else 'heads/') + index['ref'],
        'sourceUrl': f'https://github.com/{index["repository"]}/tree/{index["commit"]}'}
    if (any(metadata.get(k) != v for k, v in expected_identity.items())
            or ('branch' if source_field == 'tag' else 'tag') in metadata):
        raise ValueError('Displayed source identity contradicts the frozen selection')
    if sorted(p['slug'] for p in blueprint['pages']) != sorted(p['slug'] for p in previous['pages']):
        raise ValueError('Refresh every existing page in the selected snapshot together')
    pkg = metadata['packages']['paykit-lib']
    if version == 'released' and (metadata.get('releaseUrl') != metadata['release']['html_url']
            or pkg.get('published') is not True or pkg.get('yanked') is not False
            or pkg.get('verifiedSource', {}).get('sourceCommit') != metadata['commit']
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:\+[a-zA-Z0-9.-]+)?', pkg['version'])):
        raise ValueError('Released demo requires a published package verified against the selected stable source')
    evidence_ids = set()
    for row in blueprint['evidence']:
        path = Path(row['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Evidence source path is invalid')
        data = (sources / path).read_bytes()
        lines = data.decode().splitlines()
        start, end = row['startLine'], row['endLine']
        if (row['id'] in evidence_ids or row['commit'] != index['commit'] or row['version'] != version
                or digest(data) != row['sourceHash'] or not 1 <= start <= end <= len(lines)
                or '\n'.join(lines[start - 1:end]) != row['excerpt']):
            raise ValueError('Evidence does not match the frozen source: ' + row['id'])
        evidence_ids.add(row['id'])
    for page in blueprint['pages']:
        if any(not s.get('evidence') or not set(s['evidence']) <= evidence_ids for s in page['sections']):
            raise ValueError('Evidence is missing for a documentation section')
    plan = lock_plan(sources, index, previous, metadata, version, cargo_bin, deadline)
    outputs, resolved = example_outputs(root, version, metadata, plan, cargo_bin, deadline, bound)
    warning = next(((i, line) for i, line in enumerate((sources / 'README.md').read_text().splitlines(), 1)
                    if 'not for production' in line.lower()), None)
    metadata['readiness'] = {'status': 'unassessed', 'attribution': 'documentation-policy',
        'notice': 'A stable release label does not establish production readiness. Review upstream status before deployment.',
        'sourceUrl': metadata['sourceUrl'].replace('/tree/', '/blob/') + '/README.md'}
    if warning:
        metadata['readiness'].update(status='experimental', attribution='upstream',
            notice='Upstream README: ' + warning[1], sourceUrl=metadata['readiness']['sourceUrl'] + f'#L{warning[0]}')
    document = {'version': metadata, 'pages': copy.deepcopy(blueprint['pages']),
                'lastUpdated': datetime.now(timezone.utc).date().isoformat()}
    for page in document['pages']:
        if page['slug'] == 'resources':
            ids = {key for section in page['sections'] for key in section['evidence']}
            _validate_resources(page, document, version, sources, index,
                                [row for row in blueprint['evidence'] if row['id'] in ids])
        for section in page['sections']:
            for block in section.get('code', []):
                if 'artifact' in block:
                    name = block.pop('artifact')
                    block['code'] = outputs[f'public/docs-examples/{version}/' + name].decode()
    outputs[document_path] = encode(document)
    outputs[f'automation/evidence/{version}.json'] = encode(blueprint['evidence'])
    lock = read(root / 'automation/sources.lock.json')
    lock[version] = {k: v for k, v in metadata.items() if k != 'readiness'}
    lock['repository'], lock['defaultVersion'] = index['repository'], config['defaultVersion']
    if version == 'coming-soon':
        lock['selectedSource'] = {'repository': index['repository'], **{k: metadata[k] for k in ('kind', 'ref', 'commit', 'object', 'qualifiedRef')}}
        if 'selection' in source:
            lock['selectedSource']['sourcePolicy'] = source
    outputs['automation/sources.lock.json'] = encode(lock)
    manifest = read(root / 'public/docs-manifest.json')
    manifest['sources'][version] = {k: metadata[k] for k in ('commit', source_field, 'ref', 'kind')}
    manifest['contentHashes'][version] = digest(outputs[document_path])
    if previous_bytes != outputs[document_path] or 'versionRefresh' not in manifest:
        manifest['versionRefresh'] = {'version': version, 'sourceCommit': metadata['commit'],
                                      'pages': [p['slug'] for p in document['pages']]}
    outputs['public/docs-manifest.json'] = encode(manifest)
    if deadline is not None and deadline <= time.time():
        raise ValueError('Documentation generation time budget expired before writing')
    changed = []
    for name, data in outputs.items():
        path = root / name
        if not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed.append(name)
    return {'version': version, 'sourceCommit': metadata['commit'], 'changedFiles': changed, 'resolvedLocks': resolved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True, help='Frozen public snapshot directory with index.json and version metadata')
    parser.add_argument('--version', choices=('coming-soon', 'released'), default='coming-soon')
    parser.add_argument('--cargo-bin', type=Path, help='Trusted installed toolchain bin directory, not a Rustup shim or repository path')
    parser.add_argument('--deadline', type=float, help='Absolute deadlineEpoch from the original weekly gate')
    args = parser.parse_args()
    print(json.dumps(refresh_development(Path(__file__).resolve().parents[1], args.sources,
        version=args.version, cargo_bin=args.cargo_bin, deadline=args.deadline)))


if __name__ == '__main__':
    main()
