#!/usr/bin/env python3
"""Read-only weekly decision against the documentation actually deployed."""
import json
import argparse
import urllib.request
import hashlib
import os
import subprocess
import time
import math
from pathlib import Path
from check_source import resolve_source
from release_source import select_source, source_matches


SITE_URLS = ['https://paykit-landing-page-lake.vercel.app',
             'https://paykit-landing-page-git-docs-automation-g-a0aa.vercel.app']

PROCESS_INPUTS = [
    'docs-generation.json', 'automation/COORDINATOR.md', 'automation/WEEKLY.md',
    'automation/README.md', 'automation/corrections.json', 'automation/SOURCE-BOUNDARY.md',
    'automation/refresh_page.py', 'automation/refresh_development.py', 'automation/check_source.py',
    'automation/weekly_check.py', 'automation/prepare_development.py', 'automation/recipes',
    'automation/release_source.py', 'automation/refresh_glossary.py', 'automation/refresh_resources.py',
    'automation/blueprints/coming-soon.json', 'automation/skills',
    'components/docs', 'app/docs', 'app/layout.tsx', 'app/globals.css', 'app/llms.txt', 'package.json', 'package-lock.json',
    'tsconfig.json', 'next.config.ts', 'postcss.config.mjs', 'playwright.config.ts',
    'tests/browser/landing-docs.spec.ts', 'public/docs-examples/coming-soon',
    'tests/docs-*.test.mjs', 'tests/browser/docs*.spec.ts', 'tests/resources-policy.test.mjs',
    'automation/tests/test_check_source.py', 'automation/tests/test_refresh_page.py',
    'automation/tests/test_refresh_development.py', 'automation/tests/test_footer_generation_date.py',
    'automation/tests/test_weekly_check.py', 'automation/tests/test_prepare_development.py',
    'automation/tests/test_release_source.py', 'automation/tests/test_refresh_glossary.py',
    'automation/tests/test_refresh_resources.py',
]


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                   env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=30).strip()


def check_weekly(root, deployed_manifest, resolver=resolve_source, force=False, started_at=None):
    started = time.time() if started_at is None else started_at
    if not math.isfinite(started) or started < 0 or started > time.time():
        raise ValueError('Invalid run start time')
    deadline = started + 21600
    if time.time() >= deadline:
        raise TimeoutError('Weekly run budget exhausted')
    root = Path(root)
    if git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Weekly check requires a clean checkout')
    config = json.loads((root / 'docs-generation.json').read_text())
    document_bytes = (root / 'content/docs/coming-soon.json').read_bytes()
    document = json.loads(document_bytes)
    if (deployed_manifest['sources']['coming-soon']['commit'] != document['version']['commit']
            or deployed_manifest['contentHashes']['coming-soon'] != hashlib.sha256(document_bytes).hexdigest()):
        raise ValueError('Checkout documentation does not match the deployed baseline')
    revision = git(root, 'log', '-1', '--format=%H', '--', 'content/docs/coming-soon.json')
    if not revision:
        raise ValueError('Missing documentation generation history')
    changed = sorted(git(root, 'diff', '--name-only', revision, 'HEAD', '--', *PROCESS_INPUTS).splitlines())
    frozen = select_source(config['source'], resolver)
    selection, commit = frozen['sourceSelection'], frozen['sourceCommit']
    reasons = [] if source_matches(document['version'], frozen) else ['source_changed']
    if changed:
        reasons.append('generation_process_changed')
    if force:
        reasons.append('forced')
    return {'action': 'refresh_needed' if reasons else 'skip', 'reasons': reasons,
            'processFilesChanged': changed, 'documentationRevision': revision, 'head': git(root, 'rev-parse', 'HEAD'),
            'startedEpoch': started, 'deadlineEpoch': deadline, 'budgetSeconds': 21600,
            'sourceCommit': commit, 'documentationCommit': document['version']['commit'],
            'refKind': selection['kind'], 'ref': selection['ref'], **frozen}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--site-url', default=SITE_URLS[0])
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--started-at', type=float, help='Original run start epoch; preserve it on retries')
    args = parser.parse_args(argv)
    started = time.time() if args.started_at is None else args.started_at
    try:
        if not math.isfinite(started) or started < 0 or started > time.time():
            raise ValueError('Invalid run start time')
        if args.site_url not in SITE_URLS:
            raise ValueError('Unsupported documentation site')
        if time.time() >= started + 21600:
            raise TimeoutError('Weekly run budget exhausted')
        request = urllib.request.Request(args.site_url + '/docs-manifest.json', headers={
            'User-Agent': 'paykit-docs-weekly-check', 'Cache-Control': 'no-cache'})
        with urllib.request.urlopen(request, timeout=30) as response:
            manifest = json.load(response)
        result = check_weekly(args.root, manifest, resolver=resolve_source,
                              force=args.force, started_at=started)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': 'weekly_check_failed',
                          'detail': str(error), 'startedEpoch': started, 'deadlineEpoch': started + 21600}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
