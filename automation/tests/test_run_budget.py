"""Prospective budget behavior; offline temporary roots, no generation."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('budget_pipeline', Path(__file__).parents[1] / 'pipeline.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
CONFIG = Path(__file__).parents[1] / 'config.json'


class RunBudgetTests(unittest.TestCase):
    def test_begin_failure_leaves_readable_reports_after_lease_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / '.automation'
            config = root / 'automation/config.json'
            p.save_json(config, p.read_json(CONFIG))
            with patch.object(p, 'ROOT', root), patch.object(p, 'CACHE', cache), patch.object(p, 'CONFIG', config), \
                 patch.object(p, 'resolve_sources', side_effect=ValueError('public fixture unavailable')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'public fixture unavailable'):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=False))
            runs = list((cache / 'runs').iterdir())
            self.assertEqual(len(runs), 1)
            self.assertFalse((cache / 'run.lock').exists())
            self.assertTrue((runs[0] / 'budget-report.json').is_file(), 'begin failure must leave a structured report')
            report = p.read_json(runs[0] / 'budget-report.json')
            self.assertEqual(report['outcome'], 'failed')
            self.assertGreaterEqual(report['wallClockSeconds'], 0)
            self.assertIn('failed', (runs[0] / 'budget-report.txt').read_text())

    def test_new_run_freezes_six_hour_policy_without_renewing_old_runs(self):
        cfg = p.read_json(CONFIG)
        self.assertEqual(cfg['limits']['runtimeSeconds'], 21600)
        expected = {'generation_build': 3600, 'independent_review': 10800,
                    'correction_retest': 5400, 'exports_qa_deploy': 1800}
        self.assertEqual(cfg['budget']['phasePlanningSeconds'], expected)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / '.automation'
            config = root / 'config.json'
            p.save_json(config, cfg)
            with patch.object(p, 'ROOT', root), patch.object(p, 'CACHE', cache), patch.object(p, 'CONFIG', config), \
                 patch.object(p, 'resolve_sources', side_effect=ValueError('fixture stopped')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                old = p.acquire_lease(cache, now=100, duration=3500)
                with self.assertRaisesRegex(ValueError, 'fixture stopped'):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=False))
            runs = list((cache / 'runs').iterdir())
            new = next(path for path in runs if path.name != old['id'])
            record = p.read_json(new / 'run.json')
            report = p.read_json(new / 'budget-report.json')
            self.assertEqual(record['deadline'] - record['started'], 21600)
            self.assertEqual(report['policySnapshot'], cfg['budget'])
            self.assertEqual(report['runCapSeconds'], 21600)
            self.assertEqual(p.read_json(cache / ('expired-' + old['id']) / 'owner.json')['deadline'], 3600)
            cfg['budget']['phasePlanningSeconds']['generation_build'] = 1
            p.save_json(config, cfg)
            self.assertEqual(p.read_json(new / 'budget-state.json')['policySnapshot']['phasePlanningSeconds'], expected)

    def test_budget_policy_rejects_malformed_full_configs_before_acquiring(self):
        import copy
        baseline = p.read_json(CONFIG)
        cases = []
        def case(path, value, delete=False):
            cfg = copy.deepcopy(baseline)
            target = cfg
            for key in path[:-1]:
                target = target[key]
            if delete:
                del target[path[-1]]
            else:
                target[path[-1]] = value
            cases.append(cfg)
        for value in (True, -1, float('nan'), float('inf'), '21600', None):
            case(['limits', 'runtimeSeconds'], value)
        for value in (True, -1, float('nan'), float('inf'), '3600', None):
            case(['budget', 'phasePlanningSeconds', 'generation_build'], value)
            case(['budget', 'implementationCheckpointSeconds'], value)
        for key in ('requireEveryRunReport', 'reportNoChange'):
            for value in (1, 'true', False, None):
                case(['budget', key], value)
        case(['budget', 'schemaVersion'], True)
        case(['budget', 'schemaVersion'], 2)
        case(['budget', 'extra'], 1)
        case(['budget', 'phasePlanningSeconds', 'extra'], 0)
        case(['budget', 'phasePlanningSeconds', 'independent_review'], None, delete=True)
        case(['budget', 'phasePlanningSeconds', 'generation_build'], 1)
        case(['budget'], None, delete=True)
        for index, cfg in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / 'config.json'
                cache = root / '.automation'
                p.save_json(config, cfg)
                with patch.object(p, 'CONFIG', config), patch.object(p, 'CACHE', cache), \
                     patch.object(p, 'resolve_sources', side_effect=AssertionError('invalid budget reached source work')):
                    with self.assertRaisesRegex(ValueError, '[Bb]udget'):
                        p.begin_run(p.argparse.Namespace(reuse_candidate=False))
                self.assertFalse((cache / 'run.lock').exists())

    def test_execute_stage_measures_real_work_and_all_four_phase_rows(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            cfg = p.read_json(CONFIG)
            record = p.acquire_lease(cache, duration=21600, budget_policy=cfg['budget'])
            result = p.execute_stage(record['id'], 'generate',
                lambda: p.bounded_process([sys.executable, '-c', "print('observed fixture')"],
                    cwd=directory, run_id=record['id'], cache=cache, check=True), cache)
            self.assertEqual(result.stdout, 'observed fixture\n')
            p.release_lease(record['id'], 'failed', {'error': 'offline fixture only'}, cache)
            report = p.read_json(cache / 'runs' / record['id'] / 'budget-report.json')
            self.assertIn('phases', report, 'real execute_stage must produce phase observations')
            self.assertEqual(set(report['phases']), set(cfg['budget']['phasePlanningSeconds']))
            phase = report['phases']['generation_build']
            self.assertEqual(phase['status'], 'completed')
            self.assertEqual(phase['allocatedSeconds'], 3600)
            self.assertGreater(phase['elapsedSeconds'], 0)
            self.assertAlmostEqual(phase['remainingSeconds'], 3600 - phase['elapsedSeconds'])
            self.assertEqual(phase['overrunSeconds'], 0)
            self.assertEqual(phase['assessment']['moreAllocationUseful'], 'unknown')
            self.assertIsNone(report['phases']['independent_review']['elapsedSeconds'])
            self.assertEqual(report['phases']['independent_review']['status'], 'not-run')
            self.assertGreater(report['unattributedWallSeconds'], 0)
            self.assertIsNone(report['aggregateAgentSeconds'])
            stage = report['observations'][0]
            self.assertEqual((stage['kind'], stage['name'], stage['status']), ('stage', 'generate', 'completed'))
            self.assertGreater(stage['endedAt'], stage['startedAt'])
            self.assertLessEqual(phase['elapsedSeconds'], report['wallClockSeconds'])

    @contextlib.contextmanager
    def begin_fixture(self, root, no_change=False):
        cache = root / '.automation'
        config = root / 'automation/config.json'
        p.save_json(config, p.read_json(CONFIG))
        source = {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'synthetic-budget-fixture'}
        p.save_json(root / 'docs-generation.json', {'schemaVersion': 1, 'source': source, 'defaultVersion': 'coming-soon'})
        selected = {**source, 'qualifiedRef': 'refs/tags/synthetic-budget-fixture', 'object': 'b' * 40, 'commit': 'a' * 40}
        lock = {'repository': source['repository'], 'defaultVersion': 'coming-soon', 'selectedSource': selected,
                'coming-soon': {**{key: value for key, value in selected.items() if key != 'repository'},
                    'tag': source['ref'], 'label': source['ref'], 'sourceUrl': 'https://github.com/pubky/paykit-rs/tree/' + 'a' * 40},
                'released': {'commit': 'c' * 40}}
        p.save_json(cache / 'deployment.json', {'stableDevelopmentUrl': 'https://fixture.vercel.app'})
        with patch.object(p, 'ROOT', root), patch.object(p, 'CACHE', cache), patch.object(p, 'CONFIG', config), \
             patch.object(p, 'tool_versions', return_value={'fixture': '1'}), \
             patch.object(p, 'credential_health', return_value={'warnings': []}), \
             patch.object(p, 'resolve_sources', return_value=lock), \
             patch.object(p, 'generate_docs', side_effect=AssertionError('begin must not generate')):
            fingerprint = p.input_fingerprint(lock, tools={'fixture': '1'})
            if no_change:
                p.save_json(cache / 'last-success.json', {'verifiedDeployment': True, 'sources': lock,
                    'fingerprint': fingerprint, 'processFingerprint': p.process_fingerprint(tools={'fixture': '1'}),
                    'regenerationFingerprint': p.regeneration_fingerprint(lock, tools={'fixture': '1'}),
                    'deploymentUrl': 'https://fixture.vercel.app'})
            with patch.object(p, 'get_json', return_value={'fingerprint': fingerprint}):
                yield cache

    def test_no_change_reports_measured_begin_without_touching_success_or_public_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'public/untouched.txt'
            marker.parent.mkdir()
            marker.write_bytes(b'unchanged public fixture')
            with self.begin_fixture(root, no_change=True) as cache:
                success = cache / 'last-success.json'
                before = (success.read_bytes(), success.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns)
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=False))
                result = json.loads(output.getvalue())
                self.assertEqual(result['action'], 'no-change')
                self.assertIn('runId', result, 'no-change must identify its recoverable budget report')
                report = p.read_json(cache / 'runs' / result['runId'] / 'budget-report.json')
                self.assertEqual(report['outcome'], 'no-change')
                self.assertEqual(report['observations'][0]['name'], 'begin')
                self.assertEqual(report['observations'][0]['status'], 'completed')
                self.assertGreater(report['observations'][0]['endedAt'], report['observations'][0]['startedAt'])
                self.assertIn('budgetReport', result)
                self.assertFalse((cache / 'run.lock').exists())
                self.assertEqual(before, (success.read_bytes(), success.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns))

    def test_real_concurrent_workers_are_separate_from_wall_time_and_keep_two_worker_cap(self):
        import os
        import subprocess
        import sys
        self.assertTrue(hasattr(p, 'budget_start'), 'coordinator API must bracket actual delegated work')
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            children = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.18)']) for _ in range(2)]
            try:
                observations = [p.budget_start(record['id'], 'independent_review', 'fixture-reviewer',
                    'pid:' + str(child.pid), allocation=.01, cache=cache) for child in children]
                with self.assertRaisesRegex(ValueError, '[Tt]wo|worker limit'):
                    p.budget_start(record['id'], 'independent_review', 'fixture-extra', 'pid:' + str(os.getpid()), cache=cache)
                for child in children:
                    child.wait(timeout=5)
                for observation in observations:
                    p.budget_finish(record['id'], observation, 'completed', cache=cache)
                p.release_lease(record['id'], 'failed', {'error': 'fixture only'}, cache)
                report = p.read_json(cache / 'runs' / record['id'] / 'budget-report.json')
                workers = report['workers']
                self.assertEqual({row['taskId'] for row in workers}, {'pid:' + str(child.pid) for child in children})
                self.assertGreater(report['aggregateAgentSeconds'], report['wallClockSeconds'])
                self.assertLess(report['phases']['independent_review']['elapsedSeconds'], report['aggregateAgentSeconds'])
                self.assertGreater(workers[0]['overrunSeconds'], 0)
                self.assertEqual(report['deadline'], record['deadline'])
                self.assertEqual(report['runCapSeconds'], 21600)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait()

    def test_evidence_based_assessments_distinguish_time_from_tooling_blockers(self):
        self.assertTrue(hasattr(p, 'budget_assess'), 'coordinator must record explicit allocation assessments')
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            observation = p.budget_start(record['id'], 'independent_review', 'fixture-reviewer', 'fixture-task-1', cache=cache)
            time_limited = {'moreAllocationUseful': 'yes', 'constraint': 'time',
                'remainingCoverage': 'Two fixture edge cases not checked', 'evidence': ['fixture/check.log: timeout'],
                'rationale': 'Deadline stopped actual fixture audit before those cases', 'suggestedExtraSeconds': 600}
            p.budget_finish(record['id'], observation, 'blocked', assessment=time_limited, cache=cache)
            tooling = {**time_limited, 'moreAllocationUseful': 'no', 'constraint': 'tooling',
                'remainingCoverage': 'Sandbox execution unavailable', 'evidence': ['fixture/preflight.log: sandbox unavailable'],
                'rationale': 'Time alone cannot provide the missing sandbox', 'suggestedExtraSeconds': None}
            p.budget_assess(record['id'], tooling, phase='independent_review', cache=cache)
            for bad in ({**tooling, 'moreAllocationUseful': 'yes', 'suggestedExtraSeconds': 600},
                        {**time_limited, 'evidence': []}, {**time_limited, 'suggestedExtraSeconds': True},
                        {**time_limited, 'remainingCoverage': ''}, {**time_limited, 'suggestedExtraSeconds': -1},
                        {**time_limited, 'moreAllocationUseful': True}, {**tooling, 'extra': 'unapproved'}):
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, '[Aa]ssessment|[Bb]udget'):
                    p.budget_assess(record['id'], bad, phase='independent_review', cache=cache)
            p.release_lease(record['id'], 'failed', cache=cache)
            report = p.read_json(cache / 'runs' / record['id'] / 'budget-report.json')
            self.assertEqual(report['workers'][0]['assessment'], time_limited)
            self.assertEqual(report['phases']['independent_review']['assessment'], tooling)
            self.assertIn('generation_build', report['missingAssessments'])
            text = (cache / 'runs' / record['id'] / 'budget-report.txt').read_text()
            for value in ('Two fixture edge cases not checked', 'fixture/check.log: timeout', '600', 'Sandbox execution unavailable'):
                self.assertIn(value, text)
            self.assertFalse((cache / 'review.json').exists(), 'budget assessments never create content approvals')

    def test_cli_brackets_coordinator_work_and_reads_closed_or_historical_reports(self):
        import os
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            automation = root / 'automation'
            automation.mkdir()
            for name in ('pipeline.py', 'run_budget.py'):
                shutil.copy2(Path(__file__).parents[1] / name, automation / name)
            cache = root / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            def cli(*args):
                return subprocess.run([sys.executable, str(automation / 'pipeline.py'), *args],
                    cwd=root, capture_output=True, text=True)
            started = cli('budget-start', '--run', record['id'], '--phase', 'generation_build', '--role', 'coordinator',
                          '--kind', 'coordinator', '--task-id', 'pid:' + str(os.getpid()), '--allocation', '30')
            self.assertEqual(started.returncode, 0, started.stderr)
            observation = json.loads(started.stdout)['observation']
            missing_status = cli('budget-finish', '--run', record['id'], '--observation', observation)
            self.assertNotEqual(missing_status.returncode, 0, 'coordinator must explicitly state work outcome')
            finished = cli('budget-finish', '--run', record['id'], '--observation', observation, '--outcome', 'completed')
            self.assertEqual(finished.returncode, 0, finished.stderr)
            failed = cli('fail', '--run', record['id'], '--reason', 'offline CLI fixture only')
            self.assertEqual(failed.returncode, 0, failed.stderr)
            for kind in ('json', 'text'):
                output = cli('budget-report', '--run', record['id'], '--format', kind)
                self.assertEqual(output.returncode, 0, output.stderr)
                self.assertIn('generation_build', output.stdout)
                if kind == 'json':
                    report = json.loads(output.stdout)
                    self.assertEqual(report['outcome'], 'failed')
                    self.assertGreater(report['aggregateAgentSeconds'], 0)
            historical = cache / 'runs/historical-run/run.json'
            p.save_json(historical, {'id': 'historical-run', 'started': 100, 'deadline': 3600, 'status': 'failed'})
            output = cli('budget-report', '--run', 'historical-run', '--format', 'json')
            self.assertEqual(output.returncode, 0, output.stderr)
            unavailable = json.loads(output.stdout)
            self.assertEqual(unavailable['outcome'], 'unavailable')
            self.assertIsNone(unavailable['policySnapshot'])
            self.assertFalse((historical.parent / 'budget-state.json').exists())
            self.assertTrue(all(row['elapsedSeconds'] is None and row['allocatedSeconds'] is None for row in unavailable['phases'].values()))

    def test_expiry_and_reclaim_preserve_partial_unknown_work_without_new_defaults(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            old = p.acquire_lease(cache, duration=.04)
            observation = p.budget_start(old['id'], 'independent_review', 'fixture-reviewer', 'fixture-open-task', cache=cache)
            time.sleep(.05)
            expired = p.budget_report(old['id'], cache)
            self.assertEqual(expired['outcome'], 'expired', 'report must expose expired lease even before reclaim')
            self.assertIsNone(expired['workers'][0]['elapsedSeconds'])
            self.assertEqual(expired['workers'][0]['status'], 'unknown')
            self.assertIsNone(expired['policySnapshot'])
            new = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            report = p.budget_report(old['id'], cache)
            self.assertEqual(report['outcome'], 'expired-reclaimed')
            self.assertGreaterEqual(report['endedAt'], old['deadline'])
            self.assertIsNone(report['workers'][0]['endedAt'])
            self.assertEqual(report['workers'][0]['id'], observation)
            self.assertIsNone(report['phases']['independent_review']['allocatedSeconds'])
            self.assertEqual(p.read_json(cache / ('expired-' + old['id']) / 'owner.json')['deadline'], old['deadline'])
            self.assertEqual(p.guard_run(new['id'], cache)['repairs'], 0)
            with self.assertRaisesRegex(ValueError, 'owner|expired'):
                p.budget_start(old['id'], 'generation_build', 'fixture-author', 'late-fixture', cache=cache)
            self.assertFalse((cache / 'last-success.json').exists())

    def test_interrupted_stage_persists_failure_and_requires_accounted_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            interruption = KeyboardInterrupt('fixture coordinator interrupted')
            def interrupted():
                raise interruption
            with self.assertRaises(KeyboardInterrupt) as caught:
                p.execute_stage(record['id'], 'generate', interrupted, cache)
            self.assertIs(caught.exception, interruption)
            self.assertTrue(p.guard_run(record['id'], cache).get('needsRepair'), 'termination cannot bypass repair accounting')
            report = p.budget_report(record['id'], cache)
            row = report['observations'][0]
            self.assertEqual(row['status'], 'failed')
            self.assertIsNotNone(row['endedAt'])
            self.assertEqual(row['details']['errorType'], 'KeyboardInterrupt')
            self.assertEqual(p.request_repair(record['id'], 'accounted fixture repair', cache)['repairs'], 1)
            self.assertEqual(p.guard_run(record['id'], cache)['deadline'], record['deadline'])

    def test_success_finalization_reports_after_checkpoint_without_any_new_acceptance_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            checkpoint = cache / 'last-success.json'
            p.save_json(checkpoint, {'fixture': 'previous'})
            real_guard = p.guard_run
            calls = []
            def before_commit_only(*args, **kwargs):
                self.assertEqual(p.read_json(checkpoint), {'fixture': 'previous'}, 'telemetry added a post-commit acceptance guard')
                calls.append('guard')
                return real_guard(*args, **kwargs)
            with patch.object(p, 'guard_run', side_effect=before_commit_only):
                p.finalize_success(record['id'], {'fixture': 'accepted unit checkpoint'}, cache)
            self.assertEqual(p.read_json(checkpoint), {'fixture': 'accepted unit checkpoint'})
            self.assertEqual(len(calls), 2)
            self.assertFalse((cache / 'run.lock').exists())
            report = p.budget_report(record['id'], cache)
            self.assertEqual(report['outcome'], 'published', 'accepted finalization must close its budget report')
            finalization = next(row for row in report['observations'] if row['name'] == 'finalize')
            self.assertEqual(finalization['status'], 'completed')
            self.assertGreater(finalization['endedAt'], finalization['startedAt'])
            self.assertGreaterEqual(report['endedAt'], finalization['endedAt'])
            self.assertEqual(report['deadline'], record['deadline'])

    def test_admission_queue_and_finalization_are_inside_run_wall_clock(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            real_lock = p.state_lock
            @contextlib.contextmanager
            def queued(*args, **kwargs):
                time.sleep(.04)
                with real_lock(*args, **kwargs):
                    yield
            started = time.time()
            with patch.object(p, 'state_lock', side_effect=queued):
                record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            p.finalize_success(record['id'], {'fixture': 'local checkpoint, not deployment'}, cache)
            report = p.budget_report(record['id'], cache)
            self.assertLess(record['started'] - started, .02, 'lease admission must include lock queue, not renew after waiting')
            self.assertGreaterEqual(report['admissionQueueSeconds'], .04)
            self.assertGreaterEqual(report['wallClockSeconds'], report['admissionQueueSeconds'])
            self.assertGreater(report['unattributedWallSeconds'], .03)
            self.assertEqual(record['deadline'], record['started'] + 21600)
            self.assertGreater(report['phases']['exports_qa_deploy']['elapsedSeconds'], 0)

    def test_public_entrypoints_observe_work_without_changing_their_acceptance_actions(self):
        for name, internal in (('publish', '_publish'), ('verify_publication', '_verify_publication')):
            with self.subTest(entry=name), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / '.automation'
                with patch.object(p, 'CACHE', cache):
                    record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
                    marker = []
                    expected = {'gitCommit': 'fixture-unit-sha', 'deploymentUrl': 'https://fixture.invalid'} if name == 'verify_publication' else 'actual fixture action'
                    def action(run_id):
                        marker.append(run_id)
                        if name == 'verify_publication':
                            p.finalize_success(run_id, {'fixture': 'unit checkpoint only'}, cache)
                        return expected
                    with patch.object(p, internal, side_effect=action):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(getattr(p, name)(record['id']), expected)
                    self.assertEqual(marker, [record['id']])
                    report = p.budget_report(record['id'], cache)
                    stage = 'verify' if name == 'verify_publication' else 'publish'
                    self.assertIn(stage, [row['name'] for row in report['observations']], 'public entrypoint work must be timed')
                    observed = next(row for row in report['observations'] if row['name'] == stage)
                    self.assertEqual(observed['status'], 'completed')
                    if name == 'verify_publication':
                        self.assertEqual(report['outcome'], 'published')
                        self.assertGreaterEqual(report['endedAt'], observed['endedAt'])
                    else:
                        self.assertTrue((cache / 'run.lock').exists())
                        self.assertFalse((cache / 'last-success.json').exists())

    def test_repair_retest_work_uses_correction_phase_without_resetting_the_shared_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            with self.assertRaisesRegex(ValueError, 'fixture failure'):
                p.execute_stage(record['id'], 'generate', lambda: (_ for _ in ()).throw(ValueError('fixture failure')), cache)
            p.request_repair(record['id'], 'fixture correction', cache)
            p.execute_stage(record['id'], 'generate', lambda: 'corrected fixture', cache)
            report = p.budget_report(record['id'], cache)
            self.assertEqual([row['phase'] for row in report['observations']], ['generation_build', 'correction_retest'])
            self.assertEqual(report['phases']['correction_retest']['status'], 'completed')
            self.assertEqual(p.guard_run(record['id'], cache)['repairs'], 1)
            self.assertEqual(report['deadline'], record['deadline'])

    def test_legacy_minimal_begin_fixture_keeps_its_original_run_scope_seam(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / '.automation'
            config = root / 'config.json'
            p.save_json(config, {'limits': {'runtimeSeconds': 600}})
            with patch.object(p, 'ROOT', root), patch.object(p, 'CACHE', cache), patch.object(p, 'CONFIG', config), \
                 patch.object(p, 'acquire_lease', return_value={'id': 'fixture-run'}), \
                 patch.object(p, 'run_scope', return_value=contextlib.nullcontext()), \
                 patch.object(p, 'release_lease'), patch.object(p, 'bind_native_context') as binding, \
                 patch.object(p, 'resolve_sources', side_effect=RuntimeError('fixture source failure')):
                try:
                    with self.assertRaisesRegex(RuntimeError, 'fixture source failure'):
                        p.begin_run(p.argparse.Namespace(reuse_candidate=False))
                except KeyError as error:
                    self.fail('Budget instrumentation bypassed the retained legacy run_scope seam: ' + str(error))
                binding.assert_called_once()
            self.assertFalse((cache / 'runs/fixture-run/budget-state.json').exists(), 'legacy observation gaps must not be fabricated')

    def test_outcome_envelopes_inform_usage_in_one_json_object(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.begin_fixture(root, no_change=True) as cache:
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=False))
                no_change = json.loads(output.getvalue())
                self.assertIn('budgetSummary', no_change, 'no-change must inform measured usage, not only point at a file')
                self.assertEqual(set(no_change['budgetSummary']['phases']), set(p.read_json(CONFIG)['budget']['phasePlanningSeconds']))
                self.assertGreater(no_change['budgetSummary']['wallClockSeconds'], 0)
                record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
                output = io.StringIO()
                with patch.object(sys, 'argv', ['pipeline.py', 'fail', '--run', record['id'], '--reason', 'fixture only']), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    p.main()
                failed = json.loads(output.getvalue())
                self.assertEqual(failed['budgetSummary']['outcome'], 'failed')
                self.assertEqual(failed['runId'], record['id'])
                self.assertIn('budgetReport', failed)

    def test_sigterm_during_cli_begin_keeps_partial_timings_and_closes_failed_lease(self):
        import shutil
        import subprocess
        import sys
        import time
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'automation/config.json'
            p.save_json(config, p.read_json(CONFIG))
            ready = root / 'ready'
            program = ("import importlib.util,pathlib,sys,time; "
                "s=importlib.util.spec_from_file_location('fixture',sys.argv[1]); p=importlib.util.module_from_spec(s); s.loader.exec_module(p); "
                "p.ROOT=pathlib.Path(sys.argv[2]); p.CACHE=p.ROOT/'.automation'; p.CONFIG=p.ROOT/'automation/config.json'; "
                "ready=p.ROOT/'ready'; p.resolve_sources=lambda: (ready.write_text('ready'),time.sleep(60)); "
                "sys.argv=['pipeline.py','begin']; p.main()")
            child = subprocess.Popen([sys.executable, '-c', program, str(Path(__file__).parents[1] / 'pipeline.py'), str(root)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                until = time.monotonic() + 5
                while not ready.exists() and child.poll() is None and time.monotonic() < until:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), 'termination fixture never reached real begin action')
                child.terminate()
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 143, stderr)
                self.assertFalse((root / '.automation/run.lock').exists())
                report = p.read_json(next((root / '.automation/runs').glob('*/budget-report.json')))
                self.assertEqual(report['outcome'], 'failed')
                self.assertEqual(report['observations'][0]['details']['errorType'], 'SystemExit')
                self.assertGreater(report['observations'][0]['endedAt'], report['observations'][0]['startedAt'])
                self.assertFalse((root / '.automation/last-success.json').exists())
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_explicit_unknown_outcome_is_not_invented_as_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            observation = p.budget_start(record['id'], 'independent_review', 'fixture-reviewer', 'fixture-unknown-task', cache=cache)
            p.budget_finish(record['id'], observation, 'unknown', cache=cache)
            report = p.budget_report(record['id'], cache)
            self.assertEqual(report['workers'][0]['status'], 'unknown')
            self.assertEqual(report['phases']['independent_review']['status'], 'unknown')
            self.assertIsNotNone(report['workers'][0]['elapsedSeconds'], 'bracket timing is known even if task outcome is unknown')
            self.assertEqual(report['workers'][0]['assessment']['constraint'], 'not-assessed')

    def test_coordinator_and_stage_durations_are_visible_in_both_report_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            observation = p.budget_start(record['id'], 'generation_build', 'fixture-coordinator', 'fixture-coordinator-task',
                                         allocation=10, kind='coordinator', cache=cache)
            p.execute_stage(record['id'], 'generate', lambda: 'fixture work', cache)
            p.budget_finish(record['id'], observation, 'completed', cache=cache)
            report = p.budget_report(record['id'], cache)
            self.assertTrue(all('elapsedSeconds' in row for row in report['observations']), 'stage and role duration fields must be explicit')
            self.assertEqual(len(report['agents']), 1)
            self.assertEqual(report['agents'][0]['taskId'], 'fixture-coordinator-task')
            text = (cache / 'runs' / record['id'] / 'budget-report.txt').read_text()
            self.assertIn('fixture-coordinator-task', text)
            self.assertIn('Stage generate', text)
            self.assertGreaterEqual(report['aggregateAgentSeconds'], report['observations'][1]['elapsedSeconds'])

    def test_new_direct_leases_default_to_six_hours_but_missing_allocations_remain_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache)
            self.assertEqual(record['deadline'] - record['started'], 21600)
            report = p.budget_report(record['id'], cache)
            self.assertIsNone(report['policySnapshot'])
            self.assertTrue(all(row['allocatedSeconds'] is None for row in report['phases'].values()))
        for duration in (True, -1, float('nan'), float('inf'), '21600', None, 10 ** 1000):
            with self.subTest(duration=str(duration)[:30]), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory)
                with self.assertRaisesRegex(ValueError, '[Bb]udget'):
                    p.acquire_lease(cache, duration=duration)
                self.assertFalse((cache / 'run.lock').exists())

    def test_reporting_ack_failure_does_not_replace_the_actual_stage_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            module = p.budget_module()
            finish = module.finish
            original = ValueError('actual fixture validation failure')
            def lose_ack(*args, **kwargs):
                finish(*args, **kwargs)
                raise OSError('fixture report acknowledgement lost after durable write')
            def fail():
                raise original
            with patch.object(p, 'budget_module', return_value=module), patch.object(module, 'finish', side_effect=lose_ack):
                caught = None
                try:
                    p.execute_stage(record['id'], 'validate', fail, cache)
                except BaseException as error:
                    caught = error
                self.assertIs(caught, original, 'budget reporting must not mask the genuine action failure')
            report = p.budget_report(record['id'], cache)
            self.assertEqual(report['observations'][0]['status'], 'failed')
            self.assertEqual(report['observations'][0]['details']['error'], str(original))
            self.assertEqual(p.read_json(cache / 'runs' / record['id'] / 'failures.json')[0]['error'], str(original))


    def test_begin_failures_and_verified_success_emit_budget_usage_without_extra_stdout_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.begin_fixture(root) as cache:
                stderr = io.StringIO()
                with patch.object(p, 'resolve_sources', side_effect=ValueError('fixture access blocker')), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                    with self.assertRaisesRegex(ValueError, 'fixture access blocker'):
                        p.begin_run(p.argparse.Namespace(reuse_candidate=False))
                self.assertIn('generation_build', stderr.getvalue(), 'failed begin must inform usage as well as save files')
                record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
                checkpoint = {'gitCommit': 'fixture-only-sha', 'deploymentUrl': 'https://fixture.invalid'}
                def verified(run_id):
                    p.finalize_success(run_id, checkpoint, cache)
                    return checkpoint
                output = io.StringIO()
                with patch.object(p, '_verify_publication', side_effect=verified), contextlib.redirect_stdout(output):
                    p.verify_publication(record['id'])
                result = json.loads(output.getvalue())
                self.assertEqual(result['published'], checkpoint['gitCommit'])
                self.assertEqual(result['budgetSummary']['outcome'], 'published')
                self.assertTrue(all(row['endedAt'] is not None for row in result['budgetSummary']['observations']))

    def test_coordinator_finish_requires_explicit_outcome_and_returns_measured_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            observation = p.budget_start(record['id'], 'independent_review', 'fixture-role', 'fixture-task', cache=cache)
            with self.assertRaisesRegex(ValueError, 'outcome|status'):
                p.budget_finish(record['id'], observation, cache=cache)
            result = p.budget_finish(record['id'], observation, 'blocked', cache=cache)
            self.assertEqual(result['status'], 'blocked')
            self.assertGreaterEqual(result['elapsedSeconds'], 0)
            with self.assertRaisesRegex(ValueError, 'already finished'):
                p.budget_finish(record['id'], observation, 'completed', cache=cache)

    def test_coordinator_start_rejects_malformed_brackets_without_side_effects(self):
        base = {'phase': 'independent_review', 'role': 'fixture-reviewer', 'task_id': 'fixture-task', 'allocation': 30, 'kind': 'worker'}
        cases = [('phase', None), ('phase', 'invalid'), ('kind', 'stage'), ('kind', 'invalid'),
                 ('role', None), ('role', 'role\nforged'), ('task_id', ''), ('task_id', 'task\nforged'),
                 ('allocation', True), ('allocation', -1), ('allocation', float('nan')), ('allocation', float('inf'))]
        for key, value in cases:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / '.automation'
                record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
                with self.assertRaises(ValueError):
                    p.budget_start(record['id'], cache=cache, **{**base, key: value})
                self.assertEqual(p.budget_report(record['id'], cache)['observations'], [])

    def test_cli_parser_startup_is_counted_before_begin_admission(self):
        import sys
        import time
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.begin_fixture(root, no_change=True) as cache:
                parse = p.argparse.ArgumentParser.parse_args
                def delayed(parser, *args, **kwargs):
                    time.sleep(.04)
                    return parse(parser, *args, **kwargs)
                before = time.time()
                output = io.StringIO()
                with patch.object(p.argparse.ArgumentParser, 'parse_args', delayed), \
                     patch.object(sys, 'argv', ['pipeline.py', 'begin']), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    p.main()
                report = json.loads(output.getvalue())['budgetSummary']
                self.assertLess(report['startedAt'] - before, .02, 'CLI startup must not silently disappear before the run clock starts')
                self.assertGreater(report['wallClockSeconds'], .04)
                self.assertGreater(report['admissionQueueSeconds'], .04)

    def test_worker_start_can_include_a_genuinely_observed_delegation_startup(self):
        import inspect
        import subprocess
        import sys
        import time
        self.assertIn('started_at', inspect.signature(p.budget_start).parameters)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            observed = time.time()
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.08)'])
            try:
                time.sleep(.04)
                observation = p.budget_start(record['id'], 'generation_build', 'fixture-checker', 'pid:' + str(child.pid),
                                             allocation=1, started_at=observed, cache=cache)
                child.wait(timeout=5)
                row = p.budget_finish(record['id'], observation, 'completed', cache=cache)
                self.assertEqual(row['startedAt'], observed)
                self.assertGreaterEqual(row['recordedAt'] - observed, .04)
                self.assertGreaterEqual(row['elapsedSeconds'], .08)
                self.assertEqual(row['startSource'], 'coordinator-observed')
                for invalid in (True, -1, float('nan'), float('inf'), record['started'] - 1, time.time() + 60):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        p.budget_start(record['id'], 'generation_build', 'fixture-checker', 'fixture-invalid', started_at=invalid, cache=cache)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()

    def test_publication_guard_refusals_are_observed_without_running_any_action(self):
        for entry in (p.publish, p.verify_publication):
            with self.subTest(entry=entry.__name__), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / '.automation'
                with patch.object(p, 'CACHE', cache):
                    record = p.acquire_lease(cache, now=100, duration=1)
                    with patch.object(p, '_publish', side_effect=AssertionError('expired action executed')), \
                         patch.object(p, '_verify_publication', side_effect=AssertionError('expired action executed')):
                        with self.assertRaisesRegex(ValueError, 'expired'):
                            entry(record['id'])
                    report = p.budget_report(record['id'], cache)
                    self.assertEqual(len(report['observations']), 1, 'refused public entrypoints still require truthful telemetry')
                    self.assertEqual(report['observations'][0]['status'], 'failed')
                    self.assertIn('expired', report['observations'][0]['details']['error'])
                    self.assertFalse((cache / 'last-success.json').exists())

    def test_reports_expose_run_remaining_budget_and_explicitly_grant_no_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            p.release_lease(record['id'], 'failed', cache=cache)
            report = p.budget_report(record['id'], cache)
            self.assertIn('publicationApprovalGranted', report)
            self.assertIs(report['publicationApprovalGranted'], False)
            self.assertAlmostEqual(report['runRemainingSeconds'], 21600 - report['wallClockSeconds'])
            self.assertEqual(report['runOverrunSeconds'], 0)
            self.assertIn('bracketed', report['agentTimingScope'])
            self.assertIn('never grants publication approval', (cache / 'runs' / record['id'] / 'budget-report.txt').read_text())


if __name__ == '__main__':
    unittest.main()
