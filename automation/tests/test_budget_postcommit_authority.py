"""Postcommit state-loss regressions, using owned offline software fixtures only.

The public projection fixture supplies synthetic source/API inputs; real frozen
verification, checkpoint replacement, run records, state writes and cleanup run.
Set BUDGET_PROJECTION_ROOT to retain raw fixture evidence outside the source tree.
"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_budget_projection_failures import begin, caught, fixture, p


def standalone_report(case, kind):
    root, cache, run_id, module, calls = case
    argv = [sys.executable, str(root / 'automation/pipeline.py'), 'budget-report',
            '--run', run_id, '--format', kind]
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=5)
    (root / ('standalone-' + kind + '.stdout')).write_text(result.stdout)
    (root / ('standalone-' + kind + '.stderr')).write_text(result.stderr)
    (root / ('standalone-' + kind + '.json')).write_text(json.dumps(
        {'argv': argv, 'cwd': str(root), 'exitCode': result.returncode}, indent=2))
    return result


@contextlib.contextmanager
def verified_fixture(name):
    with fixture(name) as (root, cache, lock, module):
        run_id = begin()['runId']
        manifest = {'fingerprint': 'postcommit-authority-software-unit'}
        p.save_json(root / 'public/docs-manifest.json', manifest)
        p.save_json(cache / 'pending-publication.json',
                    {'runId': run_id, 'sha': 'd' * 40, 'fingerprint': manifest['fingerprint']})
        calls = []
        def api(endpoint):
            calls.append(endpoint)
            if endpoint.startswith('deployments?'):
                return [{'id': 1, 'environment': 'postcommit-software-unit'}]
            if endpoint == 'deployments/1/statuses?per_page=100':
                return [{'state': 'success', 'environment_url': 'https://fixture.vercel.app'}]
            if endpoint == 'branches/docs-automation':
                return {'commit': {'sha': 'd' * 40}}
            raise AssertionError('Unexpected synthetic public boundary: ' + endpoint)
        with patch.object(p, 'fork_api', side_effect=api), \
             patch.object(p, 'get_json', return_value=manifest):
            yield root, cache, run_id, module, calls


class StateFault:
    """Physically fail one selected postcommit rename, retaining exact old bytes."""
    def __init__(self, case, boundary='close', persistent=False):
        self.root, self.cache, self.run_id, self.module, self.calls = case
        self.boundary, self.persistent = boundary, persistent
        self.path = self.cache / 'runs' / self.run_id / 'budget-state.json'
        self.hits = []
        self.old = None
        self.replace = Path.replace

    def obstruct(self, source, target):
        target = Path(target)
        checkpoint_path = self.cache / 'last-success.json'
        if target == self.path and checkpoint_path.is_file() and not self.hits:
            state = json.loads(source.read_text())
            rows = {row['name']: row for row in state['observations']}
            selected = (state['outcome'] == 'published' and
                        (self.boundary == 'close' and rows['finalize']['endedAt'] is None or
                         self.boundary == 'finalize-finish' and rows['finalize']['endedAt'] is not None and rows['verify']['endedAt'] is None or
                         self.boundary == 'verify-finish' and all(row['endedAt'] is not None for row in rows.values())))
            if selected:
                assert not (self.cache / 'run.lock').exists(), 'fault preceded owned lease cleanup'
                assert p.read_json(target.parent / 'run.json')['status'] == 'published'
                assert p.read_json(checkpoint_path)['verifiedDeployment'] is True
                self.old = target.read_bytes()
                (self.root / 'prior-state.raw').write_bytes(self.old)
                (self.root / 'proposed-state.raw').write_bytes(source.read_bytes())
                self.hits.append({'boundary': self.boundary, 'checkpoint': checkpoint_path.read_text()})
                target.unlink()
                target.mkdir()
                try:
                    return self.replace(source, target)
                except OSError as error:
                    self.hits[0]['errorType'] = type(error).__name__
                    raise
                finally:
                    if not self.persistent:
                        target.rmdir()
                        target.write_bytes(self.old)
        return self.replace(source, target)

    def invoke(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        real_guard = p.guard_run
        def precommit_only(*args, **kwargs):
            assert not (self.cache / 'last-success.json').exists(), 'postcheckpoint guard introduced'
            return real_guard(*args, **kwargs)
        with patch.object(Path, 'replace', lambda source, target: self.obstruct(source, target)), \
             patch.object(p, 'guard_run', side_effect=precommit_only), \
             patch.object(p.sys, 'argv', ['pipeline.py', 'verify', '--run', self.run_id]), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            error = caught(p.main)
        (self.root / 'verify.stdout').write_text(stdout.getvalue())
        (self.root / 'verify.stderr').write_text(stderr.getvalue())
        (self.root / 'fault.json').write_text(json.dumps(self.hits, indent=2))
        return error, stdout.getvalue(), stderr.getvalue()


class PostcommitAuthorityTests(unittest.TestCase):
    def test_close_state_failure_does_not_retroactively_fail_accepted_verification(self):
        with verified_fixture('close-state-authority') as case:
            fault = StateFault(case)
            error, stdout, stderr = fault.invoke()
            self.assertEqual(len(fault.hits), 1, 'real physical state fault was not reached')
            self.assertEqual(fault.hits[0]['errorType'], 'IsADirectoryError')
            self.assertIsNone(error, 'postcheckpoint telemetry retroactively failed accepted verification')
            envelope = json.loads(stdout)
            self.assertEqual(envelope['published'], 'd' * 40)
            self.assertTrue(envelope['budgetSummary']['stateErrors'], 'state loss must not be silent')
            self.assertEqual(len(fault.calls), 3, 'acceptance was replayed')
            self.assertFalse((fault.cache / 'run.lock').exists())
            self.assertEqual(p.read_json(fault.path.parent / 'run.json')['status'], 'published')
            self.assertEqual((fault.cache / 'last-success.json').read_text(), fault.hits[0]['checkpoint'])

    def test_close_loss_recovers_published_with_unknown_end_not_running(self):
        with verified_fixture('close-state-recovery') as case:
            fault = StateFault(case)
            error, stdout, stderr = fault.invoke()
            self.assertIsNone(error)
            checkpoint = (fault.cache / 'last-success.json').read_bytes()
            recovered = p.budget_report(fault.run_id)
            self.assertEqual(recovered['outcome'], 'published', 'accepted authority recovered as running')
            self.assertEqual(recovered['reportingAvailability'], 'degraded')
            self.assertEqual(recovered['timingAvailability'], 'partial')
            self.assertIsNone(recovered['endedAt'], 'missing close must not invent a terminal timestamp')
            self.assertIsNone(recovered['wallClockSeconds'], 'closed duration is unknown after lost close')
            self.assertIsNone(recovered['runRemainingSeconds'])
            self.assertIsNone(recovered['runOverrunSeconds'])
            self.assertTrue(recovered['stateErrors'])
            self.assertIs(recovered['publicationApprovalGranted'], False)
            self.assertFalse(any(row['name'] in ('verify', 'finalize') and row['status'] == 'failed'
                                 for row in recovered['observations']))
            self.assertEqual(checkpoint, (fault.cache / 'last-success.json').read_bytes())
            self.assertIn('telemetry', fault.module.text(recovered).lower())
            cli = standalone_report(case, 'json')
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(json.loads(cli.stdout)['outcome'], 'published')
            self.assertIsNone(json.loads(cli.stdout)['endedAt'])

    def test_persistent_state_obstruction_returns_published_with_unavailable_timing_then_partial_recovery(self):
        with verified_fixture('persistent-state-recovery') as case:
            fault = StateFault(case, persistent=True)
            error, stdout, stderr = fault.invoke()
            self.assertEqual(len(fault.hits), 1)
            self.assertIsNone(error)
            report = json.loads(stdout)['budgetSummary']
            self.assertEqual(report['outcome'], 'published', 'unavailable timing must not replace accepted action outcome')
            self.assertEqual(report['reportingAvailability'], 'unavailable')
            self.assertEqual(report['timingAvailability'], 'unavailable')
            self.assertIsNone(report['policySnapshot'], 'do not backfill unavailable observations from current config')
            self.assertIsNone(report['endedAt'])
            self.assertIsNone(report['wallClockSeconds'])
            self.assertEqual(report['observations'], [])
            self.assertTrue(report['stateErrors'])
            self.assertIs(report['publicationApprovalGranted'], False)
            self.assertTrue(fault.path.is_dir())
            self.assertIn('published', fault.module.text(report))
            cli = standalone_report(case, 'json')
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(json.loads(cli.stdout)['outcome'], 'published')
            self.assertEqual(json.loads(cli.stdout)['timingAvailability'], 'unavailable')
            cli_text = standalone_report(case, 'text')
            self.assertEqual(cli_text.returncode, 0, cli_text.stderr)
            self.assertIn('published', cli_text.stdout)
            self.assertIn('Telemetry unavailable', cli_text.stdout)
            fault.path.rmdir()
            fault.path.write_bytes(fault.old)
            recovered = p.budget_report(fault.run_id)
            self.assertEqual(recovered['outcome'], 'published')
            self.assertEqual(recovered['timingAvailability'], 'partial')
            self.assertIsNone(recovered['endedAt'])
            self.assertTrue(any(row['name'] == 'verify' and row['endedAt'] is None
                                for row in recovered['observations']))
            self.assertFalse(any(row['status'] == 'failed' for row in recovered['observations']))
            self.assertEqual(fault.path.read_bytes(), fault.old, 'report recovery must not invent durable endpoints')
            self.assertEqual(len(fault.calls), 3)
            self.assertEqual((fault.cache / 'last-success.json').read_text(), fault.hits[0]['checkpoint'])

    def test_finalize_and_verify_finish_state_loss_preserve_unknown_open_observations(self):
        for boundary in ('finalize-finish', 'verify-finish'):
            with self.subTest(boundary=boundary), verified_fixture(boundary) as case:
                fault = StateFault(case, boundary=boundary)
                error, stdout, stderr = fault.invoke()
                self.assertEqual(len(fault.hits), 1, 'physical finish fault not reached')
                self.assertIsNone(error)
                report = json.loads(stdout)['budgetSummary']
                self.assertEqual(report['outcome'], 'published')
                self.assertEqual(report['reportingAvailability'], 'degraded')
                self.assertEqual(report['timingAvailability'], 'partial')
                lost = next(row for row in report['observations'] if row['name'] == boundary.removesuffix('-finish'))
                self.assertIsNone(lost['endedAt'])
                self.assertEqual(lost['status'], 'unknown')
                self.assertIsNone(lost['elapsedSeconds'])
                self.assertFalse(any(row['status'] == 'failed' for row in report['observations']))
                self.assertEqual(report['stateErrors'][0]['errorType'], 'IsADirectoryError')
                self.assertEqual(len(fault.calls), 3)
                recovered = p.budget_report(fault.run_id)
                self.assertEqual(recovered['outcome'], 'published')
                self.assertEqual(recovered['observations'], report['observations'])
                self.assertEqual((fault.cache / 'last-success.json').read_text(), fault.hits[0]['checkpoint'])

    def test_failed_diagnostic_persistence_still_recovers_explicit_partial_published_timing(self):
        with verified_fixture('state-and-diagnostic-loss') as case:
            fault = StateFault(case)
            diagnostic = fault.path.parent / 'budget-state-loss.json'
            diagnostic.mkdir()  # Independent, persistent physical diagnostic-write obstruction.
            error, stdout, stderr = fault.invoke()
            self.assertIsNone(error)
            report = json.loads(stdout)['budgetSummary']
            self.assertTrue(diagnostic.is_dir())
            self.assertEqual(report['outcome'], 'published')
            self.assertEqual(report['reportingAvailability'], 'degraded')
            self.assertEqual(report['timingAvailability'], 'partial')
            self.assertEqual(report['stateErrors'][0]['errorType'], 'IncompleteTelemetryState')
            self.assertIsNone(report['endedAt'])
            self.assertIsNone(report['wallClockSeconds'])

    def test_success_envelope_delivery_failure_does_not_rewrite_accepted_observations(self):
        class BrokenSink(io.StringIO):
            def write(self, value):
                raise BrokenPipeError('actual final response sink is closed')
        with verified_fixture('delivery-not-verification') as case:
            root, cache, run_id, module, calls = case
            with contextlib.redirect_stdout(BrokenSink()):
                error = caught(lambda: p.verify_publication(run_id))
            self.assertIsInstance(error, BrokenPipeError)
            report = p.budget_report(run_id)
            self.assertEqual(report['outcome'], 'published')
            self.assertEqual(report['reportingAvailability'], 'available')
            self.assertTrue(all(row['status'] == 'completed' for row in report['observations']))
            self.assertIs(p.read_json(cache / 'last-success.json')['verifiedDeployment'], True)
            self.assertFalse((cache / 'run.lock').exists())

    def test_new_test_source_is_fingerprinted_and_inside_existing_staging_scope(self):
        name = 'automation/tests/' + Path(__file__).name
        self.assertIn(name, p.generation_inputs(p.ROOT))
        self.assertTrue(any(name == prefix or name.startswith(prefix + '/') for prefix in p.PUBLISH_PATHS))

    def test_unavailable_timing_keeps_projection_loss_explicit_in_surviving_text(self):
        with verified_fixture('state-plus-projection-loss') as case:
            fault = StateFault(case, persistent=True)
            projection = fault.path.parent / 'budget-report.json'
            projection.unlink()
            projection.mkdir()
            error, stdout, stderr = fault.invoke()
            self.assertIsNone(error)
            report = json.loads(stdout)['budgetSummary']
            self.assertEqual(report['outcome'], 'published')
            self.assertEqual(report['timingAvailability'], 'unavailable')
            self.assertEqual(report['projectionErrors'][0]['file'], 'budget-report.json')
            surviving = (fault.path.parent / 'budget-report.txt').read_text()
            self.assertIn('published', surviving)
            self.assertIn('budget-report.json', surviving, 'unavailable timing erased existing projection diagnostics')


if __name__ == '__main__':
    unittest.main()
