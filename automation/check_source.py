#!/usr/bin/env python3
"""Read-only upstream check before authoring an overview refresh."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
from release_source import select_source, source_matches


def resolve_source(repository, kind, ref):
    if repository != 'pubky/paykit-rs' or kind not in ('branch', 'tag'):
        raise ValueError('Unsupported public source repository or ref kind')
    namespace = 'heads' if kind == 'branch' else 'tags'
    qualified = f'refs/{namespace}/{ref}'
    check = subprocess.run(['git', 'check-ref-format', qualified], capture_output=True)
    if check.returncode:
        raise ValueError('Invalid source ref')
    # The commits endpoint resolves annotated tags and requires a commit target.
    url = ('https://api.github.com/repos/pubky/paykit-rs/commits/'
           + urllib.parse.quote(qualified, safe=''))
    request = urllib.request.Request(url, headers={
        'User-Agent': 'paykit-docs-source-check', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        record = json.load(response)
    commit = record['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit) or 'tree' not in record.get('commit', {}):
        raise ValueError('The source ref did not resolve to a commit')
    return commit


def check_source(root, version, page, force=False, resolver=resolve_source):
    root = Path(root)
    config = json.loads((root / 'docs-generation.json').read_text())
    document = json.loads((root / f'content/docs/{version}.json').read_text())
    if not any(item['slug'] == page for item in document['pages']):
        raise ValueError('The requested page does not exist')
    if version == 'released':
        source = {'repository': config['source']['repository'], 'kind': 'tag', 'ref': document['version']['tag']}
    else:
        source = config['source']
    frozen = select_source(source, resolver)
    selection = frozen['sourceSelection']
    kind, ref, commit = selection['kind'], selection['ref'], frozen['sourceCommit']
    unchanged = source_matches(document['version'], frozen)
    return {'action': 'skip' if unchanged and not force else 'refresh_needed',
            'reason': 'forced' if force else ('source_unchanged' if unchanged else 'source_changed'),
            'version': version, 'page': page, 'refKind': kind, 'ref': ref,
            'documentationCommit': document['version']['commit'], **frozen}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=['released', 'coming-soon'], required=True)
    parser.add_argument('--page', choices=['overview'], required=True)
    parser.add_argument('--force', action='store_true', help='Request a refresh even for unchanged source')
    args = parser.parse_args()
    try:
        result = check_source(Path(__file__).resolve().parents[1], args.version, args.page, args.force)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': 'source_check_failed', 'detail': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
