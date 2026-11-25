"""Small synthetic Git histories for the read-only weekly gate."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import hashlib
import sys

SCRIPT = Path(__file__).parents[1] / 'weekly_check.py'
COMMIT = 'a' * 40


class WeeklyCheckTests(unittest.TestCase):
    def fixture(self):
        self.assertTrue(SCRIPT.exists(), 'The weekly source/process check is missing')
        sys.path.insert(0, str(SCRIPT.parent))
        spec = importlib.util.spec_from_file_location('weekly_check', SCRIPT)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'content/docs').mkdir(parents=True); (root / 'automation').mkdir()
        (root / 'docs-generation.json').write_text(json.dumps({'source': {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'v-test'}, 'defaultVersion': 'coming-soon'}))
        (root / 'content/docs/coming-soon.json').write_text(json.dumps({'version': {'commit': COMMIT}, 'lastUpdated': '2026-11-20', 'pages': []}))
        (root / 'automation/COORDINATOR.md').write_text('Public author and independent review.\n')
        (root / 'app').mkdir()
        for name in ['layout.tsx', 'globals.css']:
            (root / 'app' / name).write_text('Shared application input\n')
        self.git(root, 'init', '--initial-branch=docs-automation')
        self.git(root, 'config', 'user.name', 'Fixture')
        self.git(root, 'config', 'user.email', 'fixture@example.invalid')
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-m', 'fixture published documentation')
        manifest = {'sources': {'coming-soon': {'commit': COMMIT}}, 'contentHashes': {'coming-soon': hashlib.sha256((root / 'content/docs/coming-soon.json').read_bytes()).hexdigest()}}
        return root, module, manifest

    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def test_unchanged_deployed_source_and_process_skip_without_writes(self):
        root, module, manifest = self.fixture()
        before = (root / 'content/docs/coming-soon.json').read_bytes()
        result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
        self.assertEqual(result['action'], 'skip')
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['processFilesChanged'], [])
        self.assertEqual((root / 'content/docs/coming-soon.json').read_bytes(), before)
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')
    def test_unpublished_or_unknown_baseline_refuses_before_upstream_lookup(self):
        root, module, manifest = self.fixture()
        for group, field in [('sources', 'commit'), ('contentHashes', None)]:
            wrong = json.loads(json.dumps(manifest))
            if field:
                wrong[group]['coming-soon'][field] = 'b' * 40
            else:
                wrong[group]['coming-soon'] = '0' * 64
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, 'deployed'):
                module.check_weekly(root, wrong, resolver=lambda *args: self.fail('Unpublished inputs must not reach upstream'))

    def test_a_new_selected_commit_requires_refresh(self):
        root, module, manifest = self.fixture()
        result = module.check_weekly(root, manifest, resolver=lambda *args: 'b' * 40)
        self.assertEqual(result['action'], 'refresh_needed')
        self.assertEqual(result['reasons'], ['source_changed'])
        self.assertEqual(result['sourceCommit'], 'b' * 40)
        self.assertEqual(result['documentationCommit'], COMMIT)

    def test_committed_generation_inputs_trigger_but_unrelated_commits_do_not(self):
        root, module, manifest = self.fixture()
        (root / 'notes.txt').write_text('unrelated note')
        self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'unrelated')
        self.assertEqual(module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)['action'], 'skip')
        names = ['automation/COORDINATOR.md', 'automation/WEEKLY.md', 'components/docs/DocsPage.tsx', 'automation/blueprints/coming-soon.json', 'docs-generation.json', 'automation/weekly_check.py', 'playwright.config.ts', 'public/docs-examples/coming-soon/wallet/Cargo.lock']
        for name in names:
            file = root / name; file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text((file.read_text() if file.exists() else '') + '\n')
        self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'generation inputs changed')
        result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
        self.assertEqual(result['action'], 'refresh_needed')
        self.assertEqual(result['reasons'], ['generation_process_changed'])
        self.assertEqual(result['processFilesChanged'], sorted(names))
        self.assertEqual(result['documentationRevision'], self.git(root, 'log', '-1', '--format=%H', '--', 'content/docs/coming-soon.json'))

    def test_committed_shared_app_inputs_require_refresh_without_source_changes(self):
        for name in ['app/layout.tsx', 'app/globals.css']:
            with self.subTest(path=name):
                root, module, manifest = self.fixture()
                document = root / 'content/docs/coming-soon.json'
                before = document.read_bytes()
                revision = self.git(root, 'rev-parse', 'HEAD')
                self.assertEqual(module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)['action'], 'skip')
                target = root / name
                target.write_text(target.read_text() + '\n')
                self.git(root, 'add', name)
                self.git(root, 'commit', '-m', 'shared docs rendering input changed')
                result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(result['action'], 'refresh_needed')
                self.assertEqual(result['reasons'], ['generation_process_changed'])
                self.assertEqual(result['processFilesChanged'], [name])
                self.assertEqual(result['documentationRevision'], revision)
                self.assertEqual(result['sourceCommit'], COMMIT)
                self.assertEqual(result['documentationCommit'], COMMIT)
                self.assertEqual(document.read_bytes(), before)
                self.assertEqual(self.git(root, 'status', '--porcelain'), '')

    def test_dirty_checkout_refuses_before_upstream_lookup(self):
        root, module, manifest = self.fixture()
        file = root / 'automation/COORDINATOR.md'; file.write_text('Uncommitted instructions')
        with self.assertRaisesRegex(ValueError, 'clean'):
            module.check_weekly(root, manifest, resolver=lambda *args: self.fail('Dirty inputs reached upstream'))

    def test_force_requests_fresh_work_without_writing_any_state(self):
        root, module, manifest = self.fixture()
        before = (root / 'content/docs/coming-soon.json').read_bytes()
        result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT, force=True)
        self.assertEqual(result['action'], 'refresh_needed')
        self.assertEqual(result['reasons'], ['forced'])
        self.assertEqual((root / 'content/docs/coming-soon.json').read_bytes(), before)
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')

    def test_fixed_six_hour_budget_cannot_be_reset_by_an_expired_check(self):
        root, module, manifest = self.fixture()
        from unittest.mock import patch
        with patch.object(module.time, 'time', return_value=100000):
            result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
            self.assertEqual(result['startedEpoch'], 100000)
            self.assertEqual(result['deadlineEpoch'] - result['startedEpoch'], 21600)
            with self.assertRaisesRegex(TimeoutError, 'budget'):
                module.check_weekly(root, manifest, resolver=lambda *args: self.fail('Expired check reached upstream'), started_at=78399)

    def test_cli_reports_real_decision_and_rejects_unknown_site(self):
        root, module, manifest = self.fixture()
        import contextlib
        import io
        import urllib.request
        from unittest.mock import patch
        self.assertTrue(callable(getattr(module, 'main', None)), 'Weekly CLI entry point is missing')
        stream = io.StringIO()
        response = io.BytesIO(json.dumps(manifest).encode())
        response.status, response.headers = 200, {}
        with patch.object(urllib.request.OpenerDirector, 'open', return_value=response) as fetch, patch.object(module, 'resolve_source', return_value=COMMIT), contextlib.redirect_stdout(stream):
            module.main(['--root', str(root)])
        result = json.loads(stream.getvalue())
        self.assertEqual(result['action'], 'skip')
        self.assertEqual(fetch.call_args.kwargs['timeout'], 30)
        self.assertEqual(fetch.call_args.args[0].full_url, module.SITE_URLS[0] + '/docs-manifest.json')
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), self.assertRaises(SystemExit) as exit_result:
            module.main(['--root', str(root), '--site-url', 'https://not-approved.example'])
        self.assertEqual(exit_result.exception.code, 1)
        self.assertEqual(json.loads(stream.getvalue())['action'], 'error')
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')

    def test_invalid_start_times_cannot_bypass_the_run_budget(self):
        root, module, manifest = self.fixture()
        from unittest.mock import patch
        with patch.object(module.time, 'time', return_value=100000):
            for start in [float('nan'), float('inf'), 100001]:
                with self.subTest(start=start), self.assertRaisesRegex(ValueError, 'start'):
                    module.check_weekly(root, manifest, resolver=lambda *args: COMMIT, started_at=start)

    def test_weekly_policy_returns_one_frozen_concrete_selection(self):
        from unittest.mock import patch
        from test_release_source import POLICY, release
        import release_source
        root, module, manifest = self.fixture()
        (root / 'docs-generation.json').write_text(json.dumps({'source': POLICY, 'defaultVersion': 'coming-soon'}))
        self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'select newest published release')
        with patch.object(release_source, 'fetch_release_page', return_value=([release()], '')) as fetched:
            try:
                result = module.check_weekly(root, manifest, resolver=lambda *args: 'b' * 40)
            except KeyError as error:
                self.fail('Weekly gate must resolve policy to a concrete release: ' + str(error))
        self.assertEqual(result['sourceSelection'], {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': release()['tag_name']})
        self.assertEqual(result['sourcePolicy'], POLICY)
        self.assertEqual(result['sourceCommit'], 'b' * 40)
        self.assertEqual(result['release']['id'], release()['id'])
        self.assertEqual(fetched.call_count, 1)
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')

    def dual_fixture(self):
        from test_release_source import POLICY, STABLE, release
        root, module, manifest = self.fixture()
        (root / 'docs-generation.json').write_text(json.dumps({'source': POLICY, 'releasedSource': STABLE}))
        rc = release(tag='v2.0.0-rc.1', prerelease=False)
        stable = release(19, 'v1.0.0', '2030-01-01T00:00:00Z', prerelease=False)
        for version, row, commit in [('released', stable, 'd' * 40), ('coming-soon', rc, COMMIT)]:
            body = root / f'content/docs/{version}.json'
            body.write_text(json.dumps({'version': {'commit': commit, 'tag': row['tag_name']}}))
            manifest['sources'][version] = {'commit': commit, 'tag': row['tag_name']}
            manifest['contentHashes'][version] = hashlib.sha256(body.read_bytes()).hexdigest()
        self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'live versions')
        return root, module, manifest, rc, stable

    def test_stable_only_refresh_when_newest_publication_is_unchanged(self):
        from test_release_source import release
        root, module, manifest, rc, stable = self.dual_fixture()
        new = release(20, 'v1.1.0', '2030-01-02T00:00:00Z', prerelease=False)
        calls = []
        def fetch(url):
            calls.append(url)
            return [rc, stable, new], ''
        def resolve(repo, kind, ref):
            return {'sourceCommit': COMMIT if ref == rc['tag_name'] else 'e' * 40,
                    'sourceObject': 'b' * 40, 'qualifiedRef': 'refs/tags/' + ref}
        result = module.check_weekly(root, manifest, resolver=resolve, fetcher=fetch)
        self.assertEqual(result['refreshVersions'], ['released'])
        self.assertEqual(result['reasons'], ['source_changed'])
        self.assertEqual(result['selections']['released']['release']['tag_name'], new['tag_name'])
        self.assertEqual(result['selections']['released']['documentationCommit'], 'd' * 40)
        self.assertEqual(result['sourceCommit'], result['selections']['coming-soon']['sourceCommit'])
        self.assertEqual(result['documentationCommit'], COMMIT)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.git(root, 'status', '--porcelain'), '')

    def test_unchanged_tracks_skip_and_process_only_keeps_selected_source_scope(self):
        root, module, manifest, rc, stable = self.dual_fixture()
        def resolve(repo, kind, ref):
            return {'sourceCommit': COMMIT if ref == rc['tag_name'] else 'd' * 40,
                    'sourceObject': 'b' * 40, 'qualifiedRef': 'refs/tags/' + ref}
        def gate():
            return module.check_weekly(root, manifest, resolver=resolve, fetcher=lambda _: ([rc, stable], ''))
        self.assertEqual(gate()['refreshVersions'], [])
        self.assertEqual(gate()['action'], 'skip')
        (root / 'app/layout.tsx').write_text('New shared layout')
        self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'process change')
        result = gate()
        self.assertEqual(result['reasons'], ['generation_process_changed'])
        self.assertEqual(result['refreshVersions'], ['coming-soon'])

    def test_released_baseline_commit_and_hash_are_required_before_catalog_reads(self):
        root, module, manifest, _, _ = self.dual_fixture()
        for group in ('sources', 'contentHashes'):
            with self.subTest(group=group):
                wrong = json.loads(json.dumps(manifest))
                wrong[group]['released'] = {'commit': 'f' * 40} if group == 'sources' else '0' * 64
                with self.assertRaisesRegex(ValueError, 'released.*deployed'):
                    module.check_weekly(root, wrong, fetcher=lambda _: self.fail('Bad baseline reached HTTP'))

    def test_contradictory_deployed_tag_or_ref_refuses_before_catalog_reads(self):
        root, module, manifest, rc, stable = self.dual_fixture()
        manifest['sources']['released']['tag'] = stable['tag_name']
        manifest['sources']['coming-soon']['tag'] = rc['tag_name']
        def resolve(repo, kind, ref):
            return {'sourceCommit': COMMIT if ref == rc['tag_name'] else 'd' * 40,
                    'sourceObject': 'b' * 40, 'qualifiedRef': 'refs/tags/' + ref}
        self.assertEqual(module.check_weekly(root, manifest, resolver=resolve,
                         fetcher=lambda _: ([rc, stable], ''))['action'], 'skip')
        for version, field in (('released', 'tag'), ('coming-soon', 'ref')):
            wrong = json.loads(json.dumps(manifest))
            wrong['sources'][version][field] = 'v1.0.1'
            with self.subTest(version=version, field=field), self.assertRaisesRegex(ValueError, 'deployed'):
                module.check_weekly(root, wrong, fetcher=lambda _: self.fail('Bad baseline reached HTTP'))

    def test_release_and_glossary_process_dependencies_each_trigger_refresh(self):
        for name in ('automation/release_source.py', 'automation/refresh_glossary.py',
                     'automation/demo_locks.py', 'automation/tests/test_demo_locks.py',
                     'automation/verify_stable_package.py',
                     'automation/tests/test_release_source.py', 'automation/tests/test_refresh_glossary.py'):
            with self.subTest(path=name):
                root, module, manifest = self.fixture()
                self.assertEqual(module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)['action'], 'skip')
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('Synthetic generation dependency\n')
                self.git(root, 'add', name); self.git(root, 'commit', '-m', 'new docs generation dependency')
                result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(result['action'], 'refresh_needed')
                self.assertEqual(result['processFilesChanged'], [name])
                self.assertEqual(result['reasons'], ['generation_process_changed'])

    def test_resources_dependencies_each_trigger_refresh_but_unrelated_commit_skips(self):
        for name in ('automation/refresh_resources.py', 'automation/tests/test_refresh_resources.py', 'tests/resources-policy.test.mjs'):
            with self.subTest(path=name):
                root, module, manifest = self.fixture()
                (root / 'unrelated.txt').write_text('Synthetic unrelated commit\n')
                self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'unrelated')
                self.assertEqual(module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)['action'], 'skip')
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('Synthetic Resources generation dependency\n')
                self.git(root, 'add', name); self.git(root, 'commit', '-m', 'Resources dependency changed')
                before = (root / 'content/docs/coming-soon.json').read_bytes()
                result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(result['action'], 'refresh_needed')
                self.assertEqual(result['reasons'], ['generation_process_changed'])
                self.assertEqual(result['processFilesChanged'], [name])
                self.assertEqual((root / 'content/docs/coming-soon.json').read_bytes(), before)
                self.assertEqual(self.git(root, 'status', '--porcelain'), '')


    def test_actual_homepage_export_producers_and_parity_checks_trigger_but_unrelated_does_not(self):
        # Discover real local import edges from the export entry, not a guessed
        # duplicate of PROCESS_INPUTS. External packages stay package-lock-bound.
        import re
        source_root = SCRIPT.parents[1]
        pending = [source_root / "app/markdown/route.ts"]
        self.assertTrue(pending[0].is_file(), "Approved homepage export entry is missing")
        producers = set()
        while pending:
            file = pending.pop()
            if file in producers:
                continue
            producers.add(file)
            if file.suffix not in (".ts", ".tsx", ".mjs"):
                continue
            for name in re.findall(r"(?:from\s*|import\s*)[\"']([^\"']+)[\"']", file.read_text()):
                if not (name.startswith(".") or name.startswith("@/")):
                    continue
                target = source_root / name[2:] if name.startswith("@/") else file.parent / name
                candidates = [target, *[Path(str(target) + suffix) for suffix in (".ts", ".tsx", ".mjs")]]
                resolved = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
                self.assertIsNotNone(resolved, name)
                pending.append(resolved)
        checks = [source_root / "tests/homepage-markdown.test.mjs", source_root / "tests/homepage-markdown-observe.py"]
        for file in checks:
            self.assertTrue(file.is_file(), str(file))
        for file in sorted(producers | set(checks)):
            name = str(file.relative_to(source_root))
            with self.subTest(actual_producer=name):
                root, module, manifest = self.fixture()
                (root / "unrelated-homepage-note.txt").write_text("Synthetic unrelated control\n")
                self.git(root, "add", "."); self.git(root, "commit", "-m", "unrelated control")
                self.assertEqual(module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)["action"], "skip")
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((target.read_text() if target.exists() else "") + "\n")
                self.git(root, "add", name); self.git(root, "commit", "-m", "actual homepage producer changed")
                before = (root / "content/docs/coming-soon.json").read_bytes()
                result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(result["action"], "refresh_needed", name)
                self.assertEqual(result["reasons"], ["generation_process_changed"])
                self.assertEqual(result["processFilesChanged"], [name])
                self.assertEqual((root / "content/docs/coming-soon.json").read_bytes(), before)
                self.assertEqual(self.git(root, "status", "--porcelain"), "")


    def test_actual_llm_guide_and_alias_producers_trigger_but_unrelated_commit_skips(self):
        # Discover the real module graph, independently of PROCESS_INPUTS.
        import re
        source_root = SCRIPT.parents[1]
        pending = [source_root / name for name in (
            'app/index.md/route.ts', 'app/llms.txt/route.ts', 'app/docs/index.md/route.ts',
            'app/docs/[version]/[slug]/index.md/route.ts', 'next.config.ts')]
        producers = set()
        while pending:
            file = pending.pop()
            self.assertTrue(file.is_file(), str(file))
            if file in producers:
                continue
            producers.add(file)
            if file.suffix not in ('.ts', '.tsx', '.mjs'):
                continue
            for name in re.findall(r'(?:from\s*|import\s*)[\"\']([^\"\']+)[\"\']', file.read_text()):
                if not (name.startswith('.') or name.startswith('@/')):
                    continue
                target = source_root / name[2:] if name.startswith('@/') else file.parent / name
                candidates = [target, *[Path(str(target) + suffix) for suffix in ('.ts', '.tsx', '.mjs')]]
                resolved = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
                self.assertIsNotNone(resolved, name)
                pending.append(resolved)
        producers.add(source_root / 'tests/docs-llms.test.mjs')
        self.assertIn(source_root / 'components/docs/llms.ts', producers)
        for file in sorted(producers):
            name = str(file.relative_to(source_root))
            with self.subTest(actual_producer=name):
                root, module, manifest = self.fixture()
                (root / 'unrelated-llm-note.txt').write_text('Synthetic unrelated control\n')
                self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'unrelated control')
                self.assertEqual(module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)['action'], 'skip')
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((target.read_text() if target.exists() else '') + '\n')
                self.git(root, 'add', name); self.git(root, 'commit', '-m', 'actual LLM producer changed')
                before = (root / 'content/docs/coming-soon.json').read_bytes()
                result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(result['action'], 'refresh_needed', name)
                self.assertEqual(result['reasons'], ['generation_process_changed'])
                self.assertEqual(result['processFilesChanged'], [name])
                self.assertEqual((root / 'content/docs/coming-soon.json').read_bytes(), before)
                self.assertEqual(self.git(root, 'status', '--porcelain'), '')


    def test_newcomer_helper_role_and_check_each_trigger_real_history_refresh(self):
        # Synthetic committed histories drive the real cheap decision, not mocked fingerprints.
        for name in ('automation/newcomer_review.py', 'automation/skills/newcomer-review.md',
                     'automation/tests/test_newcomer_review.py'):
            with self.subTest(path=name):
                root, module, manifest = self.fixture()
                before = (root / 'content/docs/coming-soon.json').read_bytes()
                (root / 'synthetic-unrelated.txt').write_text('Synthetic unrelated control\n')
                self.git(root, 'add', '.'); self.git(root, 'commit', '-m', 'unrelated control')
                from unittest.mock import patch
                with patch.object(module.subprocess, 'check_output', wraps=subprocess.check_output) as commands:
                    skipped = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(skipped['action'], 'skip')
                self.assertEqual(skipped['processFilesChanged'], [])
                self.assertTrue(commands.call_args_list)
                # No normal-skip author, factual reviewer, newcomer or helper subprocess.
                self.assertTrue(all(call.args[0][0] == 'git' for call in commands.call_args_list))
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('Synthetic newcomer process input\n')
                self.git(root, 'add', name); self.git(root, 'commit', '-m', 'newcomer input changed')
                result = module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                self.assertEqual(result['action'], 'refresh_needed')
                self.assertEqual(result['reasons'], ['generation_process_changed'])
                self.assertEqual(result['processFilesChanged'], [name])
                self.assertEqual((root / 'content/docs/coming-soon.json').read_bytes(), before)
                self.assertEqual(self.git(root, 'status', '--porcelain'), '')

    def test_active_newcomer_workflow_names_real_gate_and_keeps_skip_publication_boundaries(self):
        source_root = SCRIPT.parents[1]
        for name in ('automation/COORDINATOR.md', 'automation/WEEKLY.md'):
            body = (source_root / name).read_text()
            self.assertIn('newcomer_review.py capture', body)
            self.assertIn('newcomer_review.py validate', body)
            self.assertIn('--author-task-id', body)
            self.assertIn('--factual-reviewer-task-id', body)
            self.assertIn('skills/newcomer-review.md', body)
        weekly = (source_root / 'automation/WEEKLY.md').read_text()
        self.assertIn('Public AUTHOR and factual REVIEWER get separate fresh contexts: only frozen', weekly)
        self.assertIn('The distinct PUBLIC NEWCOMER receives only rendered public reader inputs', weekly)
        self.assertIn('weekly_check, newcomer_review, prepare_development', weekly)
        role = (source_root / 'automation/skills/newcomer-review.md').read_text()
        for token in ('reading-only', 'reviewerTaskId', 'inputManifestSha256', 'checkedRoutes',
                      'readerQuestion', 'blocking', 'observations', 'Talos'):
            self.assertIn(token, role)
        registry = json.loads((source_root / 'automation/corrections.json').read_bytes())
        self.assertIn('docs-newcomer-reading-review', [row['id'] for row in registry['requirements']])
        writing = (source_root / 'automation/skills/writing.md').read_text()
        for token in ('identity versus App ID at first relevant use',
                      'verified existing public escalation handoff', 'non-sensitive diagnostics',
                      'No repeated warning/provenance box or new support service'):
            self.assertIn(token, writing)


if __name__ == '__main__':
    unittest.main()
