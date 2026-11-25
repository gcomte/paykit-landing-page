"""Synthetic release records: selection is by publication, never a fixed tag."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

SCRIPT = Path(__file__).parents[1] / 'release_source.py'
POLICY = {'repository': 'pubky/paykit-rs', 'selection': 'newest-release'}
STABLE = {'repository': 'pubky/paykit-rs', 'selection': 'highest-stable'}
COMMIT = 'a' * 40
API = 'https://api.github.com/repos/pubky/paykit-rs'


def release(number=17, tag='synthetic/next-preview', published='2030-01-03T00:00:00Z', **extra):
    return {'id': number, 'tag_name': tag, 'published_at': published,
            'prerelease': True, 'draft': False,
            'html_url': 'https://github.com/pubky/paykit-rs/releases/tag/' + tag, **extra}


class ReleaseSourceTests(unittest.TestCase):
    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'Newest-published release selector is missing')
        sys.path.insert(0, str(SCRIPT.parent))
        spec = importlib.util.spec_from_file_location('release_source', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_latest_prerelease_beats_old_stable_by_publication_not_creation_or_semver(self):
        module = self.module()
        rows = [release(1, 'v99.0.0', '2030-01-01T00:00:00Z', prerelease=False,
                        created_at='2030-01-10T00:00:00Z'),
                release(created_at='2029-01-01T00:00:00Z')]
        calls = []
        def resolve(*args):
            calls.append(args)
            return COMMIT
        result = module.select_source(POLICY, resolve, fetcher=lambda url: (rows, ''))
        self.assertEqual(result, {'sourcePolicy': POLICY,
            'sourceSelection': {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'synthetic/next-preview'},
            'sourceCommit': COMMIT, 'release': {k: release()[k] for k in (
                'id', 'tag_name', 'published_at', 'prerelease', 'html_url')}})
        self.assertEqual(calls, [('pubky/paykit-rs', 'tag', 'synthetic/next-preview')])

    def test_all_release_pages_are_considered_even_if_first_page_is_not_newest(self):
        module = self.module()
        second = API + '/releases?per_page=100&page=2'
        pages = {API + '/releases?per_page=100&page=1':
                 ([release(1, 'old', '2030-01-01T00:00:00Z')], f'<{second}>; rel="next"'),
                 second: ([release()], '')}
        calls = []
        def fetch(url):
            calls.append(url)
            return pages[url]
        result = module.select_source(POLICY, lambda *args: COMMIT, fetcher=fetch)
        self.assertEqual(result['release']['id'], 17)
        self.assertEqual(calls, list(pages))

    def test_drafts_and_unpublished_records_are_not_release_candidates(self):
        module = self.module()
        rows = [release(), release(18, 'draft', '2030-02-01T00:00:00Z', draft=True),
                release(19, 'unpublished', None)]
        try:
            result = module.select_source(POLICY, lambda *args: COMMIT, fetcher=lambda url: (rows, ''))
        except TypeError as error:
            self.fail('Unpublished records must be excluded before parsing timestamps: ' + str(error))
        self.assertEqual(result['release']['id'], 17)

    def test_invalid_newest_tag_is_rejected_without_trying_an_older_release(self):
        module = self.module()
        for tag in ('invalid ref', 'synthetic//tag', '', 'v-next..bad'):
            with self.subTest(tag=tag):
                calls = []
                with self.assertRaisesRegex(ValueError, 'Invalid source ref'):
                    module.select_source(POLICY, lambda *args: calls.append(args),
                        fetcher=lambda url: ([release(1, 'old', '2030-01-01T00:00:00Z'), release(tag=tag)], ''))
                self.assertEqual(calls, [])

    def test_default_transport_reads_anonymous_public_list_with_timeout(self):
        import io
        import urllib.request
        from unittest.mock import patch
        module = self.module()
        reply = io.BytesIO(json.dumps([release()]).encode())
        reply.status, reply.headers = 200, {}
        with patch.object(urllib.request.OpenerDirector, 'open', return_value=reply) as opened:
            try:
                result = module.select_source(POLICY, lambda *args: COMMIT)
            except TypeError as error:
                self.fail('A working anonymous default transport is required: ' + str(error))
        self.assertEqual(result['sourceCommit'], COMMIT)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, API + '/releases?per_page=100&page=1')
        self.assertNotIn('Authorization', request.headers)
        self.assertEqual(opened.call_args.kwargs['timeout'], 30)

    def test_explicit_legacy_branch_or_tag_never_discovers_a_release(self):
        module = self.module()
        for kind in ('tag', 'branch'):
            with self.subTest(kind=kind):
                source = {'repository': 'pubky/paykit-rs', 'kind': kind, 'ref': 'manual/source'}
                calls = []
                def resolve(*args):
                    calls.append(args)
                    return COMMIT
                result = module.select_source(source, resolve,
                    fetcher=lambda url: self.fail('Legacy explicit selections must not discover releases'))
                self.assertEqual(result, {'sourceSelection': source, 'sourceCommit': COMMIT})
                self.assertEqual(calls, [('pubky/paykit-rs', kind, 'manual/source')])

    def test_policy_must_be_exact_and_cannot_mix_dynamic_selection_with_fixed_ref(self):
        module = self.module()
        for source in ({**POLICY, 'kind': 'tag', 'ref': 'stale'},
                       {**POLICY, 'selection': 'latest-stable'},
                       {**POLICY, 'repository': 'other/repository'}):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, 'Unsupported public source'):
                    module.select_source(source, lambda *args: self.fail('Invalid policy resolved a ref'),
                                         fetcher=lambda url: self.fail('Invalid policy reached HTTP'))

    def test_new_release_tag_at_same_commit_refreshes_both_gates_but_body_edits_do_not(self):
        from unittest.mock import patch
        from test_check_source import CheckSourceTests
        from test_weekly_check import WeeklyCheckTests
        import release_source
        for checker in (CheckSourceTests(), WeeklyCheckTests()):
            try:
                result = checker.fixture()
                root, module = result[:2]
                config = root / 'docs-generation.json'
                config.write_text(json.dumps({'source': POLICY, 'defaultVersion': 'coming-soon'}))
                path = root / 'content/docs/coming-soon.json'
                doc = json.loads(path.read_bytes())
                doc['version'].update(kind='tag', ref='older-tag', tag='older-tag')
                doc['pages'] = [{'slug': 'overview'}]
                path.write_text(json.dumps(doc))
                for covered in ('older-tag', release()['tag_name']):
                    doc['version'].update(ref=covered, tag=covered)
                    path.write_text(json.dumps(doc))
                    if hasattr(module, 'check_weekly'):
                        import hashlib
                        checker.git(root, 'add', '.'); checker.git(root, 'commit', '--allow-empty', '-m', 'document selected release')
                        manifest = {'sources': {'coming-soon': {'commit': COMMIT, 'tag': covered, 'ref': covered, 'kind': 'tag'}},
                                    'contentHashes': {'coming-soon': hashlib.sha256(path.read_bytes()).hexdigest()}}
                        call = lambda: module.check_weekly(root, manifest, resolver=lambda *args: COMMIT)
                    else:
                        call = lambda: module.check_source(root, 'coming-soon', 'overview', resolver=lambda *args: COMMIT)
                    before = path.read_bytes()
                    with patch.object(release_source, 'fetch_release_page', return_value=([release(body='arbitrary edited release notes')], '')):
                        actual = call()
                    self.assertEqual(actual['action'], 'refresh_needed' if covered == 'older-tag' else 'skip')
                    self.assertEqual(path.read_bytes(), before)
            finally:
                checker.doCleanups()

    def test_public_default_tracks_newest_release_instead_of_any_fixed_tag(self):
        config = json.loads((SCRIPT.parents[1] / 'docs-generation.json').read_bytes())
        self.assertEqual(config['source'], POLICY)
        self.assertEqual(config.get('releasedSource'), STABLE)

    def test_one_paginated_catalog_selects_stable_by_semver_and_newest_by_publication(self):
        module = self.module()
        choose = getattr(module, 'select_sources', None)
        self.assertIsNotNone(choose, 'A shared release catalog must drive both tracks')
        p1, p2 = [f'{API}/releases?per_page=100&page={i}' for i in (1, 2)]
        canonical_next = 'https://api.github.com/repositories/1100020143/releases?per_page=100&page=2'
        rc = release(1, 'v99.0.0-rc1', prerelease=False)
        rows = {
            p1: ([rc, release(2, 'v1.9.0', '2030-01-02T00:00:00Z', prerelease=False),
                  release(3, 'v5.0.0', '2030-01-01T00:00:00Z', prerelease=True)], f'<{canonical_next}>; rel="next"'),
            p2: ([release(4, 'v1.10.0+build.7', '2030-01-01T00:00:00Z', prerelease=False),
                  release(5, 'v20.0.0', prerelease=False, draft=True),
                  release(6, 'v3.00.0', '2030-01-02T00:00:00Z', prerelease=False),
                  release(7, 'v40.0.0', None, prerelease=False)], ''),
        }
        # The highest stable has an older publication date; RC is newest even with a false flag.

        fetched, resolved = [], []
        def fetch(url):
            fetched.append(url)
            return rows[url]
        def resolve(repo, kind, ref):
            resolved.append(ref)
            return {'sourceCommit': COMMIT, 'sourceObject': 'b' * 40, 'qualifiedRef': 'refs/tags/' + ref}
        result = choose({'released': STABLE, 'coming-soon': POLICY}, resolve, fetcher=fetch)
        self.assertEqual(fetched, [p1, p2])
        self.assertEqual(resolved, ['v1.10.0+build.7', 'v99.0.0-rc1'])
        self.assertEqual(result['released']['release']['tag_name'], 'v1.10.0+build.7')
        self.assertEqual(result['released']['sourceObject'], 'b' * 40)
        self.assertEqual(result['coming-soon']['release']['tag_name'], rc['tag_name'])

    def test_missing_stable_fails_without_resolving_prereleases(self):
        module = self.module()
        with self.assertRaisesRegex(ValueError, 'No published stable'):
            module.select_source(STABLE, lambda *a: self.fail('Resolved a prerelease as stable'),
                fetcher=lambda _: ([release(tag='v2.0.0-rc.1', prerelease=False)], ''))

    def test_tracks_sharing_the_same_highest_release_resolve_that_ref_once(self):
        module = self.module()
        resolved = []
        def resolve(repo, kind, ref):
            resolved.append(ref)
            return {'sourceCommit': COMMIT, 'sourceObject': COMMIT, 'qualifiedRef': f'refs/tags/{ref}'}
        result = module.select_sources({'released': STABLE, 'coming-soon': POLICY}, resolve,
                                        fetcher=lambda _: ([release(tag='1.2.3', prerelease=False)], ''))
        self.assertEqual(resolved, ['1.2.3'])
        self.assertEqual(result['released']['sourceCommit'], result['coming-soon']['sourceCommit'])

    def test_published_release_identity_requires_id_timestamp_and_boolean_prerelease(self):
        module = self.module()
        for field, value in (('id', True), ('published_at', '2030-01-03'), ('prerelease', 'false'), ('html_url', 7)):
            with self.subTest(field=field):
                calls = []
                with self.assertRaisesRegex(ValueError, 'release'):
                    module.select_source(POLICY, lambda *args: calls.append(args),
                        fetcher=lambda url: ([release(**{field: value})], ''))
                self.assertEqual(calls, [])

    def test_pagination_cannot_leave_the_release_list_or_loop(self):
        module = self.module()
        first = API + '/releases?per_page=100&page=1'
        for next_url in ('https://unapproved.invalid/releases?page=2', first):
            with self.subTest(next_url=next_url):
                calls = []
                def fetch(url):
                    calls.append(url)
                    if len(calls) > 1:
                        self.fail('Invalid pagination followed instead of rejecting its URL')
                    return [release()], f'<{next_url}>; rel="next"'
                with self.assertRaisesRegex(ValueError, 'pagination'):
                    module.select_source(POLICY, lambda *args: COMMIT, fetcher=fetch)
                self.assertEqual(calls, [first])

    def test_failed_release_discovery_never_falls_back_to_a_tag_or_branch(self):
        import io
        import urllib.request
        from unittest.mock import patch
        module = self.module()
        for status, body in ((201, json.dumps([release()]).encode()), (200, b'[]'),
                             (200, b'{malformed-json'), (200, json.dumps([release(body='x' * 2_000_001)]).encode())):
            with self.subTest(status=status, size=len(body)):
                response = io.BytesIO(body)
                response.status, response.headers = status, {}
                calls = []
                with patch.object(urllib.request.OpenerDirector, 'open', return_value=response):
                    with self.assertRaises((OSError, ValueError)):
                        module.select_source(POLICY, lambda *args: calls.append(args))
                self.assertEqual(calls, [])
        with patch.object(urllib.request.OpenerDirector, 'open', side_effect=OSError('Synthetic offline')):
            with self.assertRaisesRegex(OSError, 'offline'):
                module.select_source(POLICY, lambda *args: self.fail('Offline selection fell back'))

    def test_malformed_release_list_is_a_controlled_error_not_a_partial_selection(self):
        module = self.module()
        for page in ({'message': 'synthetic API error'}, [release(), {'tag_name': 'incomplete'}]):
            with self.subTest(page=page):
                calls = []
                with self.assertRaisesRegex(ValueError, 'release'):
                    module.select_source(POLICY, lambda *args: calls.append(args), fetcher=lambda url: (page, ''))
                self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
