"""Synthetic reference-page mechanics only; no Paykit product assertions."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_refresh_glossary as glossary_fixtures
from test_refresh_glossary import encode

SCRIPT = Path(__file__).parents[1] / 'refresh_resources.py'


class RefreshResourcesTests(unittest.TestCase):
    def fixture(self, version='released'):
        # Reuse the owned glossary fixture, preserving its original seven pages.
        owner = glossary_fixtures.RefreshGlossaryTests()
        self.addCleanup(owner.doCleanups)
        root, sources, document, blueprint, existing, manifest = owner.fixture(version)
        document['pages'].append(copy.deepcopy(blueprint['pages'][-1]))
        page = copy.deepcopy(blueprint['pages'][-1])
        page.update(slug='resources', title='Resources', description='Synthetic source reading order.')
        page['sections'].reverse()  # Resources is topical, not alphabetical.
        commit = document['version']['commit']
        for section in page['sections']:
            section['links'].insert(0, {'label': 'Synthetic source file',
                'href': f'https://github.com/pubky/paykit-rs/blob/{commit}/terms.txt#L2-L3'})
        blueprint['pages'].append(page)
        (root / f'content/docs/{version}.json').write_bytes(encode(document))
        (root / f'automation/blueprints/{version}.json').write_bytes(encode(blueprint))
        data = (sources / 'terms.txt').read_bytes()
        (sources / 'index.json').write_bytes(encode({'repository': 'pubky/paykit-rs', 'commit': commit,
            'publicOnly': True, 'files': [{'path': 'terms.txt',
            'url': f'https://raw.githubusercontent.com/pubky/paykit-rs/{commit}/terms.txt',
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}]}))
        return root, sources, document, blueprint, existing, manifest

    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'The Resources renderer is missing')
        sys.path.insert(0, str(SCRIPT.parent))
        spec = importlib.util.spec_from_file_location('refresh_resources', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def snapshot(self, root):
        return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in root.rglob('*') if p.is_file()}

    def test_bootstraps_resources_only_in_each_version_from_frozen_evidence(self):
        module = self.module()
        for version in ('released', 'coming-soon'):
            with self.subTest(version=version):
                root, sources, original, blueprint, existing, manifest = self.fixture(version)
                before, frozen = self.snapshot(root), self.snapshot(sources)
                result = module.refresh_resources(root, version, sources)
                raw = (root / f'content/docs/{version}.json').read_bytes()
                document = json.loads(raw)
                self.assertEqual(document['pages'], original['pages'] + [blueprint['pages'][-1]])
                self.assertEqual(len(document['pages']), 8)
                self.assertEqual(document, {**original, 'pages': document['pages'],
                    'lastUpdated': datetime.now(timezone.utc).date().isoformat()})
                self.assertEqual(json.loads((root / f'automation/evidence/{version}.json').read_bytes()), existing + [blueprint['evidence'][0]])
                updated = json.loads((root / 'public/docs-manifest.json').read_bytes())
                self.assertEqual(updated, {**manifest, 'contentHashes': {**manifest['contentHashes'], version: hashlib.sha256(raw).hexdigest()},
                    'pageRefresh': {'version': version, 'page': 'resources', 'sourceCommit': original['version']['commit']}})
                changed = {name for name, value in self.snapshot(root).items() if before.get(name) != value}
                self.assertEqual(changed, {f'content/docs/{version}.json', f'automation/evidence/{version}.json', 'public/docs-manifest.json'})
                self.assertEqual(set(result['changedFiles']), changed)
                self.assertEqual(result['page'], f'{version}/resources')
                self.assertEqual(result['sourceCommit'], original['version']['commit'])
                self.assertEqual(self.snapshot(sources), frozen)

    def test_rejects_noncanonical_or_wrong_snapshot_links_before_any_output(self):
        module = self.module()
        commit = 'a' * 40
        canonical = f'https://github.com/pubky/paykit-rs/blob/{commit}/terms.txt'
        invalid = [canonical.replace(commit, 'b' * 40), canonical.replace(commit, 'main'),
            canonical.replace('pubky/paykit-rs', 'private/other'), canonical.replace('https:', 'http:'),
            'javascript:alert(1)', 'data:text/html,synthetic', '//github.com/pubky/paykit-rs',
            canonical.replace('github.com', 'user:secret@github.com'), canonical + '?token=synthetic',
            canonical.replace('/terms.txt', '/../terms.txt'), canonical.replace('/terms.txt', '/%74erms.txt'),
            canonical.replace('/terms.txt', '//terms.txt'), canonical + '\n', canonical + '#unsafe',
            '/docs/coming-soon/overview#fixture', '/docs/released/unknown',
            '/docs/released/overview#missing', '/docs/released/overview?unsafe=true']
        for target in ('section', 'sourceLinks'):
            for href in invalid:
                with self.subTest(target=target, href=href):
                    root, sources, _, blueprint, _, _ = self.fixture()
                    page = blueprint['pages'][-1]
                    links = page['sections'][0]['links'] if target == 'section' else page['sourceLinks']
                    links.append({'label': 'Synthetic invalid link', 'href': href})
                    (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                    before = self.snapshot(root)
                    with self.assertRaisesRegex(ValueError, '[Rr]esources.*link'):
                        module.refresh_resources(root, 'released', sources)
                    self.assertEqual(self.snapshot(root), before)

    def test_requires_indexed_public_source_bytes_for_every_primary_link_and_evidence(self):
        module = self.module()
        for mutation in ('not-public', 'missing-file', 'unlisted-file', 'hash', 'bytes', 'url', 'duplicate-path', 'evidence-unlisted'):
            with self.subTest(mutation=mutation):
                root, sources, _, blueprint, _, _ = self.fixture()
                index = json.loads((sources / 'index.json').read_bytes())
                if mutation == 'not-public':
                    index['publicOnly'] = False
                elif mutation == 'missing-file':
                    (sources / 'terms.txt').unlink()
                elif mutation == 'unlisted-file':
                    index['files'] = []
                elif mutation == 'duplicate-path':
                    index['files'].append(copy.deepcopy(index['files'][0]))
                elif mutation == 'evidence-unlisted':
                    (sources / 'extra.txt').write_bytes((sources / 'terms.txt').read_bytes())
                    blueprint['evidence'][0]['path'] = 'extra.txt'
                else:
                    index['files'][0][{'hash': 'sha256', 'bytes': 'bytes', 'url': 'url'}[mutation]] = {
                        'hash': '0' * 64, 'bytes': 0, 'url': 'https://private.invalid/file'}[mutation]
                (sources / 'index.json').write_bytes(encode(index))
                (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Ss]ource|[Ee]vidence'):
                    module.refresh_resources(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_requires_resources_title_prose_and_primary_link_in_every_section(self):
        module = self.module()
        for mutation in ('title', 'empty-paragraphs', 'blank-paragraph', 'missing-links', 'internal-only', 'blank-label'):
            with self.subTest(mutation=mutation):
                root, sources, _, blueprint, _, _ = self.fixture()
                page = blueprint['pages'][-1]
                section = page['sections'][0]
                if mutation == 'title':
                    page['title'] = 'Not Resources'
                elif mutation == 'empty-paragraphs':
                    section['paragraphs'] = []
                elif mutation == 'blank-paragraph':
                    section['paragraphs'] = [' ']
                elif mutation == 'missing-links':
                    section.pop('links')
                elif mutation == 'internal-only':
                    section['links'] = section['links'][1:]
                else:
                    section['links'][0]['label'] = ' '
                (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Rr]esources'):
                    module.refresh_resources(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_update_and_same_day_repeat_preserve_all_other_bytes_and_mtimes(self):
        module = self.module()
        root, sources, document, blueprint, _, _ = self.fixture()
        page = copy.deepcopy(blueprint['pages'][-1])
        page['description'] = 'Prior synthetic resource description.'
        document['pages'].append(page)
        target = root / 'content/docs/released.json'
        target.write_bytes(encode(document))
        module.refresh_resources(root, 'released', sources)
        self.assertEqual(json.loads(target.read_bytes())['pages'], document['pages'][:-1] + [blueprint['pages'][-1]])
        before = self.snapshot(root)
        self.assertEqual(module.refresh_resources(root, 'released', sources)['changedFiles'], [])
        self.assertEqual(self.snapshot(root), before)

    def test_utc_generation_day_is_persisted_not_build_or_release_time(self):
        from unittest.mock import patch
        import refresh_glossary
        module = self.module()
        root, sources, _, _, _, _ = self.fixture()
        case = self
        class Clock:
            day = '2026-02-03'
            @classmethod
            def now(cls, tz):
                case.assertIs(tz, timezone.utc)
                return datetime.fromisoformat(cls.day + 'T00:01:00+00:00')
        with patch.object(refresh_glossary, 'datetime', Clock):
            module.refresh_resources(root, 'released', sources)
            first = json.loads((root / 'content/docs/released.json').read_bytes())
            self.assertEqual(first['lastUpdated'], Clock.day)
            Clock.day = '2026-02-04'
            result = module.refresh_resources(root, 'released', sources)
            self.assertEqual(json.loads((root / 'content/docs/released.json').read_bytes()), {**first, 'lastUpdated': Clock.day})
            self.assertEqual(set(result['changedFiles']), {'content/docs/released.json', 'public/docs-manifest.json'})

    def test_rejects_source_mismatches_and_inexact_evidence_without_output_writes(self):
        module = self.module()
        mutations = [('sourceCommit', 'b' * 40), ('index-commit', 'b' * 40), ('index-repository', 'private/other'),
            ('sourceHash', '0' * 64), ('excerpt', 'Wrong synthetic excerpt.'), ('startLine', True),
            ('startLine', 0), ('endLine', 99), ('version', 'coming-soon'), ('commit', 'b' * 40),
            ('path', '../terms.txt'), ('path', '/terms.txt'), ('path', 'missing.txt'), ('raw', None),
            ('missing-mapping', None), ('unknown-mapping', None), ('duplicate-id', None), ('shared-conflict', None)]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                root, sources, _, blueprint, existing, _ = self.fixture()
                if field.startswith('index-'):
                    index = json.loads((sources / 'index.json').read_bytes())
                    index[field[6:]] = value
                    (sources / 'index.json').write_bytes(encode(index))
                elif field == 'sourceCommit':
                    blueprint[field] = value
                elif field == 'raw':
                    (sources / 'terms.txt').write_bytes(b'Changed synthetic file.\n')
                elif field == 'missing-mapping':
                    blueprint['pages'][-1]['sections'][0].pop('evidence')
                elif field == 'unknown-mapping':
                    blueprint['pages'][-1]['sections'][0]['evidence'] = ['missing']
                elif field == 'duplicate-id':
                    blueprint['evidence'].append(copy.deepcopy(blueprint['evidence'][0]))
                elif field == 'shared-conflict':
                    (root / 'automation/evidence/released.json').write_bytes(encode(existing + [{**blueprint['evidence'][0], 'claim': 'Conflict'}]))
                else:
                    blueprint['evidence'][0][field] = value
                (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Ss]ource|[Ee]vidence'):
                    module.refresh_resources(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_unique_reference_sections_and_page_are_required(self):
        module = self.module()
        for mutation in ('missing-page', 'duplicate-page', 'group', 'kind', 'sections', 'duplicate-title', 'duplicate-anchor', 'unsafe-anchor', 'collision', 'existing-duplicate'):
            with self.subTest(mutation=mutation):
                root, sources, document, blueprint, _, _ = self.fixture()
                page = blueprint['pages'][-1]
                if mutation == 'missing-page':
                    blueprint['pages'].pop()
                elif mutation == 'duplicate-page':
                    blueprint['pages'].append(copy.deepcopy(page))
                elif mutation in ('group', 'kind'):
                    page[mutation] = 'invalid'
                elif mutation == 'sections':
                    page['sections'] = []
                elif mutation == 'duplicate-title':
                    page['sections'][1]['title'] = page['sections'][0]['title'].upper()
                elif mutation == 'duplicate-anchor':
                    page['sections'][1]['id'] = page['sections'][0]['id']
                elif mutation == 'unsafe-anchor':
                    page['sections'][0]['id'] = '../unsafe'
                elif mutation == 'collision':
                    page['sections'][0]['id'] = 'docs-title'
                else:
                    document['pages'].extend([copy.deepcopy(page), copy.deepcopy(page)])
                    (root / 'content/docs/released.json').write_bytes(encode(document))
                (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Rr]esources'):
                    module.refresh_resources(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_cli_uses_the_shared_renderer_and_requires_version_absolute_sources(self):
        root, sources, _, _, _, _ = self.fixture()
        for script in (SCRIPT, SCRIPT.with_name('refresh_glossary.py')):
            (root / 'automation' / script.name).write_bytes(script.read_bytes())
        command = [sys.executable, '-B', str(root / 'automation/refresh_resources.py')]
        result = subprocess.run(command + ['--version', 'released', '--sources', str(sources)], capture_output=True, text=True, cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['page'], 'released/resources')
        self.assertEqual(json.loads((root / 'content/docs/released.json').read_bytes())['pages'][-1]['slug'], 'resources')
        for args in ([], ['--version', 'released'], ['--version', 'unknown', '--sources', str(sources)],
                     ['--version', 'released', '--sources', '../frozen']):
            before = self.snapshot(root)
            self.assertNotEqual(subprocess.run(command + args, capture_output=True, cwd=root).returncode, 0)
            self.assertEqual(self.snapshot(root), before)

    def full_fixture(self):
        import test_refresh_development as development_fixtures
        owner = development_fixtures.DevelopmentRefreshTests()
        self.addCleanup(owner.doCleanups)
        root, sources, module = owner.fixture()
        index = json.loads((sources / 'index.json').read_bytes())
        raw = (sources / 'README.md').read_bytes()
        index.update(publicOnly=True, files=[{'path': 'README.md',
            'url': f'https://raw.githubusercontent.com/pubky/paykit-rs/{index["commit"]}/README.md',
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}])
        (sources / 'index.json').write_bytes(encode(index))
        for name in ('automation/blueprints/coming-soon.json', 'content/docs/coming-soon.json'):
            target = root / name
            data = json.loads(target.read_bytes())
            data['pages'].extend({'slug': slug, 'title': slug.capitalize(), 'group': 'Reference', 'kind': 'reference',
                'sourceLinks': [], 'sections': [{'id': 'synthetic', 'title': 'Synthetic section', 'evidence': ['fixture'],
                    'paragraphs': ['Synthetic reference fixture.'], 'links': [{'label': 'Synthetic primary file',
                    'href': f'https://github.com/pubky/paykit-rs/blob/{index["commit"]}/README.md'}]}]}
                for slug in ('glossary', 'resources'))
            target.write_bytes(encode(data))
        return root, sources, module

    def test_full_selected_refresh_keeps_all_eight_pages_after_bootstrap(self):
        import test_refresh_development as development_fixtures
        root, sources, module = self.full_fixture()
        module.refresh_development(root, sources)
        expected = [*development_fixtures.SLUGS, 'glossary', 'resources']
        self.assertEqual([p['slug'] for p in json.loads((root / 'content/docs/coming-soon.json').read_bytes())['pages']], expected)
        self.assertEqual(json.loads((root / 'public/docs-manifest.json').read_bytes())['versionRefresh']['pages'], expected)
        self.assertEqual(module.refresh_development(root, sources)['changedFiles'], [])

    def test_full_refresh_rejects_wrong_resource_snapshot_before_any_outputs(self):
        root, sources, module = self.full_fixture()
        target = root / 'automation/blueprints/coming-soon.json'
        blueprint = json.loads(target.read_bytes())
        blueprint['pages'][-1]['sections'][0]['links'][0]['href'] = 'https://github.com/pubky/paykit-rs/blob/' + 'a' * 40 + '/README.md'
        target.write_bytes(encode(blueprint))
        before = self.snapshot(root)
        with self.assertRaisesRegex(ValueError, 'Resources.*link'):
            module.refresh_development(root, sources)
        self.assertEqual(self.snapshot(root), before)

    def test_rendered_source_links_cannot_be_shadowed_by_an_unused_page_links_field(self):
        module = self.module()
        root, sources, _, blueprint, _, _ = self.fixture()
        page = blueprint['pages'][-1]
        page['links'] = []  # Not rendered by DocsPage; must not hide sourceLinks.
        page['sourceLinks'][0]['href'] = 'javascript:alert(1)'
        (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
        before = self.snapshot(root)
        with self.assertRaisesRegex(ValueError, 'Resources.*link'):
            module.refresh_resources(root, 'released', sources)
        self.assertEqual(self.snapshot(root), before)


if __name__ == '__main__':
    unittest.main()
