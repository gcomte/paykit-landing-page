#!/usr/bin/env python3
"""Read-only upstream check before authoring an overview refresh."""
import argparse
import json
from pathlib import Path
import subprocess
from release_source import configured_sources, read_public, read_ref, select_source, source_matches, source_metadata_issues


def resolve_source(repository, kind, ref):
    return resolve_source_identity(repository, kind, ref)['sourceCommit']


def resolve_source_identity(repository, kind, ref):
    return read_ref(repository, kind, ref, lambda url, _: json.loads(read_public(url)[1]))


def check_source(root, version, page, force=False, resolver=resolve_source_identity, *, fetcher=None):
    root = Path(root)
    if version not in ('released', 'coming-soon'):
        raise ValueError('Unsupported documentation version')
    config = json.loads((root / 'docs-generation.json').read_text())
    document = json.loads((root / f'content/docs/{version}.json').read_text())
    if not any(item['slug'] == page for item in document['pages']):
        raise ValueError('The requested page does not exist')
    sources = configured_sources(config)
    source = sources.get(version)
    if source is None:
        source = {'repository': sources['coming-soon']['repository'], 'kind': 'tag', 'ref': document['version']['tag']}
    frozen = select_source(source, resolver, fetcher, require_object='released' in sources)
    selection = frozen['sourceSelection']
    kind, ref, commit = selection['kind'], selection['ref'], frozen['sourceCommit']
    unchanged = source_matches(document['version'], frozen)
    issues = source_metadata_issues(document['version'], frozen) if unchanged else []
    reason = ('forced' if force else 'source_changed' if not unchanged else
              'source_metadata_invalid' if issues else 'source_unchanged')
    return {'action': 'skip' if reason == 'source_unchanged' else 'refresh_needed', 'reason': reason,
            'version': version, 'page': page, 'refKind': kind, 'ref': ref,
            'documentationCommit': document['version']['commit'], **frozen,
            **({'sourceMetadataIssues': issues} if issues else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=['released', 'coming-soon'], required=True)
    parser.add_argument('--page', choices=['overview'], required=True)
    parser.add_argument('--force', action='store_true', help='Request a refresh even for unchanged source')
    args = parser.parse_args()
    try:
        result = check_source(Path(__file__).resolve().parents[1], args.version, args.page, args.force)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': 'source_check_failed', 'detail': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
