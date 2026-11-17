"""Offline lifecycle regressions; synthetic public boundaries, no publication.

Set BUDGET_PROJECTION_ROOT to retain each owned fixture's real state for review.
The production begin/frozen-source/verify/finalize/cleanup paths are not mocked.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('projection_pipeline', SOURCE / 'pipeline.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def caught(action):
    try:
        action()
    except BaseException as error:
        return error
    return None


@contextlib.contextmanager
def fixture(name):
    retained = os.environ.get('BUDGET_PROJECTION_ROOT')
    with contextlib.ExitStack() as stack:
        if retained:
            Path(retained).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix=name + '-', dir=retained))
        else:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=name + '-')))
        automation = root / 'automation'
        automation.mkdir()
        for filename in ('pipeline.py', 'run_budget.py', 'config.json'):
            shutil.copyfile(SOURCE / filename, automation / filename)
        cache = root / '.automation'
        source = {'repository': 'pubky/paykit-rs', 'kind': 'tag', 'ref': 'projection-unit-fixture'}
        p.save_json(root / 'docs-generation.json', {'schemaVersion': 1, 'source': source, 'defaultVersion': 'coming-soon'})
        selected = {**source, 'qualifiedRef': 'refs/tags/' + source['ref'], 'object': 'b' * 40, 'commit': 'a' * 40}
        lock = {'repository': source['repository'], 'defaultVersion': 'coming-soon', 'selectedSource': selected,
                'coming-soon': {**{k: v for k, v in selected.items() if k != 'repository'},
                    'tag': source['ref'], 'label': source['ref'],
                    'sourceUrl': 'https://github.com/pubky/paykit-rs/tree/' + 'a' * 40},
                'released': {'commit': 'c' * 40}}
        p.save_json(cache / 'deployment.json', {'stableDevelopmentUrl': 'https://fixture.vercel.app'})
        module = p.budget_module()
        for attr, value in (('ROOT', root), ('CACHE', cache), ('CONFIG', automation / 'config.json')):
            stack.enter_context(patch.object(p, attr, value))
        stack.enter_context(patch.object(p, 'budget_module', return_value=module))
        stack.enter_context(patch.object(p, 'resolve_sources', return_value=lock))
        stack.enter_context(patch.object(p, 'tool_versions', return_value={'offline-fixture': '1'}))
        stack.enter_context(patch.object(p, 'credential_health', return_value={'warnings': []}))
        stack.enter_context(patch.object(p, 'generate_docs', side_effect=AssertionError('generation forbidden')))
        yield root, cache, lock, module


def begin():
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
        p.begin_run(p.argparse.Namespace(reuse_candidate=False, force=False))
    return json.loads(output.getvalue())


class BudgetProjectionFailureTests(unittest.TestCase):
    def test_begin_primary_identity_survives_physical_terminal_projection_obstruction(self):
        for error_type in (ValueError, SystemExit, KeyboardInterrupt):
            with self.subTest(error_type=error_type.__name__), fixture('primary-physical') as (_, cache, _, module):
                primary = error_type('PRIMARY public source fixture failure')
                obstruction = []
                def source_fault():
                    run = next((cache / 'runs').iterdir())
                    path = run / 'budget-report.json'
                    path.unlink()
                    path.mkdir()
                    obstruction.append(path)
                    raise primary
                with patch.object(p, 'resolve_sources', side_effect=source_fault):
                    error = caught(begin)
                self.assertEqual(len(obstruction), 1, 'source fault was not reached')
                self.assertTrue(obstruction[0].is_dir(), 'physical fault must remain present')
                run = obstruction[0].parent
                state = p.read_json(run / 'budget-state.json')
                self.assertFalse((cache / 'run.lock').exists(), 'owned lease must be released')
                self.assertEqual(p.read_json(run / 'run.json')['status'], 'failed')
                self.assertEqual(state['outcome'], 'failed')
                self.assertIsNotNone(state['endedAt'])
                self.assertEqual(state['observations'][0]['status'], 'failed')
                self.assertEqual(state['observations'][0]['details']['errorType'], error_type.__name__)
                self.assertGreater(state['observations'][0]['endedAt'], state['observations'][0]['startedAt'])
                self.assertFalse((cache / 'last-success.json').exists())
                self.assertIs(error, primary, 'terminal report failure replaced the exact primary exception')
                self.assertIn('Budget', '\n'.join(getattr(primary, '__notes__', [])))
                obstruction[0].rmdir()
                recovered = module.report(cache, run.name)
                self.assertEqual(recovered['outcome'], 'failed')
                self.assertEqual(recovered['deadline'], state['deadline'])

    def test_begin_primary_failure_control_and_uncertain_child_cleanup_remain_distinct(self):
        for cleanup_fault in (False, True):
            with self.subTest(cleanup_fault=cleanup_fault), fixture('cleanup-control') as (_, cache, _, _):
                primary = ValueError('PRIMARY source blocker')
                cleanup = ValueError('Cannot prove expired child ownership; lease cannot be reclaimed')
                with patch.object(p, 'resolve_sources', side_effect=primary):
                    if cleanup_fault:
                        with patch.object(p, 'cleanup_children', side_effect=cleanup):
                            error = caught(begin)
                    else:
                        error = caught(begin)
                self.assertIs(error, cleanup if cleanup_fault else primary)
                self.assertEqual((cache / 'run.lock').exists(), cleanup_fault)
                run = next((cache / 'runs').iterdir())
                self.assertEqual(p.read_json(run / 'budget-state.json')['outcome'], 'running' if cleanup_fault else 'failed')
                self.assertFalse((cache / 'last-success.json').exists())

    def test_admission_durable_state_precedes_recoverable_json_or_text_projection_loss(self):
        for suffix in ('json', 'txt'):
            with self.subTest(suffix=suffix), fixture('admission-projection') as (_, cache, _, module):
                real_write = module._write
                faults = []
                admission = []
                def obstruct_projection(path, value):
                    path = Path(path)
                    if path.name == 'budget-report.' + suffix:
                        if not faults:
                            owner = p.read_json(cache / 'run.lock/owner.json')
                            state = p.read_json(path.parent / 'budget-state.json')
                            durable_run = p.read_json(path.parent / 'run.json')
                            self.assertEqual((owner['id'], durable_run['id'], state['runId']), (path.parent.name,) * 3)
                            self.assertEqual(state['outcome'], 'running')
                            self.assertEqual(state['observations'], [], 'not the admission projection boundary')
                            admission.append(owner)
                            path.mkdir()
                        faults.append(str(path))
                    return real_write(path, value)
                output = io.StringIO()
                def cli_begin():
                    with patch.object(p.sys, 'argv', ['pipeline.py', 'begin']), \
                         contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                        p.main()
                with patch.object(module, '_write', side_effect=obstruct_projection):
                    error = caught(cli_begin)
                self.assertTrue(faults, 'admission projection never attempted')
                self.assertIsNone(error, 'recoverable projection loss stranded a newly owned lease')
                envelope = json.loads(output.getvalue())
                run_id = envelope['runId']
                run = cache / 'runs' / run_id
                self.assertEqual(envelope['action'], 'update')
                self.assertEqual(envelope['budgetSummary']['runId'], run_id)
                self.assertTrue(envelope['budgetSummary']['projectionErrors'], 'degradation must be explicit')
                self.assertEqual(envelope['budgetSummary']['projectionErrors'][0]['errorType'], 'IsADirectoryError')
                self.assertEqual(p.guard_run(run_id)['deadline'], admission[0]['deadline'])
                self.assertEqual(p.guard_run(run_id)['repairs'], 0)
                self.assertEqual(envelope['budgetSummary']['policySnapshot'], p.read_json(p.CONFIG)['budget'])
                state = p.read_json(run / 'budget-state.json')
                self.assertEqual(state['observations'][0]['status'], 'completed')
                self.assertIsNotNone(state['observations'][0]['endedAt'])
                self.assertFalse((cache / 'last-success.json').exists())
                with self.assertRaisesRegex(ValueError, 'already active'):
                    begin()
                obstruction = run / ('budget-report.' + suffix)
                self.assertTrue(obstruction.is_dir())
                obstruction.rmdir()
                p.release_lease(run_id, 'failed', {'reason': 'offline unit fixture complete'})
                retried = begin()
                self.assertNotEqual(retried['runId'], run_id)
                p.release_lease(retried['runId'], 'failed')
                self.assertEqual(p.read_json(run / 'budget-state.json')['outcome'], 'failed')

    def test_no_change_cli_survives_both_projection_obstructions_without_success_changes(self):
        with fixture('no-change-projection') as (root, cache, lock, module):
            tools = {'offline-fixture': '1'}
            fingerprint = p.input_fingerprint(lock, tools=tools)
            previous = {'verifiedDeployment': True, 'sources': lock, 'fingerprint': fingerprint,
                        'processFingerprint': p.process_fingerprint(tools=tools),
                        'regenerationFingerprint': p.regeneration_fingerprint(lock, tools=tools),
                        'deploymentUrl': 'https://fixture.vercel.app'}
            success = cache / 'last-success.json'
            p.save_json(success, previous)
            marker = root / 'public/untouched.txt'
            marker.parent.mkdir()
            marker.write_bytes(b'public fixture must remain unchanged')
            before = (success.read_bytes(), success.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns)
            real_write = module._write
            faults = []
            def obstruct(path, value):
                path = Path(path)
                if path.name in ('budget-report.json', 'budget-report.txt'):
                    if not path.exists():
                        path.mkdir()
                    faults.append(path.name)
                return real_write(path, value)
            output = io.StringIO()
            with patch.object(module, '_write', side_effect=obstruct), \
                 patch.object(p, 'get_json', return_value={'fingerprint': fingerprint}), \
                 patch.object(p.sys, 'argv', ['pipeline.py', 'begin']), contextlib.redirect_stdout(output):
                p.main()
            envelope = json.loads(output.getvalue())
            report = envelope['budgetSummary']
            self.assertEqual(envelope['action'], 'no-change')
            self.assertEqual(report['outcome'], 'no-change')
            self.assertEqual({error['file'] for error in report['projectionErrors']},
                             {'budget-report.json', 'budget-report.txt'})
            self.assertEqual(set(faults), {'budget-report.json', 'budget-report.txt'})
            self.assertEqual(report['policySnapshot'], p.read_json(p.CONFIG)['budget'])
            self.assertTrue(all(row['status'] == 'completed' and row['endedAt'] is not None for row in report['observations']))
            self.assertFalse((cache / 'run.lock').exists())
            self.assertEqual(before, (success.read_bytes(), success.stat().st_mtime_ns, marker.read_bytes(), marker.stat().st_mtime_ns))

    def test_terminal_close_acknowledgement_loss_preserves_primary_identity(self):
        for error_type in (ValueError, SystemExit, KeyboardInterrupt):
            with self.subTest(error_type=error_type.__name__), fixture('close-lost-ack') as (_, cache, _, module):
                primary = error_type('PRIMARY source fixture error')
                close = module.close
                secondary = OSError('SECONDARY terminal telemetry acknowledgement lost after real close')
                def lose_ack(*args, **kwargs):
                    close(*args, **kwargs)
                    raise secondary
                with patch.object(module, 'close', side_effect=lose_ack), \
                     patch.object(p, 'resolve_sources', side_effect=primary):
                    error = caught(begin)
                self.assertIs(error, primary)
                self.assertIn(str(secondary), '\n'.join(primary.__notes__))
                run = next((cache / 'runs').iterdir())
                self.assertEqual(p.read_json(run / 'budget-state.json')['outcome'], 'failed')
                self.assertFalse((cache / 'run.lock').exists())

    def test_transient_finish_or_close_projection_loss_is_not_lost_from_primary_diagnosis(self):
        for boundary in ('finish', 'close'):
            with self.subTest(boundary=boundary), fixture('transient-primary') as (_, cache, _, module):
                primary = ValueError('PRIMARY source fixture blocker')
                write = module._write
                faults = []
                def one_physical_fault(path, value):
                    path = Path(path)
                    if path.name == 'budget-report.json' and not faults:
                        state = p.read_json(path.parent / 'budget-state.json')
                        at_boundary = (state['outcome'] == 'failed' if boundary == 'close' else
                                       state['outcome'] == 'running' and state['observations'] and
                                       state['observations'][0]['endedAt'] is not None)
                        if at_boundary:
                            path.unlink()
                            path.mkdir()
                            faults.append(path.name)
                            try:
                                return write(path, value)
                            finally:
                                path.rmdir()  # Fault disappears before terminal report regeneration.
                    return write(path, value)
                with patch.object(module, '_write', side_effect=one_physical_fault), \
                     patch.object(p, 'resolve_sources', side_effect=primary):
                    error = caught(begin)
                self.assertEqual(faults, ['budget-report.json'])
                self.assertIs(error, primary)
                self.assertIn('budget-report.json', '\n'.join(getattr(primary, '__notes__', [])),
                              'a later successful projection erased the secondary diagnosis')
                run = next((cache / 'runs').iterdir())
                self.assertEqual(p.read_json(run / 'budget-state.json')['outcome'], 'failed')
                self.assertFalse((cache / 'run.lock').exists())

    def test_real_unproven_child_cleanup_is_not_downgraded_to_report_degradation(self):
        with fixture('unproven-child') as (root, cache, _, _):
            primary = ValueError('PRIMARY source fixture blocker')
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     cwd=root, start_new_session=True)
            try:
                def source_fault():
                    owner = p.read_json(cache / 'run.lock/owner.json')
                    owner['children'] = [{'pid': child.pid, 'start': p.process_identity(child.pid) + '-unproven'}]
                    p.save_json(cache / 'run.lock/owner.json', owner)
                    path = cache / 'runs' / owner['id'] / 'budget-report.json'
                    path.unlink()
                    path.mkdir()
                    raise primary
                with patch.object(p, 'resolve_sources', side_effect=source_fault):
                    error = caught(begin)
                self.assertIsInstance(error, ValueError)
                self.assertIsNot(error, primary)
                self.assertIn('Cannot prove expired child ownership', str(error))
                self.assertIsNone(child.poll(), 'uncertain owner must not be killed by pipeline cleanup')
                self.assertTrue((cache / 'run.lock').exists())
                run = next((cache / 'runs').iterdir())
                self.assertEqual(p.read_json(run / 'budget-state.json')['outcome'], 'running')
                self.assertFalse((cache / 'last-success.json').exists())
            finally:
                child.terminate()  # The test's own exact Popen, not an unproven record.
                child.wait(timeout=5)

    def test_state_write_and_precheckpoint_acceptance_failures_are_not_optional_projection_loss(self):
        with fixture('state-write-control') as (_, cache, _, module):
            run_id = begin()['runId']
            run = cache / 'runs' / run_id
            path = run / 'budget-state.json'
            before = path.read_bytes()
            path.unlink()
            path.mkdir()
            called = []
            try:
                error = caught(lambda: p.execute_stage(run_id, 'generate', lambda: called.append(True)))
                self.assertIsInstance(error, IsADirectoryError)
                self.assertEqual(called, [])
                self.assertTrue((cache / 'run.lock').exists())
                self.assertFalse((cache / 'last-success.json').exists())
            finally:
                path.rmdir()
                path.write_bytes(before)
        with fixture('acceptance-drift-control') as (root, cache, _, _):
            run_id = begin()['runId']
            helper = root / 'automation/run_budget.py'
            helper.write_bytes(helper.read_bytes() + b'\n# synthetic acceptance drift\n')
            path = cache / 'runs' / run_id / 'budget-report.json'
            path.unlink()
            path.mkdir()
            with patch.object(p, 'fork_api', side_effect=AssertionError('drift reached remote acceptance')):
                error = caught(lambda: p.verify_publication(run_id))
            self.assertIsInstance(error, ValueError)
            self.assertIn('Acceptance policy changed', str(error))
            self.assertFalse((cache / 'last-success.json').exists())
            owner = p.read_json(cache / 'run.lock/owner.json')
            self.assertIs(owner['acceptanceInvalidated'], True)
            self.assertTrue(owner.get('id') == run_id)

    def test_sigterm_with_projection_obstruction_keeps_real_exit_and_failed_partial_state(self):
        with fixture('sigterm-parent') as (root, _, _, _):
            ready = root / 'ready.json'
            program = '''import importlib.util,json,pathlib,sys,time
s=importlib.util.spec_from_file_location('signal_fixture',sys.argv[1]);t=importlib.util.module_from_spec(s);s.loader.exec_module(t)
ready=pathlib.Path(sys.argv[2])
with t.fixture('sigterm-child') as (root,cache,lock,module):
 def source_fault():
  run=next((cache/'runs').iterdir());path=run/'budget-report.json';path.unlink();path.mkdir()
  ready.write_text(json.dumps({'cache':str(cache),'runId':run.name}))
  time.sleep(30)
 t.p.resolve_sources=source_fault
 sys.argv=['pipeline.py','begin'];t.p.main()
'''
            child = subprocess.Popen([sys.executable, '-c', program, str(Path(__file__).resolve()), str(ready)],
                                     cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=dict(os.environ, BUDGET_PROJECTION_ROOT=str(root / 'signal-owned-roots')))
            try:
                until = time.monotonic() + 5
                while not ready.exists() and child.poll() is None and time.monotonic() < until:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), 'real begin did not reach obstructed source boundary')
                child.send_signal(signal.SIGTERM)
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 143, stderr.decode())
                self.assertEqual(stdout, b'')
                info = json.loads(ready.read_text())
                cache = Path(info['cache'])
                state = p.read_json(cache / 'runs' / info['runId'] / 'budget-state.json')
                self.assertEqual(state['outcome'], 'failed')
                self.assertEqual(state['observations'][0]['details']['errorType'], 'SystemExit')
                self.assertIsNotNone(state['observations'][0]['endedAt'])
                self.assertFalse((cache / 'run.lock').exists())
                self.assertIn(b'Budget report projection failed', stderr)
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_verified_checkpoint_survives_json_or_text_projection_failure(self):
        for suffix in (None, 'json', 'txt'):
            with self.subTest(suffix=suffix), fixture('verified-projection') as (root, cache, _, module):
                run_id = begin()['runId']
                run = cache / 'runs' / run_id
                checkpoint_path = cache / 'last-success.json'
                manifest = {'fingerprint': 'synthetic-unit-provenance'}
                p.save_json(root / 'public/docs-manifest.json', manifest)
                p.save_json(cache / 'pending-publication.json',
                            {'runId': run_id, 'sha': 'd' * 40, 'fingerprint': manifest['fingerprint']})
                api_calls = []
                def api(endpoint):
                    api_calls.append(endpoint)
                    if endpoint.startswith('deployments?'):
                        return [{'id': 1, 'environment': 'unit-fixture'}]
                    if endpoint == 'deployments/1/statuses?per_page=100':
                        return [{'state': 'success', 'environment_url': 'https://fixture.vercel.app'}]
                    if endpoint == 'branches/docs-automation':
                        return {'commit': {'sha': 'd' * 40}}
                    raise AssertionError('Unexpected offline boundary: ' + endpoint)
                faults = []
                real_write, real_guard = module._write, p.guard_run
                def fault_after_commit(path, value):
                    path = Path(path)
                    if suffix and path.name == 'budget-report.' + suffix and checkpoint_path.exists():
                        self.assertFalse((cache / 'run.lock').exists())
                        self.assertEqual(p.read_json(run / 'run.json')['status'], 'published')
                        if not faults:
                            path.unlink()
                            path.mkdir()
                        faults.append(str(path))
                    return real_write(path, value)
                def guard_before_commit_only(*args, **kwargs):
                    self.assertFalse(checkpoint_path.exists(), 'post-commit guard added')
                    return real_guard(*args, **kwargs)
                output = io.StringIO()
                with patch.object(module, '_write', side_effect=fault_after_commit), \
                     patch.object(p, 'guard_run', side_effect=guard_before_commit_only), \
                     patch.object(p, 'fork_api', side_effect=api), \
                     patch.object(p, 'get_json', return_value=manifest), contextlib.redirect_stdout(output):
                    error = caught(lambda: p.verify_publication(run_id))
                checkpoint = p.read_json(checkpoint_path)
                self.assertEqual(checkpoint['runId'], run_id)
                self.assertIs(checkpoint['verifiedDeployment'], True, 'real finalization not reached')
                self.assertEqual(len(api_calls), 3, 'acceptance was replayed')
                self.assertEqual(bool(faults), suffix is not None, 'projection fault was not triggered')
                self.assertIsNone(error, 'post-checkpoint projection failure changed accepted verification')
                envelope = json.loads(output.getvalue())
                state = p.read_json(run / 'budget-state.json')
                self.assertEqual(state['outcome'], 'published')
                self.assertIsNotNone(state['endedAt'])
                self.assertTrue(all(row['endedAt'] is not None and row['status'] == 'completed'
                                    for row in state['observations']))
                self.assertGreaterEqual(state['endedAt'], max(row['endedAt'] for row in state['observations']))
                self.assertEqual(envelope['published'], 'd' * 40)
                self.assertEqual(envelope['budgetSummary']['outcome'], 'published')
                self.assertIs(envelope['budgetSummary']['publicationApprovalGranted'], False)
                if suffix:
                    self.assertTrue(envelope['budgetSummary']['projectionErrors'])
                    obstruction = run / ('budget-report.' + suffix)
                    self.assertTrue(obstruction.is_dir())
                    obstruction.rmdir()
                checkpoint_bytes = checkpoint_path.read_bytes()
                recovered = module.report(cache, run_id)
                self.assertEqual(recovered['outcome'], 'published')
                self.assertEqual(recovered.get('projectionErrors', []), [])
                self.assertEqual(checkpoint_path.read_bytes(), checkpoint_bytes)
                self.assertFalse((cache / 'run.lock').exists())
                self.assertEqual(p.read_json(run / 'budget-report.json')['outcome'], 'published')
                self.assertIn('published', (run / 'budget-report.txt').read_text())


if __name__ == '__main__':
    unittest.main()
