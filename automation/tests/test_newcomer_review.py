"""Synthetic HTTP/report controls only; not an independent newcomer audit."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).parents[1] / 'newcomer_review.py'


class NewcomerReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.document = self.root / 'document.json'
        self.doc = {'version': {'label': 'synthetic-tag', 'commit': 'a' * 40,
                               'sourceUrl': 'https://github.com/pubky/paykit-rs/tree/' + 'a' * 40},
                    'lastUpdated': '2026-11-20', 'pages': [{'slug': 'overview'}, {'slug': 'wallets'}]}
        self.document.write_text(json.dumps(self.doc))
        self.routes = ['/docs', '/docs/coming-soon/overview', '/docs/coming-soon/wallets']
        self.bodies = {route + suffix: ((b'<html>Synthetic reader page</html>\n' if not suffix
                                       else b'# Synthetic reader page\n') + route.encode())
                       for route in self.routes for suffix in ('', '/index.md')}

    @contextlib.contextmanager
    def server(self, redirect=None, status=200):
        bodies = self.bodies
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                body = bodies.get(self.path, b'missing')
                self.send_response(302 if redirect else status if self.path in bodies else 404)
                if redirect:
                    self.send_header('Location', redirect)
                self.send_header('Content-Type', 'text/markdown' if self.path.endswith('.md') else 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield 'http://127.0.0.1:' + str(server.server_port), requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def cli(self, *args, ok=True):
        self.assertTrue(SCRIPT.is_file(), 'Dedicated newcomer helper is missing')
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['action'], 'captured' if args[0] == 'capture' and ok
                         else 'pass' if ok else 'refused')
        return payload

    def capture(self, base, output=None, ok=True):
        return self.cli('capture', '--document', self.document, '--version', 'coming-soon',
                        '--base-url', base, '--output', output or self.root / 'bundle', ok=ok)

    def test_capture_cli_preserves_all_actual_bodies_and_metadata(self):
        with self.server() as (base, requests):
            result = self.capture(base)
        index = self.root / 'bundle/index.json'
        manifest = json.loads(index.read_bytes())
        self.assertEqual(result['inputManifestSha256'], hashlib.sha256(index.read_bytes()).hexdigest())
        self.assertEqual(manifest['kind'], 'rendered-public-reader-input')
        self.assertEqual(manifest['versionMetadata'], self.doc['version'])
        self.assertEqual(manifest['lastUpdated'], self.doc['lastUpdated'])
        self.assertEqual(manifest['documentSha256'], hashlib.sha256(self.document.read_bytes()).hexdigest())
        self.assertEqual(manifest['entryUrl'], base + '/docs')
        self.assertEqual(manifest['pageRoutes'], self.routes[1:])
        self.assertEqual([row['route'] for row in manifest['documents']], self.routes)
        self.assertCountEqual(requests, self.bodies)
        for i, row in enumerate(manifest['documents']):
            self.assertEqual(row['url'], base + row['route'])
            for kind, suffix in [('html', ''), ('md', '/index.md')]:
                binding = row[kind]
                path = self.root / 'bundle' / binding['path']
                self.assertEqual(path.name, f'{i:02d}.{kind}')
                body = path.read_bytes()
                self.assertEqual(body, self.bodies[row['route'] + suffix])
                self.assertEqual(binding['bytes'], len(body))
                self.assertEqual(binding['sha256'], hashlib.sha256(body).hexdigest())


    def synthetic_report(self, bundle):
        # Fabricated laboratory fixture, never represented as a real reader verdict.
        index = bundle / 'index.json'
        return {'perspective': 'newcomer', 'execution': 'reading-only',
                'reviewerTaskId': 'synthetic-reader-task',
                'inputManifestSha256': hashlib.sha256(index.read_bytes()).hexdigest(),
                'checkedRoutes': self.routes, 'passed': True,
                'summary': 'Synthetic reader identified the two documented paths and their next actions.',
                'journeys': [{'goal': 'Choose a synthetic wallet path',
                              'path': [json.loads(index.read_bytes())['entryUrl']],
                              'observations': ['The entry links to the synthetic wallet guide.']}],
                'findings': [], 'unansweredQuestions': [],
                'limits': ['Synthetic reading only; no guide commands or SDK executed.']}

    def validate(self, bundle, report=None, ok=True):
        path = self.root / 'report.json'
        if report is not None:
            path.write_text(json.dumps(report))
        return self.cli('validate', '--inputs', bundle / 'index.json', '--report', path,
                        '--document', self.document, '--author-task-id', 'synthetic-author-task',
                        '--factual-reviewer-task-id', 'synthetic-factual-task', ok=ok)

    def fixture_bundle(self):
        with self.server() as (base, _):
            self.capture(base)
        return self.root / 'bundle'

    def editorial_cli(self, bundle, report, ok=True):
        path = self.root / 'editorial.json'
        if report is not None:
            path.write_text(json.dumps(report))
        return self.cli('validate-editorial', '--inputs', bundle / 'index.json',
                        '--report', path, '--newcomer-report', self.root / 'report.json', '--document', self.document,
                        '--author-task-id', 'synthetic-author-task',
                        '--factual-reviewer-task-id', 'synthetic-factual-task',
                        '--newcomer-task-id', 'synthetic-reader-task',
                        '--editorial-task-id', 'synthetic-editor-task', ok=ok)

    def test_editorial_cli_binds_complete_independent_reading_report(self):
        bundle = self.fixture_bundle()
        index = bundle / 'index.json'
        entry = json.loads(index.read_bytes())['entryUrl']
        report = {'perspective': 'editorial', 'execution': 'reading-only',
                  'reviewerTaskId': 'synthetic-editor-task',
                  'inputManifestSha256': hashlib.sha256(index.read_bytes()).hexdigest(),
                  'checkedRoutes': self.routes, 'passed': True,
                  'summary': 'Synthetic review of wording, steps and terminology.',
                  'findings': [{'url': entry, 'location': 'Synthetic entry',
                                'evidence': 'Synthetic reader page',
                                'impact': 'A shorter label might help scanning.',
                                'recommendation': 'Consider shortening the synthetic label.',
                                'blocking': False}]}
        before = {p: p.read_bytes() for p in bundle.iterdir()}
        self.assertIn('Missing regular newcomer report', self.editorial_cli(bundle, None, ok=False)['detail'])
        self.validate(bundle, self.synthetic_report(bundle))
        self.assertIn('Missing regular editorial report', self.editorial_cli(bundle, None, ok=False)['detail'])
        newcomer_path = self.root / 'report.json'
        newcomer_path.write_text(json.dumps({**self.synthetic_report(bundle), 'passed': False}))
        self.assertIn('Newcomer report did not pass', self.editorial_cli(bundle, report, ok=False)['detail'])
        newcomer_path.write_text(json.dumps({**self.synthetic_report(bundle), 'inputManifestSha256': '1' * 64}))
        self.assertIn('Stale input manifest hash', self.editorial_cli(bundle, report, ok=False)['detail'])
        self.validate(bundle, self.synthetic_report(bundle))
        result = self.editorial_cli(bundle, report)
        self.assertEqual(result['reviewerTaskId'], report['reviewerTaskId'])
        self.assertEqual(result['newcomerTaskId'], 'synthetic-reader-task')
        self.assertEqual(result['newcomerReportSha256'], hashlib.sha256(newcomer_path.read_bytes()).hexdigest())
        self.assertEqual(result['authorTaskId'], 'synthetic-author-task')
        self.assertEqual(result['factualReviewerTaskId'], 'synthetic-factual-task')
        self.assertEqual(result['nonblockingFindings'], report['findings'])
        self.assertEqual({p: p.read_bytes() for p in bundle.iterdir()}, before)

        for key, value in (('perspective', 'newcomer'), ('execution', 'hands-on'),
                           ('reviewerTaskId', 'synthetic-reader-task'),
                           ('reviewerTaskId', 'unverified-editor'),
                           ('inputManifestSha256', '1' * 64),
                           ('checkedRoutes', self.routes[:-1]),
                           ('checkedRoutes', [*self.routes, self.routes[0]]),
                           ('passed', 1), ('passed', False), ('summary', 'pass'),
                           ('findings', None)):
            with self.subTest(field=key, value=value):
                self.editorial_cli(bundle, {**report, key: value}, ok=False)

        for key, value in (('url', 'https://example.invalid/'), ('evidence', ''),
                           ('recommendation', ''), ('blocking', True), ('blocking', 0)):
            with self.subTest(finding=key, value=value):
                self.editorial_cli(bundle, {**report, 'findings': [
                    {**report['findings'][0], key: value}]}, ok=False)

        path = bundle / '01.md'
        path.write_bytes(path.read_bytes() + b'Altered after the review')
        result = self.editorial_cli(bundle, report, ok=False)
        self.assertIn('Reader body length/hash mismatch', result['detail'])
        path.write_bytes(before[path])

        legacy = json.loads(index.read_bytes())
        legacy.pop('documentSha256')
        index.write_text(json.dumps(legacy))
        result = self.editorial_cli(bundle, {**report,
            'inputManifestSha256': hashlib.sha256(index.read_bytes()).hexdigest()}, ok=False)
        self.assertIn('document hash', result['detail'])

    def test_editorial_gate_refuses_an_actual_newcomer_id_hidden_by_the_cli_label(self):
        bundle = self.fixture_bundle()
        newcomer = {**self.synthetic_report(bundle), 'reviewerTaskId': 'synthetic-editor-task'}
        self.assertEqual(self.validate(bundle, newcomer)['reviewerTaskId'], 'synthetic-editor-task')
        editorial = {'perspective': 'editorial', 'execution': 'reading-only',
                     'reviewerTaskId': 'synthetic-editor-task',
                     'inputManifestSha256': newcomer['inputManifestSha256'],
                     'checkedRoutes': self.routes, 'passed': True,
                     'summary': 'Synthetic review of the exact rendered views.', 'findings': []}
        self.assertIn('Wrong newcomer task ID', self.editorial_cli(bundle, editorial, ok=False)['detail'])

    def test_security_privacy_gate_binds_all_reports_and_refuses_wrong_inputs(self):
        bundle = self.fixture_bundle()
        newcomer = self.synthetic_report(bundle)
        self.validate(bundle, newcomer)
        editor = {**{key: newcomer[key] for key in ('execution', 'inputManifestSha256', 'checkedRoutes', 'passed')},
                  'perspective': 'editorial', 'reviewerTaskId': 'synthetic-editor-task',
                  'summary': 'Synthetic editor inspected clarity.', 'findings': []}
        self.editorial_cli(bundle, editor)
        topics = ['secrets', 'identifiers', 'diagnostics', 'data-boundaries']
        finding = {'url': json.loads((bundle / 'index.json').read_bytes())['entryUrl'],
                   'location': 'Synthetic entry, Markdown', 'evidence': 'Synthetic reader page',
                   'impact': 'The short synthetic label lacks context.',
                   'recommendation': 'Clarify the synthetic label.', 'blocking': False}
        report = {**editor, 'perspective': 'security-privacy', 'reviewerTaskId': 'synthetic-security-task',
                  'checkedTopics': topics, 'summary': 'Synthetic privacy and security reading.',
                  'limits': 'No product behavior was exercised.', 'findings': [finding]}
        report_path = self.root / 'security-privacy.json'
        editor_path = self.root / 'editorial.json'
        newcomer_path = self.root / 'report.json'

        def check(ok=True, reviewer=report['reviewerTaskId']):
            return self.cli('validate-security-privacy', '--inputs', bundle / 'index.json',
                            '--report', report_path, '--document', self.document,
                            '--newcomer-report', newcomer_path, '--editorial-report', editor_path,
                            '--author-task-id', 'synthetic-author-task',
                            '--factual-reviewer-task-id', 'synthetic-factual-task',
                            '--newcomer-task-id', newcomer['reviewerTaskId'],
                            '--editorial-task-id', editor['reviewerTaskId'],
                            '--security-privacy-task-id', reviewer, ok=ok)

        self.assertIn('Missing regular security-privacy report', check(ok=False)['detail'])
        report_path.write_text(json.dumps(report))
        paths = [*bundle.iterdir(), report_path, editor_path, newcomer_path, self.document]
        original = {path: path.read_bytes() for path in paths}
        result = check()
        self.assertEqual(result['checkedTopics'], topics)
        self.assertEqual(result['checkedRoutes'], self.routes)
        self.assertEqual(result['nonblockingFindings'], [finding])
        for field, identity in [('reviewerTaskId', report['reviewerTaskId']),
                                ('editorialTaskId', editor['reviewerTaskId']),
                                ('newcomerTaskId', newcomer['reviewerTaskId']),
                                ('authorTaskId', 'synthetic-author-task'),
                                ('factualReviewerTaskId', 'synthetic-factual-task')]:
            self.assertEqual(result[field], identity)
            if field != 'reviewerTaskId':
                self.assertIn('Distinct actual security/privacy', check(ok=False, reviewer=identity)['detail'])
        for field, path in [('securityPrivacyReportSha256', report_path),
                            ('editorialReportSha256', editor_path), ('newcomerReportSha256', newcomer_path)]:
            self.assertEqual(result[field], hashlib.sha256(original[path]).hexdigest())
        self.assertEqual({path: path.read_bytes() for path in paths}, original)

        for field, value in [('execution', 'hands-on'), ('perspective', 'editorial'), ('passed', 1),
                             ('passed', False), ('checkedTopics', topics[:-1]),
                             ('checkedTopics', [*topics, topics[0]]), ('checkedRoutes', self.routes[:-1]),
                             ('limits', 'unknown'), ('reviewerTaskId', editor['reviewerTaskId']),
                             ('inputManifestSha256', '0' * 64),
                             ('findings', [{**finding, 'blocking': True}]),
                             ('findings', [{**finding, 'publicSource': ''}]),
                             ('findings', [{**finding, 'url': 'https://example.invalid/'}])]:
            with self.subTest(field=field, value=value):
                report_path.write_text(json.dumps({**report, field: value}))
                check(ok=False)
        report_path.write_bytes(original[report_path])

        for path, mutation, detail in [
                (editor_path, {**editor, 'passed': False}, 'Editorial review did not pass'),
                (editor_path, {**editor, 'reviewerTaskId': report['reviewerTaskId']}, 'Wrong editorial task ID'),
                (newcomer_path, {**newcomer, 'passed': False}, 'Newcomer report did not pass'),
                (newcomer_path, {**newcomer, 'reviewerTaskId': report['reviewerTaskId']}, 'Wrong newcomer task ID')]:
            with self.subTest(prior_report=path.name, detail=detail):
                path.write_text(json.dumps(mutation))
                self.assertIn(detail, check(ok=False)['detail'])
                path.write_bytes(original[path])

        for path, detail in [(self.document, 'Missing or stale document hash'),
                             (bundle / '01.md', 'Reader body length/hash mismatch')]:
            path.write_bytes(original[path] + b' ')
            self.assertIn(detail, check(ok=False)['detail'])
            path.write_bytes(original[path])

    def rewrite_index(self, bundle, manifest):
        (bundle / 'index.json').write_text(json.dumps(manifest))
        return self.synthetic_report(bundle)

    def test_validation_cli_accepts_current_bound_reading_report_without_writes(self):
        bundle = self.fixture_bundle()
        report = self.synthetic_report(bundle)
        path = self.root / 'report.json'
        path.write_text(json.dumps(report))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.validate(bundle)
        self.assertEqual(result['reviewerTaskId'], report['reviewerTaskId'])
        self.assertEqual(result['nonblockingFindings'], [])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(set(p for p in self.root.rglob('*') if p.is_file()), set(before))

    def test_initial_schema_absolute_body_paths_without_document_hash_is_compatible(self):
        bundle = self.fixture_bundle()
        manifest = json.loads((bundle / 'index.json').read_bytes())
        manifest.pop('documentSha256')
        manifest['baselineCommit'] = 'b' * 40
        manifest['manifestSha256'] = 'c' * 64  # Historical optional label, not raw index hash.
        report = self.rewrite_index(bundle, manifest)
        self.validate(bundle, report)
        manifest['versionMetadata']['commit'] = 'd' * 40
        self.validate(bundle, self.rewrite_index(bundle, manifest), ok=False)

    def test_nonblocking_suggestions_are_logged_not_promoted_to_requirements(self):
        bundle = self.fixture_bundle()
        report = self.synthetic_report(bundle)
        report['findings'] = [{'id': 'synthetic-preference', 'severity': 'suggestion', 'blocking': False,
                               'url': json.loads((bundle / 'index.json').read_bytes())['entryUrl'],
                               'location': 'Synthetic entry paragraph', 'evidence': 'Synthetic reader page',
                               'readerQuestion': 'Could this synthetic label be shorter?',
                               'impact': 'Synthetic minor preference, not a missing prerequisite.',
                               'recommendation': 'Consider a shorter source-supported label.'}]
        result = self.validate(bundle, report)
        self.assertEqual(result['nonblockingFindings'], report['findings'])

    def test_missing_failed_stale_incomplete_and_malformed_reports_refuse_immutably(self):
        import copy
        bundle = self.fixture_bundle()
        valid = self.synthetic_report(bundle)
        changes = [('perspective', 'factual'), ('execution', 'sdk-execution'),
                   ('reviewerTaskId', ''), ('reviewerTaskId', 'synthetic-author-task'),
                   ('reviewerTaskId', 'synthetic-factual-task'), ('inputManifestSha256', '0' * 64),
                   ('checkedRoutes', self.routes[:-1]), ('checkedRoutes', self.routes + [self.routes[0]]),
                   ('checkedRoutes', self.routes + ['/docs/coming-soon/unknown']),
                   ('passed', False), ('passed', 'true'), ('passed', 1),
                   ('summary', ''), ('summary', 'unknown'), ('journeys', []), ('journeys', {}),
                   ('journeys', ['unknown']), ('findings', {}), ('unansweredQuestions', None),
                   ('limits', [])]
        for field in valid:
            candidate = copy.deepcopy(valid)
            del candidate[field]
            changes.append(('__report__', candidate))
        changes += [('__report__', None), ('__report__', []), ('__report__', {})]
        for key, value in changes:
            with self.subTest(field=key, value=value):
                report = copy.deepcopy(valid)
                if key == '__report__':
                    report = value
                else:
                    report[key] = value
                path = self.root / 'report.json'
                path.write_text(json.dumps(report))
                before = path.read_bytes()
                self.validate(bundle, ok=False)
                self.assertEqual(path.read_bytes(), before)
        path.write_text('{broken')
        self.validate(bundle, ok=False)
        path.unlink()
        self.validate(bundle, ok=False)

    def test_blocking_and_malformed_findings_refuse(self):
        import copy
        bundle = self.fixture_bundle()
        valid = self.synthetic_report(bundle)
        finding = {'id': 'synthetic-gap', 'severity': 'major', 'blocking': True,
                   'url': json.loads((bundle / 'index.json').read_bytes())['entryUrl'],
                   'location': 'Synthetic heading', 'evidence': 'Synthetic reader page',
                   'readerQuestion': 'What is the synthetic prerequisite?',
                   'impact': 'The synthetic journey cannot continue.',
                   'recommendation': 'Explain the prerequisite from public source evidence.'}
        cases = [finding, {**finding, 'severity': 'blocker', 'blocking': False},
                 {**finding, 'severity': 'unknown', 'blocking': False},
                 {**finding, 'blocking': 'false'}, {**finding, 'blocking': False, 'url': 'unknown'}]
        for key in finding:
            candidate = copy.deepcopy(finding)
            del candidate[key]
            cases.append(candidate)
        cases += ['unknown', None, {}]
        for case in cases:
            with self.subTest(finding=case):
                report = copy.deepcopy(valid)
                report['findings'] = [case]
                self.validate(bundle, report, ok=False)

    def test_body_tamper_and_escaping_or_symlink_paths_refuse(self):
        bundle = self.fixture_bundle()
        manifest = json.loads((bundle / 'index.json').read_bytes())
        body = bundle / '00.html'
        original = body.read_bytes()
        body.write_bytes(original + b'tamper')
        self.validate(bundle, self.synthetic_report(bundle), ok=False)
        body.write_bytes(original)
        outside = self.root / 'outside.html'
        outside.write_bytes(original)
        for name in (str(outside), '../outside.html'):
            with self.subTest(path=name):
                manifest['documents'][0]['html']['path'] = name
                self.validate(bundle, self.rewrite_index(bundle, manifest), ok=False)
        manifest['documents'][0]['html']['path'] = str(body)
        report = self.rewrite_index(bundle, manifest)
        body.unlink()
        body.symlink_to(outside)
        self.validate(bundle, report, ok=False)
        body.unlink()
        body.mkdir()
        self.validate(bundle, report, ok=False)
        body.rmdir()
        self.validate(bundle, report, ok=False)

    def test_stale_document_metadata_scope_and_body_bindings_refuse(self):
        import copy
        bundle = self.fixture_bundle()
        original = json.loads((bundle / 'index.json').read_bytes())
        changes = [('kind', 'unknown'), ('version', 'unknown'), ('lastUpdated', '2026-11-19'),
                   ('versionMetadata', {}), ('documentSha256', '0' * 64),
                   ('pageRoutes', self.routes[1:-1]), ('pageRoutes', self.routes[1:] * 2),
                   ('entryUrl', 'https://unknown.invalid/docs'), ('documents', [])]
        for key, value in changes:
            with self.subTest(field=key):
                manifest = copy.deepcopy(original)
                manifest[key] = value
                self.validate(bundle, self.rewrite_index(bundle, manifest), ok=False)
        for field, value in [('route', self.routes[1]), ('url', 'http://127.0.0.1:1/private'),
                             ('bytes', 1), ('bytes', True), ('sha256', '0' * 64), ('path', '')]:
            with self.subTest(binding=field):
                manifest = copy.deepcopy(original)
                row = manifest['documents'][0]
                (row if field in ('route', 'url') else row['html'])[field] = value
                self.validate(bundle, self.rewrite_index(bundle, manifest), ok=False)
        self.rewrite_index(bundle, original)
        report = self.synthetic_report(bundle)
        for key, value in [('lastUpdated', '2026-11-21'), ('pages', [{'slug': 'changed'}]),
                           ('version', {**self.doc['version'], 'commit': 'b' * 40})]:
            with self.subTest(document=key):
                doc = copy.deepcopy(self.doc)
                doc[key] = value
                self.document.write_text(json.dumps(doc))
                self.validate(bundle, report, ok=False)
        self.document.write_text(json.dumps(self.doc))
        self.validate(bundle, report)

    def test_capture_refuses_reuse_unapproved_origins_redirects_and_failed_bodies(self):
        with self.server() as (base, requests):
            self.capture(base)
            count = len(requests)
            self.capture(base, ok=False)
            self.assertEqual(len(requests), count)
        for base in ['https://unknown.invalid', 'http://example.invalid:3000',
                     'http://127.0.0.1:3000/private', 'http://user@127.0.0.1:3000',
                     'http://127.0.0.1:3000?query=1', 'http://localhost:3000']:
            with self.subTest(origin=base):
                result = self.capture(base, output=self.root / 'refused', ok=False)
                self.assertFalse((self.root / 'refused').exists())
        with self.server() as (other, other_requests):
            with self.server(redirect=other + '/docs') as (base, _):
                self.capture(base, output=self.root / 'redirect', ok=False)
            self.assertEqual(other_requests, [])
        with self.server(status=500) as (base, _):
            self.capture(base, output=self.root / 'failed', ok=False)
        self.assertFalse((self.root / 'failed/index.json').exists())

    def test_recorded_summary_outcome_and_question_objects_are_supported(self):
        bundle = self.fixture_bundle()
        report = self.synthetic_report(bundle)
        report['summary'] = {'outcome': 'Synthetic newcomer journey passed with a localized question.',
                             'blockingFindings': 0, 'minorFindings': 0,
                             'remainingWork': 'Synthetic contract only, not actual reading approval.'}
        report['unansweredQuestions'] = [{'findingId': 'synthetic-reference',
                                           'question': 'Which synthetic next action is intended?',
                                           'resolution': 'Synthetic author must verify public evidence.'}]
        self.validate(bundle, report)
        for summary in (False, None, {}, {'outcome': ''}, {'outcome': False}, {'outcome': 'unknown'}):
            with self.subTest(summary=summary):
                report['summary'] = summary
                self.validate(bundle, report, ok=False)
        report['summary'] = 'Synthetic reading journey has a meaningful outcome.'
        for questions in ([{}], [{'question': ''}], [False], ['unknown']):
            with self.subTest(questions=questions):
                report['unansweredQuestions'] = questions
                self.validate(bundle, report, ok=False)

    def test_malformed_journey_fields_and_supplied_role_ids_refuse(self):
        import copy
        bundle = self.fixture_bundle()
        valid = self.synthetic_report(bundle)
        for field, value in [('goal', ''), ('path', []), ('path', ['unknown']),
                             ('observations', []), ('observations', ['unknown']),
                             ('observations', False)]:
            with self.subTest(journey=field, value=value):
                report = copy.deepcopy(valid)
                report['journeys'][0][field] = value
                self.validate(bundle, report, ok=False)
        (self.root / 'report.json').write_text(json.dumps(valid))
        for author, factual in [('', 'synthetic-factual-task'), ('synthetic-author-task', ''),
                                ('synthetic-author-task', 'synthetic-author-task')]:
            with self.subTest(author=author, factual=factual):
                self.cli('validate', '--inputs', bundle / 'index.json',
                         '--report', self.root / 'report.json', '--document', self.document,
                         '--author-task-id', author, '--factual-reviewer-task-id', factual, ok=False)

    def test_duplicate_failed_report_keys_are_not_overwritten_into_pass(self):
        bundle = self.fixture_bundle()
        report = self.synthetic_report(bundle)
        raw = json.dumps(report).replace('"passed": true', '"passed": false, "passed": true')
        (self.root / 'report.json').write_text(raw)
        self.validate(bundle, ok=False)

if __name__ == '__main__':
    unittest.main()
