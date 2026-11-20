#!/usr/bin/env python3
"""Render only glossary from a blueprint pinned to the documented source."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def refresh_glossary(root, version, sources):
    root, sources = Path(root), Path(sources)
    if version not in ('released', 'coming-soon') or not sources.is_absolute():
        raise ValueError('Choose an existing documentation version and absolute frozen sources directory')
    document_path = root / f'content/docs/{version}.json'
    evidence_path = root / f'automation/evidence/{version}.json'
    manifest_path = root / 'public/docs-manifest.json'
    blueprint = json.loads((root / f'automation/blueprints/{version}.json').read_bytes())
    document = json.loads(document_path.read_bytes())
    index = json.loads((sources / 'index.json').read_bytes())
    if (index.get('repository') != 'pubky/paykit-rs'
            or index.get('commit') != document['version']['commit']
            or blueprint['sourceCommit'] != document['version']['commit']):
        raise ValueError('Glossary inputs do not match the actual documented source')
    pages = [p for p in blueprint['pages'] if p['slug'] == 'glossary']
    if len(pages) != 1:
        raise ValueError('The blueprint must contain exactly one glossary page')
    page = copy.deepcopy(pages[0])
    if page.get('group') != 'Reference' or page.get('kind') != 'reference' or not page.get('sections'):
        raise ValueError('The glossary must be a Reference page with term sections')
    titles, anchors = [], {'docs-content', 'docs-title'}
    for section in page['sections']:
        title, anchor = section.get('title', ''), section.get('id', '')
        if (not isinstance(title, str) or not title.strip() or title != title.strip()
                or not isinstance(anchor, str) or not re.fullmatch('[a-z][a-z0-9-]*', anchor)
                or anchor in anchors or anchor + '-title' in anchors):
            raise ValueError('Glossary terms need nonempty titles and unique safe anchors')
        titles.append(title.casefold())
        anchors.update((anchor, anchor + '-title'))
    if titles != sorted(titles) or len(titles) != len(set(titles)):
        raise ValueError('Glossary terms must have unique alphabetical titles')
    if any(not isinstance(s.get('evidence'), list) or not s['evidence']
           or any(not isinstance(key, str) or not key for key in s['evidence']) for s in page['sections']):
        raise ValueError('Evidence is required for every glossary term')
    ids = {key for section in page['sections'] for key in section['evidence']}
    available = {row['id']: row for row in blueprint['evidence']}
    evidence = json.loads(evidence_path.read_bytes())
    existing = {row['id']: row for row in evidence}
    if (len(available) != len(blueprint['evidence']) or len(existing) != len(evidence)
            or not ids <= available.keys()):
        raise ValueError('Evidence IDs must be unique and all term mappings available')
    for key in sorted(ids):
        row = available[key]
        path = Path(row['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Evidence source path is invalid: ' + key)
        try:
            source = (sources / path).resolve(strict=True)
            source.relative_to(sources.resolve())
            data = source.read_bytes()
            lines = data.decode('utf-8').splitlines()
        except (OSError, ValueError) as error:
            raise ValueError('Evidence source is unavailable or invalid: ' + key) from error
        start, end = row['startLine'], row['endLine']
        if (row['version'] != version or row['commit'] != blueprint['sourceCommit']
                or hashlib.sha256(data).hexdigest() != row['sourceHash']
                or type(start) is not int or type(end) is not int
                or not 1 <= start <= end <= len(lines)
                or '\n'.join(lines[start - 1:end]) != row['excerpt']):
            raise ValueError('Evidence does not match the frozen source: ' + key)
        if key in existing and existing[key] != available[key]:
            raise ValueError('Use a new evidence ID rather than changing a shared mapping')
        if key not in existing:
            evidence.append(available[key])
    indices = [i for i, p in enumerate(document['pages']) if p['slug'] == 'glossary']
    if len(indices) > 1:
        raise ValueError('The existing document contains ambiguous glossary pages')
    if indices:
        document['pages'][indices[0]] = page
    else:
        document['pages'].append(page)
    document['lastUpdated'] = datetime.now(timezone.utc).date().isoformat()
    document_bytes = encode(document)
    manifest = json.loads(manifest_path.read_bytes())
    manifest['contentHashes'][version] = hashlib.sha256(document_bytes).hexdigest()
    # Preserve the full-generation fingerprint; disclose the limited page scope.
    manifest['pageRefresh'] = {'version': version, 'page': 'glossary', 'sourceCommit': blueprint['sourceCommit']}
    outputs = ((document_path, document_bytes), (evidence_path, encode(evidence)), (manifest_path, encode(manifest)))
    changed = []
    for path, data in outputs:
        if path.read_bytes() != data:
            path.write_bytes(data)
            changed.append(str(path.relative_to(root)))
    return {'page': f'{version}/glossary', 'sourceCommit': blueprint['sourceCommit'], 'changedFiles': changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=('released', 'coming-soon'), required=True)
    parser.add_argument('--sources', type=Path, required=True,
                        help='Absolute frozen public source directory with index.json and evidence files')
    args = parser.parse_args()
    if not args.sources.is_absolute():
        parser.error('--sources must be an absolute frozen directory')
    print(json.dumps(refresh_glossary(Path(__file__).resolve().parents[1], args.version, args.sources)))


if __name__ == '__main__':
    main()
