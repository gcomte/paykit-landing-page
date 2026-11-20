"""Focused tests for the read-only overview source check."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

SCRIPT = Path(__file__).parents[1] / 'check_source.py'
OLD = 'a' * 40
NEW = 'b' * 40


class CheckSourceTests(unittest.TestCase):
    def fixture(self):
        self.assertTrue(SCRIPT.is_file(), 'The read-only source-check command is missing')
        spec = importlib.util.spec_from_file_location('check_source', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'content/docs').mkdir(parents=True)
        config = {'source': {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'v-next'}}
        (root / 'docs-generation.json').write_text(json.dumps(config))
        doc = {'version': {'commit': OLD, 'tag': 'v1'}, 'pages': [{'slug': 'overview'}]}
        for version in ('released', 'coming-soon'):
            (root / f'content/docs/{version}.json').write_text(json.dumps(doc))
        return root, module

    def test_unchanged_source_skips_without_writes(self):
        root, module = self.fixture()
        before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in root.rglob('*') if p.is_file()}
        calls = []
        def resolve(repository, kind, ref):
            calls.append((repository, kind, ref))
            return OLD
        result = module.check_source(root, 'released', 'overview', resolver=resolve)
        self.assertEqual(result['action'], 'skip')
        self.assertEqual(result['reason'], 'source_unchanged')
        self.assertEqual(calls, [('pubky/paykit-rs', 'tag', 'v1')])
        self.assertEqual(before, {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in root.rglob('*') if p.is_file()})


    def test_force_requests_a_refresh_even_when_unchanged(self):
        root, module = self.fixture()
        result = module.check_source(root, 'released', 'overview', force=True,
                                     resolver=lambda *args: OLD)
        self.assertEqual(result['action'], 'refresh_needed')
        self.assertEqual(result['reason'], 'forced')

    def test_selected_branch_change_requests_a_refresh(self):
        root, module = self.fixture()
        config = json.loads((root / 'docs-generation.json').read_text())
        config['source'].update(kind='branch', ref='feature/docs')
        (root / 'docs-generation.json').write_text(json.dumps(config))
        calls = []
        def resolve(*args):
            calls.append(args)
            return NEW
        result = module.check_source(root, 'coming-soon', 'overview', resolver=resolve)
        self.assertEqual(result['action'], 'refresh_needed')
        self.assertEqual(result['reason'], 'source_changed')
        self.assertEqual(result['documentationCommit'], OLD)
        self.assertEqual(result['sourceCommit'], NEW)
        self.assertEqual(calls, [('pubky/paykit-rs', 'branch', 'feature/docs')])
        self.assertEqual(json.loads((root / 'content/docs/coming-soon.json').read_text())['version']['commit'], OLD)

    def test_lookup_failure_is_not_a_skip(self):
        root, module = self.fixture()
        def unavailable(*args):
            raise OSError('public source unavailable')
        with self.assertRaisesRegex(OSError, 'unavailable'):
            module.check_source(root, 'released', 'overview', resolver=unavailable)

    def test_resolver_keeps_tag_namespace_and_never_falls_back(self):
        import io
        from unittest.mock import patch
        _, module = self.fixture()
        reply = json.dumps({'sha': OLD, 'commit': {'tree': {'sha': NEW}}})
        with patch.object(module.urllib.request, 'urlopen', return_value=io.StringIO(reply)) as opened:
            self.assertEqual(module.resolve_source('pubky/paykit-rs', 'tag', 'v1'), OLD)
            self.assertTrue(opened.call_args.args[0].full_url.endswith('/refs%2Ftags%2Fv1'))
        with patch.object(module.urllib.request, 'urlopen') as opened:
            with self.assertRaisesRegex(ValueError, 'Invalid source ref'):
                module.resolve_source('pubky/paykit-rs', 'tag', 'invalid ref')
            opened.assert_not_called()

    def test_cli_lookup_failure_returns_error_and_nonzero_exit(self):
        import contextlib
        import io
        import sys
        from unittest.mock import patch
        _, module = self.fixture()
        output = io.StringIO()
        with patch.object(sys, 'argv', ['check_source.py', '--version', 'released', '--page', 'overview']), \
                patch.object(module.urllib.request, 'urlopen', side_effect=OSError('offline')), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                module.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())['action'], 'error')

    def test_development_policy_discovers_a_concrete_tag_and_returns_frozen_selection(self):
        from unittest.mock import patch
        from test_release_source import POLICY, release
        import release_source
        root, module = self.fixture()
        (root / 'docs-generation.json').write_text(json.dumps({'source': POLICY}))
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        calls = []
        def resolve(*args):
            calls.append(args)
            return NEW
        with patch.object(release_source, 'fetch_release_page', return_value=([release()], '')):
            try:
                result = module.check_source(root, 'coming-soon', 'overview', resolver=resolve)
            except KeyError as error:
                self.fail('Development checks must understand the newest-release policy: ' + str(error))
        self.assertEqual(result['action'], 'refresh_needed')
        self.assertEqual(result['sourceSelection'], {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': release()['tag_name']})
        self.assertEqual(result['sourcePolicy'], POLICY)
        self.assertEqual(result['release']['id'], release()['id'])
        self.assertEqual(calls, [('pubky/paykit-rs', 'tag', release()['tag_name'])])
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

if __name__ == '__main__':
    unittest.main()
