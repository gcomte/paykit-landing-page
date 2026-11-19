"""Focused checks for the small manual page-generation command."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'refresh_page.py'

class RefreshPageTests(unittest.TestCase):
    def test_selected_page_changes_without_rewriting_other_pages_or_version(self):
        self.assertTrue(SCRIPT.is_file(), 'The manual one-page refresh command is missing')
        spec = importlib.util.spec_from_file_location('refresh_page', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('automation/blueprints', 'automation/evidence', 'content/docs', 'public'):
                (root / name).mkdir(parents=True)
            version = {'commit': 'a' * 40, 'label': 'Pinned release', 'readiness': {'notice': 'Experimental'}}
            before = {'version': version, 'pages': [{'slug': 'overview', 'sections': []}, {'slug': 'other', 'sections': [{'paragraphs': ['Untouched']}]}]}
            page = {'slug': 'overview', 'title': 'Clearer overview', 'sections': [{'id': 'purpose', 'paragraphs': ['Fresh generated prose'], 'evidence': ['source-1']} ]}
            evidence = {'id': 'source-1', 'version': 'released', 'commit': 'a' * 40, 'path': 'README.md'}
            inputs = {'sourceCommit': 'a' * 40, 'pages': [page], 'evidence': [evidence]}
            for path, value in [('content/docs/released.json', before), ('automation/blueprints/released.json', inputs), ('automation/evidence/released.json', []), ('public/docs-manifest.json', {'fingerprint': 'previous-full-generation', 'sources': {'released': {'commit': 'a' * 40}}, 'contentHashes': {'released': 'old'}})]:
                (root / path).write_text(json.dumps(value) + '\n')
            result = module.refresh_page(root, 'released', 'overview')
            after = json.loads((root / 'content/docs/released.json').read_text())
            self.assertEqual(after['version'], version)
            self.assertEqual(after['pages'], [page, before['pages'][1]])
            self.assertEqual(result['page'], 'released/overview')
            self.assertEqual(json.loads((root / 'automation/evidence/released.json').read_text()), [evidence])
            manifest = json.loads((root / 'public/docs-manifest.json').read_text())
            self.assertEqual(manifest['fingerprint'], 'previous-full-generation')
            self.assertEqual(manifest['pageRefresh']['sourceCommit'], 'a' * 40)
            self.assertEqual(manifest['pageRefresh']['page'], 'overview')

    def _existing_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        site = SCRIPT.parents[1]
        paths = ('content/docs/released.json', 'automation/blueprints/released.json',
                 'automation/evidence/released.json', 'public/docs-manifest.json')
        for name in paths:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((site / name).read_bytes())
        spec = importlib.util.spec_from_file_location('refresh_page', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return root, module, paths

    def test_a_second_refresh_does_not_rewrite_files(self):
        root, module, paths = self._existing_fixture()
        module.refresh_page(root, 'released', 'overview')
        before = {name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) for name in paths}
        self.assertEqual(module.refresh_page(root, 'released', 'overview')['changedFiles'], [])
        self.assertEqual(before, {name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) for name in paths})

    def test_a_different_document_source_refuses_before_writes(self):
        root, module, paths = self._existing_fixture()
        path = root / 'content/docs/released.json'
        value = json.loads(path.read_text())
        value['version']['commit'] = '0' * 40
        path.write_text(json.dumps(value) + '\n')
        before = {name: (root / name).read_bytes() for name in paths}
        with self.assertRaisesRegex(ValueError, 'source'):
            module.refresh_page(root, 'released', 'overview')
        self.assertEqual(before, {name: (root / name).read_bytes() for name in paths})

if __name__ == '__main__':
    unittest.main()
