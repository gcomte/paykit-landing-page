#!/usr/bin/env python3
"""Read-only weekly decision against the documentation actually deployed."""
import json
import argparse
import hashlib
import os
import subprocess
import time
import math
from pathlib import Path
from check_source import resolve_source_identity as resolve_source
from release_source import configured_sources, read_public, select_sources, source_matches


SITE_URLS = ['https://paykit-landing-page-lake.vercel.app',
             'https://paykit-landing-page-git-docs-automation-g-a0aa.vercel.app']

PROCESS_INPUTS = [
    'docs-generation.json', 'automation/COORDINATOR.md', 'automation/WEEKLY.md',
    'automation/README.md', 'automation/corrections.json', 'automation/SOURCE-BOUNDARY.md',
    'automation/refresh_page.py', 'automation/refresh_development.py', 'automation/check_source.py',
    'automation/weekly_check.py', 'automation/newcomer_review.py',
    'automation/prepare_development.py', 'automation/recipes',
    'automation/release_source.py', 'automation/refresh_glossary.py', 'automation/refresh_resources.py',
    'automation/demo_locks.py', 'automation/tests/test_demo_locks.py', 'automation/verify_stable_package.py',
    'automation/blueprints/coming-soon.json', 'automation/skills',
    'components/docs', 'app/docs', 'app/index.md', 'app/layout.tsx', 'app/globals.css', 'app/llms.txt', 'package.json', 'package-lock.json',
    # Homepage exports consume the actual request entry and these shared producers.
    'app/page.tsx', 'app/markdown', 'components/homepage', 'components/Landing.tsx',
    'components/Features.tsx', 'components/Interactions.tsx', 'components/interactionContent.ts',
    'components/PaymentIllustration.tsx', 'components/PaymentStoryController.tsx',
    'components/PaymentStory.module.css', 'lib/conference.mjs',
    'tests/homepage-markdown.test.mjs', 'tests/homepage-markdown-observe.py',
    'tsconfig.json', 'next.config.ts', 'postcss.config.mjs', 'playwright.config.ts',
    'tests/browser/landing-docs.spec.ts', 'public/docs-examples/coming-soon',
    'tests/docs-*.test.mjs', 'tests/browser/docs*.spec.ts', 'tests/resources-policy.test.mjs',
    'automation/tests/test_check_source.py', 'automation/tests/test_refresh_page.py',
    'automation/tests/test_refresh_development.py', 'automation/tests/test_footer_generation_date.py',
    'automation/tests/test_weekly_check.py', 'automation/tests/test_prepare_development.py',
    'automation/tests/test_release_source.py', 'automation/tests/test_refresh_glossary.py',
    'automation/tests/test_refresh_resources.py', 'automation/tests/test_newcomer_review.py',
]


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                   env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=30).strip()


def check_weekly(root, deployed_manifest, resolver=resolve_source, force=False, started_at=None, *, fetcher=None):
    started = time.time() if started_at is None else started_at
    if not math.isfinite(started) or started < 0 or started > time.time():
        raise ValueError('Invalid run start time')
    deadline = started + 21600
    if time.time() >= deadline:
        raise TimeoutError('Weekly run budget exhausted')
    root = Path(root)
    if git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Weekly check requires a clean checkout')
    policies = configured_sources(json.loads((root / 'docs-generation.json').read_text()))
    documents, hashes = {}, {}
    for version in policies:
        body = (root / f'content/docs/{version}.json').read_bytes()
        hashes[version] = hashlib.sha256(body).hexdigest()
        documents[version] = json.loads(body)['version']
        deployed = deployed_manifest['sources'].get(version, {})
        document = documents[version]
        identity = {'tag': document.get('tag'), 'ref': document.get('ref', document.get('tag')),
                    'kind': document.get('kind', 'tag' if document.get('tag') else None)}
        if (deployed.get('commit') != document['commit']
                or deployed.get('tag') != identity['tag']
                or any(deployed[key] != identity[key] for key in ('ref', 'kind') if key in deployed)
                or deployed_manifest['contentHashes'].get(version) != hashes[version]):
            raise ValueError(f'Checkout {version} documentation does not match the deployed baseline')
    revision = git(root, 'log', '-1', '--format=%H', '--', 'content/docs/coming-soon.json')
    if not revision:
        raise ValueError('Missing documentation generation history')
    changed = sorted(git(root, 'diff', '--name-only', revision, 'HEAD', '--', *PROCESS_INPUTS).splitlines())
    selections = select_sources(policies, resolver, fetcher, require_object='released' in policies)
    refresh = {version for version, frozen in selections.items() if not source_matches(documents[version], frozen)}
    for version, frozen in selections.items():
        frozen.update(documentationCommit=documents[version]['commit'], documentationContentHash=hashes[version])
    reasons = ['source_changed'] if refresh else []
    if changed:
        reasons.append('generation_process_changed')
    if force:
        reasons.append('forced')
    if changed or force:
        refresh.add('coming-soon')  # Existing full selected-source review on process-only/forced runs.
    if time.time() >= deadline:
        raise TimeoutError('Weekly run budget exhausted')
    frozen = selections['coming-soon']
    selection = frozen['sourceSelection']
    # Keep the original top-level selected-source fields for existing consumers.
    shorthand = {key: value for key, value in frozen.items() if key != 'documentationContentHash'}
    return {'action': 'refresh_needed' if reasons else 'skip', 'reasons': reasons,
            'processFilesChanged': changed, 'documentationRevision': revision, 'head': git(root, 'rev-parse', 'HEAD'),
            'startedEpoch': started, 'deadlineEpoch': deadline, 'budgetSeconds': 21600,
            'refreshVersions': [version for version in policies if version in refresh], 'selections': selections,
            'refKind': selection['kind'], 'ref': selection['ref'], **shorthand}


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
        manifest = json.loads(read_public(args.site_url + '/docs-manifest.json')[1])
        result = check_weekly(args.root, manifest, resolver=resolve_source,
                              force=args.force, started_at=started)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': 'weekly_check_failed',
                          'detail': str(error), 'startedEpoch': started, 'deadlineEpoch': started + 21600}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
