#!/usr/bin/env python3
"""Refresh the complete development snapshot from pinned public author inputs."""
import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tomllib


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def refresh_development(root, sources):
    root, sources = Path(root), Path(sources)
    read = lambda path: json.loads(path.read_bytes())
    index = read(sources / 'index.json')
    metadata = copy.deepcopy(index['version'])
    blueprint = read(root / 'automation/blueprints/coming-soon.json')
    previous = read(root / 'content/docs/coming-soon.json')
    config = read(root / 'docs-generation.json')
    if (not all(index.get(k) == v for k, v in config['source'].items())
            or blueprint['sourceCommit'] != index['commit'] or metadata['commit'] != index['commit']
            or metadata['kind'] != index['kind'] or metadata['ref'] != index['ref']):
        raise ValueError('Development inputs do not match the selected source')
    source_field = 'tag' if index['kind'] == 'tag' else 'branch'
    expected_identity = {source_field: index['ref'], 'label': index['ref'],
        'qualifiedRef': 'refs/' + ('tags/' if index['kind'] == 'tag' else 'heads/') + index['ref'],
        'sourceUrl': f'https://github.com/{index["repository"]}/tree/{index["commit"]}'}
    if (any(metadata.get(k) != v for k, v in expected_identity.items())
            or ('branch' if source_field == 'tag' else 'tag') in metadata):
        raise ValueError('Displayed source identity contradicts the frozen selection')
    if sorted(p['slug'] for p in blueprint['pages']) != sorted(p['slug'] for p in previous['pages']):
        raise ValueError('Refresh every existing development page together')
    evidence_ids = set()
    for row in blueprint['evidence']:
        path = Path(row['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Evidence source path is invalid')
        data = (sources / path).read_bytes()
        lines = data.decode().splitlines()
        start, end = row['startLine'], row['endLine']
        if (row['id'] in evidence_ids or row['commit'] != index['commit'] or row['version'] != 'coming-soon'
                or hashlib.sha256(data).hexdigest() != row['sourceHash'] or not 1 <= start <= end <= len(lines)
                or '\n'.join(lines[start - 1:end]) != row['excerpt']):
            raise ValueError('Evidence does not match the frozen source: ' + row['id'])
        evidence_ids.add(row['id'])
    for page in blueprint['pages']:
        if any(not s.get('evidence') or not set(s['evidence']) <= evidence_ids for s in page['sections']):
            raise ValueError('Evidence is missing for a development section')
    # Reuse the transitive graph only for an otherwise unchanged library manifest.
    old_library = tomllib.loads((sources / 'previous/paykit-lib/Cargo.toml').read_text())
    new_library = tomllib.loads((sources / 'paykit-lib/Cargo.toml').read_text())
    old_library['package']['version'] = new_library['package']['version']
    if old_library != new_library:
        raise ValueError('A new dependency lock resolution is required for the changed library manifest')
    if (metadata['packages']['paykit-lib']['dependencies'] != previous['version']['packages']['paykit-lib']['dependencies']
            or metadata['packages']['paykit-lib']['rustVersion'] != previous['version']['packages']['paykit-lib']['rustVersion']):
        raise ValueError('A new dependency lock resolution is required')
    outputs = {}
    for audience in ('wallet', 'point-of-sale'):
        prefix = f'public/docs-examples/coming-soon/{audience}'
        lock_text = (root / prefix / 'Cargo.lock').read_text()
        parts = lock_text.split('[[package]]')
        for i, part in enumerate(parts[1:], 1):
            package = tomllib.loads('[[package]]' + part)['package'][0]
            if package['name'] == 'paykit-lib':
                new_source = f'git+https://github.com/pubky/paykit-rs?rev={metadata["commit"]}#{metadata["commit"]}'
                parts[i] = part.replace(package['source'], new_source).replace(
                    f'version = "{package["version"]}"',
                    f'version = "{metadata["packages"]["paykit-lib"]["version"]}"', 1)
        rust = metadata['packages']['paykit-lib']['rustVersion']
        files = {
            'Cargo.toml': ('[package]\nname = "paykit-' + audience + '-demo"\nversion = "0.0.0"\nedition = "2021"\nrust-version = "' + rust + '"\n\n'
                '[workspace]\nresolver = "3"\n\n[dependencies]\npaykit-lib = { git = "https://github.com/pubky/paykit-rs", rev = "' + metadata['commit'] + '" }\n').encode(),
            'Cargo.lock': '[[package]]'.join(parts).encode(),
            'rust-toolchain.toml': f'[toolchain]\nchannel = "{rust}"\nprofile = "minimal"\n'.encode(),
            'src/main.rs': (root / f'automation/recipes/{audience}.rs').read_bytes()}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for name, data in sorted(files.items()):
                entry = tarfile.TarInfo(f'paykit-{audience}-demo/{name}')
                entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
                tar.addfile(entry, io.BytesIO(data))
                outputs[f'{prefix}/{name}'] = data
        outputs[f'public/docs-examples/coming-soon/paykit-{audience}-coming-soon.tar.gz'] = gzip.compress(archive.getvalue(), mtime=0)
    warning = next(((i, line) for i, line in enumerate((sources / 'README.md').read_text().splitlines(), 1)
                    if 'not for production' in line.lower()), None)
    metadata['readiness'] = {'status': 'unassessed', 'attribution': 'documentation-policy',
        'notice': 'A stable release label does not establish production readiness. Review upstream status before deployment.',
        'sourceUrl': metadata['sourceUrl'].replace('/tree/', '/blob/') + '/README.md'}
    if warning:
        metadata['readiness'].update(status='experimental', attribution='upstream',
            notice='Upstream README: ' + warning[1],
            sourceUrl=metadata['readiness']['sourceUrl'] + f'#L{warning[0]}')
    document = {'version': metadata, 'pages': copy.deepcopy(blueprint['pages']),
                'lastUpdated': datetime.now(timezone.utc).date().isoformat()}
    for page in document['pages']:
        for section in page['sections']:
            for block in section.get('code', []):
                if 'artifact' in block:
                    name = block.pop('artifact')
                    block['code'] = outputs['public/docs-examples/coming-soon/' + name].decode()
    outputs['content/docs/coming-soon.json'] = encode(document)
    outputs['automation/evidence/coming-soon.json'] = encode(blueprint['evidence'])
    lock = read(root / 'automation/sources.lock.json')
    lock['coming-soon'] = {k: v for k, v in metadata.items() if k != 'readiness'}
    lock['repository'], lock['defaultVersion'] = index['repository'], config['defaultVersion']
    lock['selectedSource'] = {**config['source'], **{k: metadata[k] for k in ('commit', 'object', 'qualifiedRef')}}
    outputs['automation/sources.lock.json'] = encode(lock)
    manifest = read(root / 'public/docs-manifest.json')
    manifest['sources']['coming-soon'] = {k: metadata[k] for k in ('commit', source_field, 'ref', 'kind')}
    manifest['contentHashes']['coming-soon'] = hashlib.sha256(outputs['content/docs/coming-soon.json']).hexdigest()
    manifest['versionRefresh'] = {'version': 'coming-soon', 'sourceCommit': metadata['commit'],
                                  'pages': [p['slug'] for p in document['pages']]}
    outputs['public/docs-manifest.json'] = encode(manifest)
    changed = []
    for name, data in outputs.items():
        path = root / name
        if not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed.append(name)
    return {'version': 'coming-soon', 'sourceCommit': metadata['commit'], 'changedFiles': changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True, help='Frozen public snapshot directory with index.json and version metadata')
    args = parser.parse_args()
    print(json.dumps(refresh_development(Path(__file__).resolve().parents[1], args.sources)))


if __name__ == '__main__':
    main()
