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


def _validate_reference_page(page, slug):
    if page.get('group') != 'Reference' or page.get('kind') != 'reference' or not page.get('sections'):
        raise ValueError('The ' + slug + ' must be a Reference page with sections')
    titles, anchors = [], {'docs-content', 'docs-title'}
    for section in page['sections']:
        title, anchor = section.get('title', ''), section.get('id', '')
        if (not isinstance(title, str) or not title.strip() or title != title.strip()
                or not isinstance(anchor, str) or not re.fullmatch('[a-z][a-z0-9-]*', anchor)
                or anchor in anchors or anchor + '-title' in anchors):
            raise ValueError(slug.capitalize() + ' sections need nonempty titles and unique safe anchors')
        titles.append(title.casefold())
        anchors.update((anchor, anchor + '-title'))
    if (slug == 'glossary' and titles != sorted(titles)) or len(titles) != len(set(titles)):
        raise ValueError(slug.capitalize() + ' sections must have unique titles; glossary terms must be alphabetical')
    if any(not isinstance(s.get('evidence'), list) or not s['evidence']
           or any(not isinstance(key, str) or not key for key in s['evidence']) for s in page['sections']):
        raise ValueError('Evidence is required for every ' + slug + ' section')


def _validate_resources(page, document, version, sources, index, evidence):
    """Allow literal pinned primary files and existing same-version guide anchors."""
    _validate_reference_page(page, 'resources')
    commit = document['version']['commit']
    prefix = f'https://github.com/pubky/paykit-rs/blob/{commit}/'
    if page.get('title') != 'Resources':
        raise ValueError('Resources page title must be Resources')
    required = {row['path'] for row in evidence}
    for section in [page, *page['sections']]:
        primary = False
        if section is not page and (not isinstance(section.get('paragraphs'), list) or not section['paragraphs']
                or any(not isinstance(text, str) or not text.strip() for text in section['paragraphs'])):
            raise ValueError('Resources sections require nonempty paragraphs')
        for link in section.get('sourceLinks' if section is page else 'links', []):
            if not isinstance(link.get('label'), str) or not link['label'].strip():
                raise ValueError('Resources links require nonempty labels')
            href = link.get('href', '')
            match = re.fullmatch(re.escape(prefix) + r'([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)(?:#L[1-9][0-9]*(?:-L[1-9][0-9]*)?)?', href)
            if match and not any(part in ('.', '..') for part in match[1].split('/')):
                required.add(match[1])
                primary = True
                continue
            internal = re.fullmatch(r'/docs/' + re.escape(version) + r'/([a-z][a-z0-9-]*)(?:#([a-z][a-z0-9-]*))?', href)
            if internal:
                target = next((p for p in document['pages'] if p['slug'] == internal[1]), None)
                if target and (not internal[2] or internal[2] in {'docs-content', 'docs-title',
                        *(s['id'] for s in target['sections']), *(s['id'] + '-title' for s in target['sections'])}):
                    continue
            raise ValueError('Resources link must use an exact approved snapshot file or an existing version-local guide')
        if section is not page and not primary:
            raise ValueError('Resources sections require a primary source link')
    files = {row['path']: row for row in index.get('files', [])}
    if index.get('publicOnly') is not True or len(files) != len(index.get('files', [])) or not required <= files.keys():
        raise ValueError('Resources sources must be unique files in the frozen public index')
    for path in required:
        row = files[path]
        try:
            source = (sources / path).resolve(strict=True)
            source.relative_to(sources.resolve())
            data = source.read_bytes()
        except (OSError, ValueError) as error:
            raise ValueError('Resources source is unavailable: ' + path) from error
        if (row.get('url') != f'https://raw.githubusercontent.com/pubky/paykit-rs/{commit}/{path}'
                or row.get('sha256') != hashlib.sha256(data).hexdigest()
                or type(row.get('bytes')) is not int or row['bytes'] != len(data)):
            raise ValueError('Resources source does not match its frozen public index: ' + path)


def _refresh_reference(root, version, sources, slug):
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
        raise ValueError(slug.capitalize() + ' inputs do not match the actual documented source')
    pages = [p for p in blueprint['pages'] if p['slug'] == slug]
    if len(pages) != 1:
        raise ValueError('The blueprint must contain exactly one ' + slug + ' page')
    page = copy.deepcopy(pages[0])
    _validate_reference_page(page, slug)
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
    if slug == 'resources':
        _validate_resources(page, document, version, sources, index, [available[key] for key in ids])
    indices = [i for i, p in enumerate(document['pages']) if p['slug'] == slug]
    if len(indices) > 1:
        raise ValueError('The existing document contains ambiguous ' + slug + ' pages')
    if indices:
        document['pages'][indices[0]] = page
    else:
        document['pages'].append(page)
    document['lastUpdated'] = datetime.now(timezone.utc).date().isoformat()
    document_bytes = encode(document)
    manifest = json.loads(manifest_path.read_bytes())
    manifest['contentHashes'][version] = hashlib.sha256(document_bytes).hexdigest()
    # Preserve the full-generation fingerprint; disclose the limited page scope.
    manifest['pageRefresh'] = {'version': version, 'page': slug, 'sourceCommit': blueprint['sourceCommit']}
    outputs = ((document_path, document_bytes), (evidence_path, encode(evidence)), (manifest_path, encode(manifest)))
    changed = []
    for path, data in outputs:
        if path.read_bytes() != data:
            path.write_bytes(data)
            changed.append(str(path.relative_to(root)))
    return {'page': f'{version}/{slug}', 'sourceCommit': blueprint['sourceCommit'], 'changedFiles': changed}


def refresh_glossary(root, version, sources):
    return _refresh_reference(root, version, sources, 'glossary')


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
