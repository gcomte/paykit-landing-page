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
    def server(self, redirect=None, status=200, on_request=None):
        bodies = self.bodies
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                body = bodies.get(self.path, b'missing')
                if on_request:
                    on_request(self.path)
                self.send_response(302 if redirect else status if self.path in bodies else 404)
                if redirect:
                    self.send_header('Location', redirect)
                self.send_header('Content-Type', 'image/svg+xml' if self.path.endswith('.svg') else
                                 'text/markdown' if self.path.endswith('.md') else 'text/html')
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
        self.assertEqual(payload['action'], 'captured' if args[0] in ('capture', 'capture-diagram-review') and ok
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


    def test_current_reader_capture_can_bind_actual_candidate_checkout(self):
        root = self.root / 'candidate'
        document = root / 'content/docs/coming-soon.json'
        document.parent.mkdir(parents=True)
        document.write_text(json.dumps(self.doc))
        def git(*args):
            subprocess.check_output(['git', '-C', str(root), *args], timeout=10)
        git('init', '-q')
        git('add', 'content/docs/coming-soon.json')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture candidate')
        repair = SCRIPT.with_name('review_repair.py')
        def fingerprint():
            out = subprocess.check_output([sys.executable, '-B', repair, '--root', root, '--fingerprint'], timeout=15)
            return json.loads(out)['candidate']
        first = fingerprint()
        with self.server() as (base, _):
            output = self.root / 'bound-bundle'
            self.cli('capture', '--document', document, '--version', 'coming-soon', '--base-url', base,
                     '--candidate-root', root, '--output', output)
            self.assertEqual(json.loads((output / 'index.json').read_bytes())['candidateSha256'], first)
            document.write_text(json.dumps(self.doc, indent=2))
            second = fingerprint()
            self.assertNotEqual(first, second)
            next_output = self.root / 'bound-bundle-new'
            self.cli('capture', '--document', document, '--version', 'coming-soon', '--base-url', base,
                     '--candidate-root', root, '--output', next_output)
            self.assertEqual(json.loads((next_output / 'index.json').read_bytes())['candidateSha256'], second)

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

    def test_failure_recovery_cli_requires_the_current_chain_and_complete_reading(self):
        bundle = self.fixture_bundle()
        index = bundle / 'index.json'
        newcomer = self.synthetic_report(bundle)
        review_roles = {'authorTaskId': 'synthetic-author-task', 'factualReviewerTaskId': 'synthetic-factual-task',
                        'newcomerTaskId': newcomer['reviewerTaskId'], 'editorialTaskId': 'synthetic-editor-task',
                        'securityPrivacyTaskId': 'synthetic-security-task', 'reviewerTaskId': 'synthetic-recovery-task'}
        core = {key: newcomer[key] for key in ('execution', 'checkedRoutes', 'inputManifestSha256', 'passed')}
        core.update(summary='Independent synthetic reading for this fixture.',
                    limits='Rendered reading only; no recovery was executed.', findings=[])
        topics = ['pending-and-failed', 'safe-retry', 'recovery-boundaries', 'escalation']
        editor = {**core, 'perspective': 'editorial', 'reviewerTaskId': review_roles['editorialTaskId']}
        security = {**core, 'perspective': 'security-privacy', 'reviewerTaskId': review_roles['securityPrivacyTaskId'],
                    'checkedTopics': ['secrets', 'identifiers', 'diagnostics', 'data-boundaries']}
        finding = {'url': json.loads(index.read_bytes())['entryUrl'], 'location': 'Synthetic entry, Markdown',
                   'evidence': 'Synthetic reader page', 'impact': 'Readers might miss where the status is described.',
                   'recommendation': 'Consider a link to the existing status description.', 'blocking': False}
        recovery = {**core, 'perspective': 'failure-recovery', 'reviewerTaskId': review_roles['reviewerTaskId'],
                    'checkedTopics': topics, 'findings': [finding]}
        reports = {'newcomer': newcomer, 'editorial': editor, 'security-privacy': security, 'failure-recovery': recovery}
        paths = {key: self.root / f'{key}.json' for key in reports}

        def check(ok=True, reviewer=review_roles['reviewerTaskId']):
            return self.cli('validate-failure-recovery', '--inputs', index, '--document', self.document,
                            '--report', paths['failure-recovery'], '--newcomer-report', paths['newcomer'],
                            '--editorial-report', paths['editorial'], '--security-privacy-report', paths['security-privacy'],
                            '--author-task-id', review_roles['authorTaskId'],
                            '--factual-reviewer-task-id', review_roles['factualReviewerTaskId'],
                            '--newcomer-task-id', review_roles['newcomerTaskId'], '--editorial-task-id', review_roles['editorialTaskId'],
                            '--security-privacy-task-id', review_roles['securityPrivacyTaskId'], '--failure-recovery-task-id', reviewer,
                            ok=ok)

        for key, report in reports.items():
            paths[key].write_text(json.dumps(report))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = check()
        self.assertEqual(result['checkedRoutes'], self.routes)
        self.assertEqual(result['checkedTopics'], topics)
        self.assertEqual(result['nonblockingFindings'], [finding])
        self.assertEqual(result['inputManifestSha256'], hashlib.sha256(before[index]).hexdigest())
        for field, key in [('newcomerReportSha256', 'newcomer'), ('editorialReportSha256', 'editorial'),
                           ('securityPrivacyReportSha256', 'security-privacy'), ('failureRecoveryReportSha256', 'failure-recovery')]:
            self.assertEqual(result[field], hashlib.sha256(before[paths[key]]).hexdigest())
        for field, actual_id in review_roles.items():
            self.assertEqual(result[field], actual_id)
            if field != 'reviewerTaskId':
                self.assertIn('Distinct actual failure/recovery', check(ok=False, reviewer=actual_id)['detail'])

        for name, field, value in [
                ('newcomer', 'reviewerTaskId', review_roles['reviewerTaskId']),
                ('editorial', 'passed', False), ('security-privacy', 'checkedTopics', ['secrets']),
                ('security-privacy', 'inputManifestSha256', 'a' * 64),
                ('security-privacy', 'reviewerTaskId', review_roles['reviewerTaskId']),
                ('failure-recovery', 'reviewerTaskId', review_roles['securityPrivacyTaskId']),
                ('failure-recovery', 'checkedTopics', topics[:-1]),
                ('failure-recovery', 'checkedTopics', [*topics, topics[0]]),
                ('failure-recovery', 'checkedRoutes', self.routes[:-1]), ('failure-recovery', 'limits', 'unknown'),
                ('failure-recovery', 'passed', 1), ('failure-recovery', 'execution', 'hands-on'),
                ('failure-recovery', 'findings', [{**finding, 'blocking': True}]),
                ('failure-recovery', 'findings', [{**finding, 'blocking': 0}]),
                ('failure-recovery', 'findings', [{**finding, 'publicSource': ''}])]:
            with self.subTest(report=name, field=field):
                paths[name].write_text(json.dumps({**reports[name], field: value}))
                check(ok=False)
                paths[name].write_bytes(before[paths[name]])
        for name in ('security-privacy', 'failure-recovery'):
            paths[name].unlink()
            self.assertIn(f'Missing regular {name} report', check(ok=False)['detail'])
            paths[name].write_bytes(before[paths[name]])
        body = bundle / '01.md'
        body.write_bytes(before[body] + b' changed after the review')
        self.assertIn('Reader body length/hash mismatch', check(ok=False)['detail'])
        body.write_bytes(before[body])
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_usability_accessibility_gate_binds_prior_reports_and_refuses_incomplete_review(self):
        bundle = self.fixture_bundle()
        index = bundle / 'index.json'
        newcomer = self.synthetic_report(bundle)
        roles = {'author': 'synthetic-author-task', 'factual-reviewer': 'synthetic-factual-task',
                 'newcomer': newcomer['reviewerTaskId'], 'editorial': 'synthetic-editor-task',
                 'security-privacy': 'synthetic-security-task', 'failure-recovery': 'synthetic-recovery-task',
                 'usability-accessibility': 'synthetic-access-task'}
        common = {key: newcomer[key] for key in ('execution', 'checkedRoutes', 'inputManifestSha256', 'passed')}
        common.update(summary='Synthetic reader checked the rendered fixture.', limits='No interactive behavior tested.', findings=[])
        topics = {'editorial': [], 'security-privacy': ['secrets', 'identifiers', 'diagnostics', 'data-boundaries'],
                  'failure-recovery': ['pending-and-failed', 'safe-retry', 'recovery-boundaries', 'escalation'],
                  'usability-accessibility': ['guide-discovery', 'task-navigation', 'accessible-structure', 'alternatives-and-reflow']}
        reports = {name: {**common, 'perspective': name, 'reviewerTaskId': roles[name], 'checkedTopics': checked}
                   for name, checked in topics.items()}
        reports['newcomer'] = newcomer
        name = 'usability-accessibility'
        paths = {role: self.root / f'{role}.json' for role in reports}
        for role, report in reports.items():
            paths[role].write_text(json.dumps(report))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

        def check(ok=True, reviewer=roles[name]):
            flags = [item for role, identity in roles.items()
                     for item in (f'--{role}-task-id', reviewer if role == name else identity)]
            paths_args = [item for role, path in paths.items()
                          for item in ('--report' if role == name else f'--{role}-report', path)]
            return self.cli('validate-usability-accessibility', '--inputs', index, '--document', self.document,
                            *paths_args, *flags, ok=ok)

        result = check()
        self.assertEqual(result['checkedTopics'], topics[name])
        self.assertEqual(result['checkedRoutes'], self.routes)
        self.assertEqual(result['reviewerTaskId'], roles[name])
        self.assertEqual(result['failureRecoveryTaskId'], roles['failure-recovery'])
        for field, role in [('newcomer', 'newcomer'), ('editorial', 'editorial'),
                            ('securityPrivacy', 'security-privacy'), ('failureRecovery', 'failure-recovery'),
                            ('usabilityAccessibility', name)]:
            self.assertEqual(result[f'{field}ReportSha256'], hashlib.sha256(before[paths[role]]).hexdigest())
        for role, identity in roles.items():
            if role != name:
                self.assertIn('Distinct actual usability/accessibility', check(ok=False, reviewer=identity)['detail'])

        for role, key, value in [('failure-recovery', 'passed', False), ('failure-recovery', 'reviewerTaskId', roles[name]),
                                 ('failure-recovery', 'inputManifestSha256', 'a' * 64), (name, 'passed', 1),
                                 (name, 'execution', 'browser-tested'), (name, 'limits', 'unknown'),
                                 (name, 'checkedTopics', topics[name][:-1]), (name, 'checkedRoutes', self.routes[:-1]),
                                 (name, 'reviewerTaskId', roles['failure-recovery']), (name, 'findings', None),
                                 (name, 'findings', [{'url': json.loads(index.read_bytes())['entryUrl'],
                                     'location': 'Synthetic entry', 'evidence': 'Synthetic reader page',
                                     'impact': 'An essential action is inaccessible.',
                                     'recommendation': 'Make the essential action reachable.', 'blocking': True}])]:
            with self.subTest(role=role, field=key):
                paths[role].write_text(json.dumps({**reports[role], key: value}))
                check(ok=False)
                paths[role].write_bytes(before[paths[role]])
        paths['failure-recovery'].unlink()
        self.assertIn('Missing regular failure-recovery report', check(ok=False)['detail'])
        paths['failure-recovery'].write_bytes(before[paths['failure-recovery']])
        body = bundle / '01.md'
        body.write_bytes(before[body] + b' changed after the review')
        self.assertIn('Reader body length/hash mismatch', check(ok=False)['detail'])
        body.write_bytes(before[body])
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_visual_design_cli_binds_prior_reports_and_actual_capture_inventory(self):
        import struct
        import zlib

        def png(width, height):
            def chunk(kind, data):
                return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
            return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress(bytes((width * 3 + 1) * height))) + chunk(b'IEND', b''))

        bundle = self.fixture_bundle()
        index = bundle / 'index.json'
        newcomer = self.synthetic_report(bundle)
        visual = self.root / 'visual'
        visual.mkdir()
        shots = []
        for i, row in enumerate(json.loads(index.read_bytes())['documents']):
            for view, width, height in (('desktop', 1440, 900), ('phone', 375, 812)):
                file = f'{i:02d}-{view}.png'
                data = png(width, height)
                (visual / file).write_bytes(data)
                shots.append({'route': row['route'], 'file': file, 'view': view, 'viewport': {'width': width, 'height': height},
                              'htmlSha256': row['html']['sha256'], 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        visual_index = visual / 'index.json'
        manifest = {'kind': 'rendered-public-visual-input', 'inputManifestSha256': newcomer['inputManifestSha256'],
                    'screenshots': shots}
        visual_index.write_text(json.dumps(manifest))
        name = 'visual-design'
        topics = {'editorial': [], 'security-privacy': ['secrets', 'identifiers', 'diagnostics', 'data-boundaries'],
                  'failure-recovery': ['pending-and-failed', 'safe-retry', 'recovery-boundaries', 'escalation'],
                  'usability-accessibility': ['guide-discovery', 'task-navigation', 'accessible-structure', 'alternatives-and-reflow'],
                  name: ['hierarchy', 'legibility', 'layout', 'consistency']}
        roles = {role: f'synthetic-{role}' for role in (*topics, 'author', 'factual-reviewer')}
        roles['newcomer'] = newcomer['reviewerTaskId']
        common = {key: newcomer[key] for key in ('execution', 'checkedRoutes', 'inputManifestSha256', 'passed')}
        common.update(summary='Synthetic reader checked both fixture views.', limits='Offline test fixture, not browser evidence.', findings=[])
        reports = {role: {**common, 'perspective': role, 'reviewerTaskId': roles[role], 'checkedTopics': labels}
                   for role, labels in topics.items()}
        reports['newcomer'] = newcomer
        reports[name].update(visualManifestSha256=hashlib.sha256(visual_index.read_bytes()).hexdigest(),
                             checkedScreenshots=[shot['file'] for shot in shots])
        paths = {role: self.root / f'{role}.json' for role in reports}
        for role in reports:
            paths[role].write_text(json.dumps(reports[role]))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

        def check(ok=True, reviewer=roles[name]):
            return self.cli('validate-visual-design', '--inputs', index, '--document', self.document, '--visual-inputs', visual_index,
                            *[item for role, path in paths.items() for item in ('--report' if role == name else f'--{role}-report', path)],
                            *[item for role, identity in roles.items() for item in (f'--{role}-task-id', reviewer if role == name else identity)],
                            ok=ok)

        result = check()
        self.assertEqual(result['checkedScreenshots'], reports[name]['checkedScreenshots'])
        self.assertEqual(result['visualManifestSha256'], reports[name]['visualManifestSha256'])
        self.assertEqual(result['visualDesignReportSha256'], hashlib.sha256(before[paths[name]]).hexdigest())
        self.assertEqual(result['usabilityAccessibilityTaskId'], roles['usability-accessibility'])
        for role, identity in roles.items():
            if role != name:
                self.assertIn('Distinct actual visual design reviewer', check(ok=False, reviewer=identity)['detail'])
        for role in reports:
            paths[role].write_text(json.dumps({**reports[role], 'passed': False}))
            check(ok=False)
            paths[role].write_bytes(before[paths[role]])
        for key, value in [('visualManifestSha256', '1' * 64), ('checkedScreenshots', reports[name]['checkedScreenshots'][:-1]),
                           ('checkedTopics', topics[name][:-1]), ('execution', 'hands-on')]:
            paths[name].write_text(json.dumps({**reports[name], key: value}))
            check(ok=False)
            paths[name].write_bytes(before[paths[name]])
        for edited, refusal in [
                (shots[:-1], 'Incomplete or duplicate visual screenshots'),
                ([*shots, shots[0]], 'Incomplete or duplicate visual screenshots'),
                ([{**shots[0], 'htmlSha256': 'b' * 64}, *shots[1:]], 'Screenshot binding'),
                ([{**shots[0], 'viewport': {'width': 1440, 'height': 901}}, *shots[1:]], 'Wrong screenshot viewport')]:
            visual_index.write_text(json.dumps({**manifest, 'screenshots': edited}))
            self.assertIn(refusal, check(ok=False)['detail'])
        visual_index.write_bytes(before[visual_index])
        shot = visual / shots[0]['file']
        shot.write_bytes(before[shot] + b'changed')
        self.assertIn('Screenshot length/hash mismatch', check(ok=False)['detail'])
        truncated = before[shot][:33]  # Valid-looking header, no decodable image data.
        shot.write_bytes(truncated)
        bad = {**shots[0], 'bytes': len(truncated), 'sha256': hashlib.sha256(truncated).hexdigest()}
        visual_index.write_text(json.dumps({**manifest, 'screenshots': [bad, *shots[1:]]}))
        self.assertIn('Incomplete PNG image data', check(ok=False)['detail'])
        visual_index.write_bytes(before[visual_index])
        shot.write_bytes(before[shot])
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_diagram_gate_requires_actual_browser_observations_and_exact_sources(self):
        import struct
        import zlib

        digest = lambda data: hashlib.sha256(data).hexdigest()
        root = self.root / 'site'
        self.document = root / 'content/docs/coming-soon.json'
        self.document.parent.mkdir(parents=True)
        alt = 'Synthetic actor sends terms to the application.'
        self.doc['pages'][0]['sections'] = [{'id': 'flow', 'diagram': {'id': 'sample', 'caption': 'Synthetic flow.', 'alt': alt}}]
        self.doc['pages'][1]['sections'] = []
        self.document.write_text(json.dumps(self.doc))
        route = self.routes[1]
        asset = '/docs-assets/coming-soon/sample'
        assets = {'svg': b'<svg xmlns="http://www.w3.org/2000/svg"><text>test</text></svg>', 'mmd': b'graph TD\n A --> B\n'}
        for kind, data in assets.items():
            relative = asset + '.' + kind
            f = root / 'public' / relative.lstrip('/')
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            self.bodies[relative] = data
        self.bodies[route] = f'<html><article><section id="flow"><figure><img src="{asset}.svg" alt="{alt}"></figure></section></article></html>'.encode()
        md_path = route + '/index.md'
        self.bodies[md_path] = f'![{alt}](<{asset}.svg>)\n'.encode()
        original_md = self.bodies[md_path]
        index = self.root / 'bundle/index.json'
        diagram = self.root / 'diagrams'
        visual = self.root / 'visual'
        visual.mkdir()
        visual_index = visual / 'index.json'
        armed = False

        def late_drift(path):
            if armed and path == asset + '.mmd':
                self.bodies[md_path] = original_md + b'\nLate renderer drift.\n'

        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

        # Browser behavior is covered by the real Node/Playwright tests. This fixture only
        # exercises retention and rejection of those observations through the actual Python CLI.
        with self.server(on_request=late_drift) as (base, requests):
            self.capture(base)
            newcomer = self.synthetic_report(index.parent)
            shots = []
            for i, row in enumerate(json.loads(index.read_bytes())['documents']):
                for view, width, height in (('desktop', 1440, 900), ('phone', 375, 812)):
                    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                            + chunk(b'IDAT', zlib.compress(bytes((width * 3 + 1) * height))) + chunk(b'IEND', b''))
                    leaf = f'{i:02d}-{view}.png'
                    (visual / leaf).write_bytes(data)
                    parsed = {'url': base + asset + '.svg', 'alt': alt, 'visible': True}
                    observations = {'markdownSha256': row['md']['sha256'], 'markdown': [parsed] if i == 1 else [],
                                    'html': [{**parsed, 'section': 'flow', 'sha256': digest(assets['svg'])}] if i == 1 else []}
                    shots.append({'route': row['route'], 'file': leaf, 'view': view, 'viewport': {'width': width, 'height': height},
                                  'htmlSha256': row['html']['sha256'], 'bytes': len(data), 'sha256': digest(data),
                                  'diagramObservations': observations})
            visual_payload = {'kind': 'rendered-public-visual-input', 'inputManifestSha256': newcomer['inputManifestSha256'],
                              'screenshots': shots}
            visual_index.write_text(json.dumps(visual_payload))
            original_visual = visual_index.read_bytes()
            capture_args = ('capture-diagram-review', '--inputs', index, '--visual-inputs', visual_index,
                            '--document', self.document, '--site-root', root)
            self.assertEqual(self.cli(*capture_args, '--output', diagram)['diagrams'], 1)
            self.assertEqual(requests.count(asset + '.svg'), 1)
            self.assertEqual(requests.count(asset + '.mmd'), 1)

            armed = True
            rejected = self.root / 'late-diagrams'
            self.assertIn('Live renderer HTML/Markdown differs', self.cli(*capture_args, '--output', rejected, ok=False)['detail'])
            self.assertTrue((rejected / '00.mmd').is_file())
            self.assertFalse((rejected / 'index.json').exists())
            armed = False
            self.bodies[md_path] = original_md

            view = shots[2]
            observed = view['diagramObservations']
            for label, field, wrong in (('inert', 'html', []),
                                        ('alt', 'markdown', [{**observed['markdown'][0], 'alt': '**Synthetic**'}]),
                                        ('alternate', 'html', [{**observed['html'][0], 'url': base + '/alternate.svg'}]),
                                        ('hash', 'html', [{**observed['html'][0], 'sha256': '0' * 64}])):
                altered = {**view, 'diagramObservations': {**observed, field: wrong}}
                visual_index.write_text(json.dumps({**visual_payload, 'screenshots': [*shots[:2], altered, *shots[3:]]}))
                dest = self.root / (label + '-diagrams')
                self.assertIn(f'Actual rendered {field} image', self.cli(*capture_args, '--output', dest, ok=False)['detail'])
                self.assertFalse(dest.exists())
            visual_index.write_bytes(original_visual)

        diagram_index = diagram / 'index.json'
        occurrence = route + '#flow'
        self.assertEqual(json.loads(diagram_index.read_bytes())['diagrams'][0]['occurrence'], occurrence)
        for kind, data in assets.items():
            self.assertEqual((diagram / ('00.' + kind)).read_bytes(), data)
        terminal_name = 'diagram-review'
        topics = {'editorial': [], 'security-privacy': ['secrets', 'identifiers', 'diagnostics', 'data-boundaries'],
                  'failure-recovery': ['pending-and-failed', 'safe-retry', 'recovery-boundaries', 'escalation'],
                  'usability-accessibility': ['guide-discovery', 'task-navigation', 'accessible-structure', 'alternatives-and-reflow'],
                  'visual-design': ['hierarchy', 'legibility', 'layout', 'consistency'],
                  terminal_name: ['nodes-and-edges', 'caption-and-alternative', 'teaching-usefulness']}
        roles = {role: f'synthetic-{role}' for role in (*topics, 'author', 'factual-reviewer')}
        roles['newcomer'] = newcomer['reviewerTaskId']
        common = {key: newcomer[key] for key in ('execution', 'checkedRoutes', 'inputManifestSha256', 'passed')}
        common.update(summary='Synthetic companion report for this fixture.', limits='Local fixture only.', findings=[])
        reports = {role: {**common, 'perspective': role, 'reviewerTaskId': roles[role], 'checkedTopics': labels}
                   for role, labels in topics.items()}
        visual_hash = digest(visual_index.read_bytes())
        reports['newcomer'] = newcomer
        reports['visual-design'].update(visualManifestSha256=visual_hash, checkedScreenshots=[shot['file'] for shot in shots])
        source = f"https://github.com/pubky/paykit-rs/blob/{self.doc['version']['commit']}/src/lib.rs#L2-L5"
        review = reports[terminal_name]
        review.update(perspective='diagram-accuracy-usefulness', diagramManifestSha256=digest(diagram_index.read_bytes()),
                      visualManifestSha256=visual_hash, checks=[{'occurrence': occurrence, 'semantics': 'Synthetic edge A to B.',
                      'usefulness': 'Synthetic explanation with corresponding caption and alternative.', 'publicSources': [source]}])
        paths = {role: self.root / f'{role}.json' for role in reports}
        for role in reports:
            paths[role].write_text(json.dumps(reports[role]))

        def check(ok=True, reviewer=roles[terminal_name]):
            return self.cli('validate-diagram-review', '--inputs', index, '--visual-inputs', visual_index,
                            '--diagram-inputs', diagram_index, '--document', self.document, '--site-root', root,
                            *[datum for role, f in paths.items() for datum in ('--report' if role == terminal_name else f'--{role}-report', f)],
                            *[datum for role, value in roles.items() for datum in (f'--{role}-task-id', reviewer if role == terminal_name else value)],
                            ok=ok)

        accepted = check()
        self.assertEqual(accepted['checkedDiagrams'], [occurrence])
        self.assertEqual(accepted['diagramManifestSha256'], digest(diagram_index.read_bytes()))
        for role, identity in roles.items():
            if role != terminal_name:
                check(ok=False, reviewer=identity)
        for role in reports:
            paths[role].write_text(json.dumps({**reports[role], 'passed': False}))
            check(ok=False)
            paths[role].write_text(json.dumps(reports[role]))
        for wrong_source in ('https://github.com/pubky/paykit-rs/blob/main/src/lib.rs#L2-L5', source.replace('L2-L5', 'L9-L3')):
            paths[terminal_name].write_text(json.dumps({**review, 'checks': [{**review['checks'][0], 'publicSources': [wrong_source]}]}))
            self.assertIn('exact-version', check(ok=False)['detail'])
        (diagram / '00.svg').write_bytes(b'changed after capture')
        paths[terminal_name].write_text(json.dumps(review))
        self.assertIn('Captured diagram asset changed', check(ok=False)['detail'])

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
