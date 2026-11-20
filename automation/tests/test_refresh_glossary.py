"""Synthetic fixtures only: exercise glossary generation, not Paykit facts."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

SCRIPT = Path(__file__).parents[1] / 'refresh_glossary.py'
CORE = ('overview', 'components', 'flows', 'boundaries', 'wallets', 'point-of-sale')


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


class RefreshGlossaryTests(unittest.TestCase):
    def fixture(self, version='released'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        root, sources = base / 'site', base / 'frozen'
        commit = 'a' * 40
        document = {'lastUpdated': '2000-01-01', 'version': {'commit': commit,
            'tag': 'synthetic-tag', 'label': 'Synthetic snapshot', 'packages': {'synthetic': 'fixture'},
            'readiness': {'notice': 'Synthetic qualification, not product advice.'}},
            'pages': [{'slug': slug, 'title': 'Synthetic ' + slug, 'sections': [
                {'id': 'fixture', 'title': 'Fixture', 'paragraphs': ['Untouched synthetic prose.'], 'evidence': ['core']}
            ]} for slug in CORE], 'extraMetadata': {'keep': True}}
        raw = b'Synthetic source fixture.\nAlpha fixture meaning.\nBeta fixture meaning.\n'
        row = {'id': 'glossary-alpha', 'version': version, 'commit': commit, 'path': 'terms.txt',
            'sourceHash': hashlib.sha256(raw).hexdigest(), 'startLine': 2, 'endLine': 3,
            'excerpt': 'Alpha fixture meaning.\nBeta fixture meaning.',
            'kind': 'implemented', 'claim': 'Synthetic meaning fixture only.'}
        page = {'slug': 'glossary', 'title': 'Synthetic glossary', 'description': 'Synthetic terms only.',
            'group': 'Reference', 'kind': 'reference', 'sections': [
                {'id': title.lower(), 'title': title, 'paragraphs': [title + ' synthetic meaning.'],
                 'evidence': [row['id']], 'links': [{'label': 'Related fixture guide', 'href': f'/docs/{version}/overview#fixture'}]}
                for title in ('Alpha', 'Beta')],
            'sourceLinks': [{'label': 'Fixture source', 'href': f'https://github.com/pubky/paykit-rs/blob/{commit}/terms.txt#L2-L3'}]}
        blueprint = {'sourceCommit': commit, 'pages': [copy.deepcopy(p) for p in document['pages']] + [page],
                     'evidence': [row, {**row, 'id': 'unused-synthetic-row'}]}
        existing = [{'id': 'core', 'synthetic': 'untouched evidence'}]
        manifest = {'fingerprint': 'previous-whole-generation', 'sources': {version: {'commit': commit}},
                    'contentHashes': {version: 'old', 'other': 'untouched'}, 'otherMetadata': ['untouched']}
        files = {f'content/docs/{version}.json': encode(document),
                 f'automation/blueprints/{version}.json': encode(blueprint),
                 f'automation/evidence/{version}.json': encode(existing), 'public/docs-manifest.json': encode(manifest),
                 'content/docs/other.json': b'Other snapshot bytes.\n',
                 'public/docs-assets/other/fixture.svg': b'<svg>synthetic</svg>',
                 'public/docs-examples/fixture.tar.gz': b'synthetic archive',
                 'automation/sources.lock.json': b'{"synthetic": "unchanged"}\n'}
        for name, data in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        sources.mkdir()
        (sources / 'index.json').write_bytes(encode({'repository': 'pubky/paykit-rs', 'commit': commit}))
        (sources / 'terms.txt').write_bytes(raw)
        return root, sources, document, blueprint, existing, manifest

    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'The glossary-only renderer is missing')
        spec = importlib.util.spec_from_file_location('refresh_glossary', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def test_adds_only_glossary_with_exact_referenced_evidence_and_preserves_core(self):
        module = self.module()
        for version in ('released', 'coming-soon'):
            with self.subTest(version=version):
                root, sources, original, blueprint, existing, manifest = self.fixture(version)
                before = self.snapshot(root)
                result = module.refresh_glossary(root, version, sources)
                document_bytes = (root / f'content/docs/{version}.json').read_bytes()
                document = json.loads(document_bytes)
                self.assertEqual(document['pages'], original['pages'] + [blueprint['pages'][-1]])
                self.assertEqual({k: v for k, v in document.items() if k not in ('pages', 'lastUpdated')},
                                 {k: v for k, v in original.items() if k not in ('pages', 'lastUpdated')})
                self.assertEqual(document['lastUpdated'], datetime.now(timezone.utc).date().isoformat())
                self.assertEqual(json.loads((root / f'automation/evidence/{version}.json').read_bytes()), existing + [blueprint['evidence'][0]])
                updated = json.loads((root / 'public/docs-manifest.json').read_bytes())
                self.assertEqual(updated['contentHashes'][version], hashlib.sha256(document_bytes).hexdigest())
                self.assertEqual(updated['pageRefresh'], {'version': version, 'page': 'glossary', 'sourceCommit': original['version']['commit']})
                self.assertEqual({k: v for k, v in updated.items() if k not in ('contentHashes', 'pageRefresh')},
                                 {k: v for k, v in manifest.items() if k != 'contentHashes'})
                changed = {name for name, data in self.snapshot(root).items() if before.get(name) != data}
                self.assertEqual(changed, {f'content/docs/{version}.json', f'automation/evidence/{version}.json', 'public/docs-manifest.json'})
                self.assertEqual(set(result['changedFiles']), changed)
                self.assertEqual(result['page'], f'{version}/glossary')
                self.assertEqual(result['sourceCommit'], original['version']['commit'])

    def test_replaces_existing_glossary_then_same_day_repeat_preserves_bytes_and_mtimes(self):
        module = self.module()
        root, sources, original, blueprint, _, _ = self.fixture()
        path = root / 'content/docs/released.json'
        original['pages'].append({**blueprint['pages'][-1], 'description': 'Prior synthetic description.'})
        path.write_bytes(encode(original))
        module.refresh_glossary(root, 'released', sources)
        self.assertEqual(json.loads(path.read_bytes())['pages'], original['pages'][:-1] + [blueprint['pages'][-1]])
        before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in root.rglob('*') if p.is_file()}
        self.assertEqual(module.refresh_glossary(root, 'released', sources)['changedFiles'], [])
        self.assertEqual(before, {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
                                 for p in root.rglob('*') if p.is_file()})

    def test_refuses_source_migration_before_any_writes(self):
        module = self.module()
        for target, field, value in [('blueprint', 'sourceCommit', 'b' * 40),
                                     ('index', 'commit', 'b' * 40),
                                     ('index', 'repository', 'synthetic/other')]:
            with self.subTest(target=target, field=field):
                root, sources, _, blueprint, _, _ = self.fixture()
                path = root / 'automation/blueprints/released.json' if target == 'blueprint' else sources / 'index.json'
                payload = json.loads(path.read_bytes())
                payload[field] = value
                path.write_bytes(encode(payload))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, 'source'):
                    module.refresh_glossary(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_refuses_shared_evidence_id_conflicts_before_writes(self):
        module = self.module()
        root, sources, _, blueprint, existing, _ = self.fixture()
        path = root / 'automation/evidence/released.json'
        path.write_bytes(encode(existing + [{**blueprint['evidence'][0], 'claim': 'Conflicting synthetic mapping.'}]))
        before = self.snapshot(root)
        with self.assertRaisesRegex(ValueError, 'new evidence ID'):
            module.refresh_glossary(root, 'released', sources)
        self.assertEqual(self.snapshot(root), before)

    def test_checks_referenced_evidence_against_frozen_file_hash_and_exact_line_span(self):
        module = self.module()
        mutations = [('sourceHash', '0' * 64), ('excerpt', 'Wrong synthetic excerpt.'),
                     ('startLine', 0), ('startLine', True), ('endLine', 9),
                     ('version', 'coming-soon'), ('commit', 'b' * 40),
                     ('path', '../terms.txt'), ('path', '/terms.txt'), ('path', 'missing.txt'),
                     ('raw-outside-excerpt', None)]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                root, sources, _, blueprint, _, _ = self.fixture()
                if field == 'raw-outside-excerpt':
                    (sources / 'terms.txt').write_bytes((sources / 'terms.txt').read_bytes().replace(b'Synthetic source', b'Modified source'))
                else:
                    blueprint['evidence'][0][field] = value
                    (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Ee]vidence'):
                    module.refresh_glossary(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_requires_one_alphabetical_glossary_with_unique_term_titles_and_anchors(self):
        module = self.module()
        for mutation in ('unordered', 'duplicate-title', 'duplicate-id', 'invalid-id', 'colliding-anchor',
                         'empty-title', 'empty-sections', 'wrong-group', 'wrong-kind', 'duplicate-page', 'missing-page'):
            with self.subTest(mutation=mutation):
                root, sources, _, blueprint, _, _ = self.fixture()
                page = blueprint['pages'][-1]
                if mutation == 'unordered':
                    page['sections'].reverse()
                elif mutation == 'duplicate-title':
                    page['sections'][1]['title'] = 'ALPHA'
                elif mutation == 'duplicate-id':
                    page['sections'][1]['id'] = 'alpha'
                elif mutation == 'invalid-id':
                    page['sections'][0]['id'] = '../alpha'
                elif mutation == 'colliding-anchor':
                    page['sections'][1]['id'] = 'alpha-title'
                elif mutation == 'empty-title':
                    page['sections'][0]['title'] = ' '
                elif mutation == 'empty-sections':
                    page['sections'] = []
                elif mutation == 'wrong-group':
                    page['group'] = 'Understand Paykit'
                elif mutation == 'wrong-kind':
                    page['kind'] = 'explanation'
                elif mutation == 'duplicate-page':
                    blueprint['pages'].append(copy.deepcopy(page))
                else:
                    blueprint['pages'].pop()
                (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Gg]lossary'):
                    module.refresh_glossary(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)

    def test_requires_unambiguous_evidence_for_every_term(self):
        module = self.module()
        for mutation in ('empty', 'missing', 'unknown', 'duplicate-blueprint-id', 'duplicate-ledger-id'):
            with self.subTest(mutation=mutation):
                root, sources, _, blueprint, existing, _ = self.fixture()
                section = blueprint['pages'][-1]['sections'][0]
                if mutation == 'empty':
                    section['evidence'] = []
                elif mutation == 'missing':
                    del section['evidence']
                elif mutation == 'unknown':
                    section['evidence'] = ['unmapped-synthetic-id']
                elif mutation == 'duplicate-blueprint-id':
                    blueprint['evidence'].append(copy.deepcopy(blueprint['evidence'][0]))
                else:
                    (root / 'automation/evidence/released.json').write_bytes(encode(existing + existing))
                (root / 'automation/blueprints/released.json').write_bytes(encode(blueprint))
                before = self.snapshot(root)
                with self.assertRaisesRegex(ValueError, '[Ee]vidence'):
                    module.refresh_glossary(root, 'released', sources)
                self.assertEqual(self.snapshot(root), before)


    def test_cli_imports_only_glossary_and_requires_version_and_absolute_frozen_sources(self):
        root, sources, original, blueprint, _, _ = self.fixture()
        script = root / 'automation/refresh_glossary.py'
        script.write_bytes(SCRIPT.read_bytes())
        command = [sys.executable, '-B', str(script)]
        result = subprocess.run(command + ['--version', 'released', '--sources', str(sources)],
                                capture_output=True, text=True, cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('{'), 'The CLI must actually invoke the glossary renderer')
        self.assertEqual(json.loads(result.stdout)['page'], 'released/glossary')
        self.assertEqual(json.loads((root / 'content/docs/released.json').read_bytes())['pages'],
                         original['pages'] + [blueprint['pages'][-1]])
        for args in [[], ['--version', 'released'], ['--version', 'unknown', '--sources', str(sources)],
                     ['--version', 'released', '--sources', '../frozen'],
                     ['--version', 'released', '--sources', str(sources), '--page', 'overview']]:
            with self.subTest(args=args):
                before = self.snapshot(root)
                refused = subprocess.run(command + args, capture_output=True, text=True, cwd=root)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(self.snapshot(root), before)

    def test_generation_uses_utc_clock_and_next_day_changes_only_date_and_content_hash(self):
        module = self.module()
        root, sources, _, _, _, _ = self.fixture()
        from unittest.mock import patch
        class Clock:
            day = '2026-02-03'
            @classmethod
            def now(cls, tz):
                self.assertIs(tz, timezone.utc)
                return datetime.fromisoformat(cls.day + 'T00:01:00+00:00')
        with patch.object(module, 'datetime', Clock):
            module.refresh_glossary(root, 'released', sources)
            first = json.loads((root / 'content/docs/released.json').read_bytes())
            self.assertEqual(first['lastUpdated'], Clock.day)
            before = self.snapshot(root)
            Clock.day = '2026-02-04'
            result = module.refresh_glossary(root, 'released', sources)
            second = json.loads((root / 'content/docs/released.json').read_bytes())
            self.assertEqual(second, {**first, 'lastUpdated': Clock.day})
            self.assertEqual(set(result['changedFiles']), {'content/docs/released.json', 'public/docs-manifest.json'})
            self.assertEqual({name for name, data in self.snapshot(root).items() if before[name] != data}, set(result['changedFiles']))

    def test_refuses_ambiguous_existing_glossary_without_touching_any_page(self):
        module = self.module()
        root, sources, document, blueprint, _, _ = self.fixture()
        document['pages'].extend([copy.deepcopy(blueprint['pages'][-1]), copy.deepcopy(blueprint['pages'][-1])])
        (root / 'content/docs/released.json').write_bytes(encode(document))
        before = self.snapshot(root)
        with self.assertRaisesRegex(ValueError, '[Gg]lossary'):
            module.refresh_glossary(root, 'released', sources)
        self.assertEqual(self.snapshot(root), before)


if __name__ == '__main__':
    unittest.main()
