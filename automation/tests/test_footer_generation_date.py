"""Generation-date regressions; all writes stay in temporary fixtures."""
from datetime import datetime, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

import test_refresh_page as page_fixtures
import test_refresh_development as development_fixtures
import test_check_source as source_fixtures


class FrozenClock(datetime):
    instant = datetime(2026, 11, 20, 0, 5, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is not timezone.utc:
            raise AssertionError('Generation dates must use UTC explicitly')
        return cls.instant.astimezone(tz)


class FooterGenerationDateTests(unittest.TestCase):
    def test_source_checks_never_stamp_dates_even_when_force_requests_generation(self):
        root, module = source_fixtures.CheckSourceTests.fixture(self)
        for version in ('released', 'coming-soon'):
            path = root / f'content/docs/{version}.json'
            document = json.loads(path.read_bytes())
            document['lastUpdated'] = '2026-11-18'
            path.write_text(json.dumps(document))
        before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
        for version in ('released', 'coming-soon'):
            for force in (False, True):
                result = module.check_source(root, version, 'overview', force=force, resolver=lambda *args: source_fixtures.OLD)
                self.assertEqual(result['action'], 'refresh_needed' if force else 'skip')
        self.assertEqual(before, {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})

    def test_explicit_development_generation_stamps_date_without_source_or_page_churn(self):
        root, sources, module = development_fixtures.DevelopmentRefreshTests.fixture(self)
        with patch.object(module, 'datetime', FrozenClock, create=True):
            module.refresh_development(root, sources)
            document_path = root / 'content/docs/coming-soon.json'
            first = json.loads(document_path.read_bytes())
            self.assertEqual(first.get('lastUpdated'), '2026-11-20')
            before = {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
            self.assertEqual(module.refresh_development(root, sources)['changedFiles'], [])
            self.assertEqual(before, {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})
            with patch.object(FrozenClock, 'instant', datetime(2026, 11, 21, 0, 1, tzinfo=timezone.utc)):
                result = module.refresh_development(root, sources)
            second = json.loads(document_path.read_bytes())
            self.assertEqual(second.pop('lastUpdated'), '2026-11-21')
            first.pop('lastUpdated')
            self.assertEqual(second, first)
            self.assertEqual(result['changedFiles'], ['content/docs/coming-soon.json', 'public/docs-manifest.json'])
            for name, state in before.items():
                if name not in result['changedFiles']:
                    self.assertEqual(((root / name).read_bytes(), (root / name).stat().st_mtime_ns), state, name)
            manifest = json.loads((root / 'public/docs-manifest.json').read_bytes())
            self.assertEqual(manifest['contentHashes']['coming-soon'], hashlib.sha256(document_path.read_bytes()).hexdigest())

    def test_explicit_page_generation_stamps_utc_day_even_with_unchanged_inputs(self):
        root, module, paths = page_fixtures.RefreshPageTests._existing_fixture(self)
        with patch.object(module, 'datetime', FrozenClock, create=True):
            module.refresh_page(root, 'released', 'overview')
            document_path = root / 'content/docs/released.json'
            first = json.loads(document_path.read_bytes())
            self.assertEqual(first.get('lastUpdated'), '2026-11-20')
            before = {name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) for name in paths}
            self.assertEqual(module.refresh_page(root, 'released', 'overview')['changedFiles'], [])
            self.assertEqual(before, {name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) for name in paths})
            with patch.object(FrozenClock, 'instant', datetime(2026, 11, 21, 0, 1, tzinfo=timezone.utc)):
                result = module.refresh_page(root, 'released', 'overview')
            second = json.loads(document_path.read_bytes())
            self.assertEqual(second.pop('lastUpdated'), '2026-11-21')
            first.pop('lastUpdated')
            self.assertEqual(second, first, 'Date-only refresh preserves source/version and every page')
            self.assertEqual(result['changedFiles'], ['content/docs/released.json', 'public/docs-manifest.json'])
            manifest = json.loads((root / 'public/docs-manifest.json').read_bytes())
            self.assertEqual(manifest['contentHashes']['released'], hashlib.sha256(document_path.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
