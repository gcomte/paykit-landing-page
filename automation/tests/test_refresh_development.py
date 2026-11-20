"""Focused mechanical checks using a small synthetic source snapshot."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

SCRIPT = Path(__file__).parents[1] / 'refresh_development.py'
OLD, NEW = 'a' * 40, 'b' * 40
SLUGS = ['overview', 'components', 'flows', 'boundaries', 'wallets', 'point-of-sale']


class DevelopmentRefreshTests(unittest.TestCase):
    def fixture(self):
        self.assertTrue(SCRIPT.is_file(), 'The complete development refresh command is missing')
        spec = importlib.util.spec_from_file_location('refresh_development', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'site'
        sources = Path(temporary.name) / 'sources'
        root.mkdir(); sources.mkdir()
        def put(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value) if not isinstance(value, str) else value)
        put(root / 'docs-generation.json', {'source': {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'v-test'}, 'defaultVersion': 'coming-soon'})
        packages = {'paykit-lib': {'version': '0.1.1-rc8', 'rustVersion': '1.91.1', 'dependencies': {'serde': '1'}, 'published': False}}
        old_metadata = {'commit': OLD, 'packages': packages}
        metadata = json.loads(json.dumps(old_metadata))
        metadata.update(commit=NEW, tag='v-test', ref='v-test', kind='tag', qualifiedRef='refs/tags/v-test', object='c' * 40,
                        label='v-test', sourceUrl=f'https://github.com/pubky/paykit-rs/tree/{NEW}')
        metadata['packages']['paykit-lib']['version'] = '0.1.1-rc9'
        put(sources / 'README.md', '# Synthetic fixture\n')
        library_manifest = '[package]\nname = "paykit-lib"\nversion = "0.1.1-rc8"\nrust-version = "1.91.1"\n\n[dependencies]\nserde = "1"\n'
        put(sources / 'previous/paykit-lib/Cargo.toml', library_manifest)
        put(sources / 'paykit-lib/Cargo.toml', library_manifest.replace('0.1.1-rc8', '0.1.1-rc9'))
        put(sources / 'index.json', {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'v-test', 'commit': NEW, 'version': metadata})
        import hashlib
        evidence = {'id': 'fixture', 'version': 'coming-soon', 'commit': NEW, 'path': 'README.md', 'startLine': 1, 'endLine': 1,
                    'excerpt': '# Synthetic fixture', 'sourceHash': hashlib.sha256((sources / 'README.md').read_bytes()).hexdigest()}
        pages = [{'slug': slug, 'sections': [{'id': 'one', 'evidence': ['fixture']}]} for slug in SLUGS]
        pages[4]['sections'][0]['code'] = [{'artifact': 'wallet/Cargo.toml'}]
        put(root / 'automation/blueprints/coming-soon.json', {'sourceCommit': NEW, 'pages': pages, 'evidence': [evidence], 'diagrams': []})
        put(root / 'content/docs/coming-soon.json', {'version': old_metadata, 'pages': pages})
        put(root / 'content/docs/released.json', {'sentinel': 'released stays byte-identical'})
        put(root / 'automation/sources.lock.json', {'released': {'commit': 'd' * 40}, 'coming-soon': old_metadata})
        put(root / 'public/docs-manifest.json', {'fingerprint': 'previous-whole-run', 'sources': {'released': {'commit': 'd' * 40}, 'coming-soon': {'commit': OLD}}, 'contentHashes': {'released': 'unchanged'}})
        for audience in ('wallet', 'point-of-sale'):
            put(root / f'automation/recipes/{audience}.rs', 'fn main() {}\n')
            put(root / f'public/docs-examples/coming-soon/{audience}/Cargo.lock',
                f'version = 4\n\n[[package]]\nname = "paykit-lib"\nversion = "0.1.1-rc8"\nsource = "git+https://github.com/pubky/paykit-rs?rev={OLD}#{OLD}"\n')
        return root, sources, module

    def test_refresh_rebinds_the_whole_development_version_only(self):
        root, sources, module = self.fixture()
        released = (root / 'content/docs/released.json').read_bytes()
        result = module.refresh_development(root, sources)
        document = json.loads((root / 'content/docs/coming-soon.json').read_text())
        self.assertEqual(document['version']['commit'], NEW)
        self.assertEqual([p['slug'] for p in document['pages']], SLUGS)
        self.assertIn(NEW, document['pages'][4]['sections'][0]['code'][0]['code'])
        self.assertEqual((root / 'content/docs/released.json').read_bytes(), released)
        self.assertEqual(json.loads((root / 'public/docs-manifest.json').read_text())['sources']['released']['commit'], 'd' * 40)
        self.assertTrue(result['changedFiles'])


    def test_wrong_source_refuses_without_touching_outputs(self):
        root, sources, module = self.fixture()
        path = root / 'automation/blueprints/coming-soon.json'
        blueprint = json.loads(path.read_text()); blueprint['sourceCommit'] = OLD
        path.write_text(json.dumps(blueprint))
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'source'):
            module.refresh_development(root, sources)
        self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_changed_evidence_refuses_before_writes(self):
        root, sources, module = self.fixture()
        (sources / 'README.md').write_text('# Changed fixture\n')
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'Evidence'):
            module.refresh_development(root, sources)
        self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_changed_dependencies_need_a_new_lock_resolution(self):
        root, sources, module = self.fixture()
        path = sources / 'index.json'
        index = json.loads(path.read_text())
        index['version']['packages']['paykit-lib']['dependencies']['serde'] = '2'
        path.write_text(json.dumps(index))
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'lock'):
            module.refresh_development(root, sources)
        self.assertEqual(before, {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_repeated_refresh_is_a_noop_including_archives(self):
        root, sources, module = self.fixture()
        module.refresh_development(root, sources)
        before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        self.assertEqual(module.refresh_development(root, sources)['changedFiles'], [])
        self.assertEqual(before, {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})

    def test_manifest_feature_change_needs_a_new_lock_resolution(self):
        root, sources, module = self.fixture()
        path = sources / 'paykit-lib/Cargo.toml'
        path.write_text(path.read_text() + '\n[features]\ndefault = ["extra"]\nextra = []\n')
        with self.assertRaisesRegex(ValueError, 'lock'):
            module.refresh_development(root, sources)

    def test_contradictory_displayed_source_refuses_before_writes(self):
        root, sources, module = self.fixture()
        path = sources / 'index.json'
        index = json.loads(path.read_text()); index['version']['branch'] = 'master'
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(ValueError, 'source'):
            module.refresh_development(root, sources)

    def policy_fixture(self):
        from test_release_source import POLICY, release
        root, sources, module = self.fixture()
        (root / 'docs-generation.json').write_text(json.dumps({'source': POLICY, 'defaultVersion': 'coming-soon'}))
        path = sources / 'index.json'
        index = json.loads(path.read_bytes())
        selected = release(tag=index['ref'])
        index['sourcePolicy'] = POLICY
        index['version']['release'] = {k: selected[k] for k in ('id', 'tag_name', 'published_at', 'prerelease', 'html_url')}
        path.write_text(json.dumps(index))
        return root, sources, module, index

    def test_policy_snapshot_renders_frozen_tag_without_live_release_discovery(self):
        from test_release_source import POLICY
        root, sources, module, index = self.policy_fixture()
        try:
            module.refresh_development(root, sources)
        except ValueError as error:
            self.fail('Renderer must accept the concrete policy-bound frozen tag: ' + str(error))
        doc = json.loads((root / 'content/docs/coming-soon.json').read_bytes())
        self.assertEqual(doc['version']['release'], index['version']['release'])
        self.assertEqual(doc['version']['ref'], index['ref'])
        lock = json.loads((root / 'automation/sources.lock.json').read_bytes())
        self.assertEqual(lock['selectedSource'], {'repository': 'pubky/paykit-rs', 'kind': 'tag',
            'ref': index['ref'], 'commit': NEW, 'object': index['version']['object'],
            'qualifiedRef': 'refs/tags/' + index['ref'], 'sourcePolicy': POLICY})
        self.assertEqual(module.refresh_development(root, sources)['changedFiles'], [])

    def test_renderer_rejects_contradictory_frozen_release_identity_before_writes(self):
        for field, value in (('tag_name', 'different-tag'),
                             ('html_url', 'https://github.com/other/repo/releases/tag/v-test')):
            with self.subTest(field=field):
                root, sources, module, index = self.policy_fixture()
                index['version']['release'][field] = value
                (sources / 'index.json').write_text(json.dumps(index))
                before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'release|selection'):
                    module.refresh_development(root, sources)
                self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

if __name__ == '__main__':
    unittest.main()
