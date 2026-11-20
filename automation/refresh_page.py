#!/usr/bin/env python3
"""Regenerate one overview page from a source-grounded author blueprint."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def refresh_page(root, version, slug):
    root = Path(root)
    blueprint_path = root / f'automation/blueprints/{version}.json'
    document_path = root / f'content/docs/{version}.json'
    evidence_path = root / f'automation/evidence/{version}.json'
    manifest_path = root / 'public/docs-manifest.json'
    blueprint = json.loads(blueprint_path.read_text())
    document = json.loads(document_path.read_text())
    if blueprint['sourceCommit'] != document['version']['commit']:
        raise ValueError('The page blueprint and published document use different source commits')
    page = copy.deepcopy(next(p for p in blueprint['pages'] if p['slug'] == slug))
    index = next(i for i, p in enumerate(document['pages']) if p['slug'] == slug)
    ids = {key for section in page['sections'] for key in section['evidence']}
    available = {row['id']: row for row in blueprint['evidence']}
    evidence = json.loads(evidence_path.read_text())
    existing = {row['id']: row for row in evidence}
    for key in sorted(ids):
        row = available[key]
        if row['version'] != version or row['commit'] != blueprint['sourceCommit']:
            raise ValueError('Evidence does not match the selected source')
        if key in existing and existing[key] != row:
            raise ValueError('Use a new evidence ID rather than changing a shared mapping')
        if key not in existing:
            evidence.append(row)
    document['pages'][index] = page
    document['lastUpdated'] = datetime.now(timezone.utc).date().isoformat()
    encode = lambda value: (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()
    document_bytes = encode(document)
    manifest = json.loads(manifest_path.read_text())
    manifest['contentHashes'][version] = hashlib.sha256(document_bytes).hexdigest()
    # Keep the previous whole-generation fingerprint; disclose this limited refresh.
    manifest['pageRefresh'] = {'version': version, 'page': slug,
                               'sourceCommit': blueprint['sourceCommit']}
    changed = []
    for path, data in ((document_path, document_bytes), (evidence_path, encode(evidence)),
                       (manifest_path, encode(manifest))):
        if path.read_bytes() != data:
            path.write_bytes(data)
            changed.append(str(path.relative_to(root)))
    return {'page': f'{version}/{slug}', 'sourceCommit': blueprint['sourceCommit'],
            'changedFiles': changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=['released', 'coming-soon'], required=True)
    parser.add_argument('--page', choices=['overview'], required=True,
                        help='This first increment supports the overview page only')
    args = parser.parse_args()
    print(json.dumps(refresh_page(Path(__file__).resolve().parents[1], args.version, args.page)))


if __name__ == '__main__':
    main()
