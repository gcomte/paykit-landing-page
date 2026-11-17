"""Safe memory/result-channel observations. Diagnostic filesystem resource retired."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from automation.tests import test_hermes_coordinator as rt
c = rt.c


def channel_assignment(role='author', **changes):
    return dict(dict(runId='channel-run', workerId='channel-worker', role=role,
        deadline=time.time()+60, artifactHash=None if role == 'author' else 'a'*64,
        version='released' if role == 'walker' else None, audience='wallet' if role == 'walker' else None), **changes)


class ResultChannelTests(unittest.TestCase):
    def test_terminal_metadata_crosses_existing_channel_after_cleanup_without_diagnostic_files(self):
        from unittest.mock import Mock
        import sys
        with patch.object(c, 'worker_diagnostics_root', create=True,
                          side_effect=AssertionError('diagnostic filesystem is forbidden')) as root:
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_submit=False)
        envelope = json.loads(state['output'])
        self.assertTrue(state['closed'])
        self.assertTrue(state['restoration_attempted'])
        self.assertEqual(envelope['diagnostic']['outcome'], 'normal_return_incomplete')
        self.assertEqual(envelope['diagnostic']['completion']['observed'], [])
        root.assert_not_called()
        collector = c.WorkerOutcomeCollector()
        a = dict(runId='channel-run', role='author', workerId='channel-worker',
                 deadline=time.time()+60, artifactHash=None, version=None, audience=None)
        proc = Mock(pid=123456789, returncode=1)
        proc.communicate.return_value = (state['output'], None)
        proc.poll.return_value = 1
        with patch.object(c, 'project_interpreter', return_value=sys.executable), \
             patch.object(c.subprocess, 'Popen', return_value=proc), patch.object(c.os, 'killpg'):
            with self.assertRaises(c.CoordinatorError) as caught:
                c.fresh_worker(a, collector=collector)
        self.assertNotIsInstance(caught.exception, c.OwnedCleanupError)
        self.assertEqual(collector.report()['sessions'][0]['child'], envelope['diagnostic'])
        self.assertEqual(collector.report()['sessions'][0]['cleanup'], 'reaped')


    def test_transport_read_failure_without_terminal_never_repairs(self):
        import sys
        from unittest.mock import Mock
        for failure in (OSError('PRIVATE_READ_CANARY'), UnicodeError('PRIVATE_READ_CANARY')):
            bridge = rt.FixtureBridge()
            collector = c.WorkerOutcomeCollector()
            proc = Mock(pid=123456789)
            proc.communicate.side_effect = failure
            proc.poll.return_value = 1
            def worker(a): return c.fresh_worker(a, collector=collector)
            with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                 patch.object(c.subprocess, 'Popen', return_value=proc) as popen, patch.object(c.os, 'killpg'):
                with self.assertRaises(c.OwnedCleanupError):
                    c.Coordinator(bridge, worker, lambda: None, no_publish=True).run()
            self.assertEqual(popen.call_count, 1)
            self.assertNotIn('repair', [op for op, _ in bridge.events])
            self.assertIsNone(collector.report()['sessions'][0]['child'])
            self.assertNotIn('PRIVATE_', c.canonical_json(collector.report()))


    def test_strict_terminal_stream_faults_are_nonrepairable_and_never_start_worker_two(self):
        import sys
        from unittest.mock import Mock
        streams = ['', '{', '{}', '[]', '{"ok":true}{}', '{"ok":true} trailing',
            '{"ok":true,"ok":true}', '{"ok":true,"diagnostic":{"x":1,"x":2}}',
            '{"ok":true,"diagnostic":NaN}', '{"ok":true,"diagnostic":Infinity}',
            '{"ok":true,"diagnostic":{"private":"'+'x'*100001+'"}}',
            '{"ok":true,"diagnostic":{"private":"'+'é'*50000+'"}}',
            '{"ok":true,"private":"PRIVATE_CORE_CANARY"}', '{"ok":1}',
            '{"ok":false,"error":"public worker failed","fatalOwnedCleanup":1}']
        for stream in streams:
            with self.subTest(streamLength=len(stream)):
                proc = Mock(pid=123456789, returncode=0)
                proc.communicate.return_value = (stream, None)
                proc.poll.return_value = 0
                collector, bridge = c.WorkerOutcomeCollector(), rt.FixtureBridge()
                with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                     patch.object(c.subprocess, 'Popen', return_value=proc) as spawned, patch.object(c.os, 'killpg'):
                    with self.assertRaises(c.OwnedCleanupError):
                        c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
                self.assertEqual(spawned.call_count, 1)
                self.assertNotIn('repair', [op for op, _ in bridge.events])
                entry = collector.report()['sessions'][0]
                self.assertEqual(entry['channel'], 'protocol_error')
                self.assertIsNone(entry['child'])
                self.assertNotIn('PRIVATE_', c.canonical_json(collector.report()))

    def test_fatal_core_precedes_optional_diagnostic_and_no_metadata_can_grant_approval(self):
        import copy, sys
        from unittest.mock import Mock
        a = channel_assignment()
        valid = c.WorkerDiagnostics(a).finish()
        variants = [None, valid, False, {'private': 'PRIVATE_D_CANARY'},
                    dict(valid, private='PRIVATE_D_CANARY'), dict(valid, private='x'*9000)]
        for diagnostic in variants:
            for kind in ('fatal', 'ordinary', 'success'):
                core = ({'ok': True} if kind == 'success' else {'ok': False, 'error': 'public worker failed'})
                if kind == 'fatal': core['fatalOwnedCleanup'] = True
                envelope = dict(core)
                if diagnostic is not None: envelope['diagnostic'] = diagnostic
                proc = Mock(pid=123456789, returncode=0 if kind == 'success' else 1)
                proc.communicate.return_value = (json.dumps(envelope), None)
                proc.poll.return_value = proc.returncode
                collector = c.WorkerOutcomeCollector()
                with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                     patch.object(c.subprocess, 'Popen', return_value=proc), patch.object(c.os, 'killpg'):
                    if kind == 'success': self.assertEqual(c.fresh_worker(a, collector=collector), core)
                    else:
                        with self.assertRaises(c.CoordinatorError) as caught:
                            c.fresh_worker(a, collector=collector)
                        self.assertEqual(isinstance(caught.exception, c.OwnedCleanupError), kind == 'fatal')
                self.assertEqual(collector.report()['sessions'][0]['channel'], 'terminal_'+('ok' if kind == 'success' else 'error' if kind == 'ordinary' else 'fatal'))
                self.assertNotIn('PRIVATE_', c.canonical_json(collector.report()))
        # Boolean or exit-code mismatch cannot authenticate any clean core.
        for role, core, exit_code in [('author', {'ok': True}, 1),
                ('author', {'ok': False, 'error': 'public worker failed'}, 0),
                ('reviewer', {'ok': True}, 0), ('reviewer', {'ok': True, 'reviewPassed': 1}, 0),
                ('author', {'ok': True, 'reviewPassed': True}, 0)]:
            with self.assertRaises(c.OwnedCleanupError): c.parse_worker_terminal(json.dumps(core), exit_code, role)
        reviewer = c.WorkerDiagnostics(channel_assignment('reviewer')).finish()
        reviewer['review'] = {'saved': True, 'verdict': 'pass'}
        reviewer['completion']['checked'] = True
        core, channel, _, _ = c.parse_worker_terminal(c.canonical_json(
            dict(ok=True, reviewPassed=False, diagnostic=reviewer)), 0, 'reviewer')
        self.assertFalse(core['reviewPassed'])

    def test_invalid_diagnostic_fields_are_discarded_before_any_serializer(self):
        import copy
        base = c.WorkerDiagnostics(channel_assignment()).finish()
        faults = [('schemaVersion', True), ('schemaVersion', 1), ('phase', 'model'),
                  ('lastPhase', 'PRIVATE_CANARY'), ('elapsedMs', True), ('elapsedMs', -1),
                  ('remainingMs', 3500001), ('modelStarted', 1), ('fatalCleanup', 1),
                  ('outcome', 'PRIVATE_CANARY'), ('sdk.apiCalls', True), ('sdk.apiCalls', 1000001),
                  ('sdk.completed', 1), ('sdk.exitReason', 'PRIVATE_CANARY'),
                  ('completion.checked', 1), ('completion.required', []),
                  ('completion.observed', ['paykit_submit_blueprint:released']*2),
                  ('completion.pendingMutations.released', -1), ('review.saved', 1),
                  ('review.verdict', 'PRIVATE_CANARY'), ('operations.other.started', None)]
        attempted = []
        original = c.json.dumps
        def dumps(value, *args, **kwargs):
            attempted.append(value)
            return original(value, *args, **kwargs)
        for path, value in faults:
            invalid = copy.deepcopy(base)
            target = invalid
            keys = path.split('.')
            for key in keys[:-1]: target = target[key]
            target[keys[-1]] = value
            stream = original({'ok': True, 'diagnostic': invalid})
            with patch.object(c.json, 'dumps', dumps):
                core, channel, child, status = c.parse_worker_terminal(stream, 0, 'author')
            self.assertEqual(core, {'ok': True})
            self.assertIsNone(child)
            self.assertEqual(status, 'malformed')
        self.assertEqual(attempted, [], 'rejected diagnostic must never reach a serializer')

    def test_nonblocking_capture_uses_prior_immutable_projection_under_either_held_lock(self):
        import threading
        for kind in ('diagnostic', 'completion'):
            a = channel_assignment()
            d = c.WorkerDiagnostics(a)
            broker = c.WorkerTools(a, lambda *args, **kwargs: {}, d)
            d.broker = broker
            d.mark('model')
            before = d.latest
            held, release = threading.Event(), threading.Event()
            lock = d.lock if kind == 'diagnostic' else broker.completion_lock
            def callback():
                with lock:
                    held.set()
                    release.wait(5)
            thread = threading.Thread(target=callback)
            thread.start()
            self.assertTrue(held.wait(3))
            try:
                start = time.monotonic()
                record = d.finish(c.OwnedCleanupError('PRIVATE_FATAL_CANARY'))
                self.assertLess(time.monotonic()-start, .5)
                self.assertTrue(record['diagnosticsFailed'])
                self.assertTrue(record['fatalCleanup'])
                self.assertFalse(record['completion']['checked'])
                self.assertEqual(record['completion']['observed'], [])
                self.assertIsInstance(before, bytes)
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_unknown_contradictory_sdk_and_privacy_canaries_never_reach_attempted_projection_serialization(self):
        canary = 'PRIVATE_CANARY_é_\x00_\udfff'
        attempts = []
        original = c.canonical_json
        def canonical(value):
            attempts.append(json.dumps(value, ensure_ascii=True))
            return original(value)
        cases = [{'completed': True, 'failed': True}, {'completed': True, 'interrupted': True},
                 {'completed': True, 'error': canary}, {'completed': []}, {'failed': {}},
                 {'error': {'nested': [canary]}, 'api_calls': True}, {'interrupted': 0}]
        with patch.object(c, 'canonical_json', canonical):
            for result in cases:
                state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_submit=False,
                    model_result=dict(result, messages=[{'query': canary}], turn_exit_reason=canary))
                record = json.loads(state['output'])['diagnostic']
                self.assertEqual(record['sdk']['status'], 'unknown')
                self.assertFalse(record['completion']['checked'])
            private_type = type('PRIVATE_CANARY_EXCEPTION_TYPE', (RuntimeError,), {})
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, interrupt=private_type(canary))
            self.assertNotIn('PRIVATE_CANARY', state['output'])
        self.assertTrue(attempts)
        self.assertTrue(all('PRIVATE_CANARY' not in attempt for attempt in attempts))

    def test_actual_fixed_schema_worst_width_fits_per_session_and_whole_report_caps(self):
        import copy
        worst = None
        for role in ('author', 'reviewer', 'walker'):
            d = c.WorkerDiagnostics(channel_assignment(role)).finish()
            d.update(elapsedMs=1000000000, remainingMs=3500000, lastPhase=max(c.DIAGNOSTIC_PHASES, key=len),
                outcome=max(c.DIAGNOSTIC_OUTCOMES, key=len), lastOperation=max(c.TOOL_FIELDS, key=len),
                lastOperationOutcome='succeeded')
            for key in ('modelStarted', 'modelReturned', 'fatalCleanup', 'diagnosticsFailed'): d[key] = False
            d['preload'] = dict.fromkeys(d['preload'], False)
            d['sdk'] = dict(completed=False, failed=False, interrupted=False, errorPresent=False,
                            apiCalls=1000000, exitReason='unknown', status='interrupted')
            for item in d['operations'].values(): item.update(started=1000000, succeeded=1000000, failed=1000000)
            d['completion'].update(known=False, checked=False, observed=d['completion']['required'][:],
                pendingMutations=dict.fromkeys(c.VERSIONS, 1000000), mutationGenerations=dict.fromkeys(c.VERSIONS, 1000000))
            d['review'] = {'saved': False, 'verdict': 'unknown'}
            c.validate_diagnostic(d, role)
            size = len(c.canonical_json(d).encode('ascii'))
            worst = max(worst or 0, size)
            entry = dict(session=23, scope=7, role=role, version='coming-soon', audience='point-of-sale',
                channel='terminal_fatal', cleanup='unknown', diagnosticStatus='valid', child=d)
            self.assertLessEqual(len(c.canonical_json(entry)), 2560)
        self.assertLessEqual(worst, 2234)
        collector = c.WorkerOutcomeCollector()
        for i in range(24):
            entry = collector.reserve(channel_assignment('walker', version=c.VERSIONS[i%2], audience=c.AUDIENCES[(i//2)%2]))
            entry.update(child=copy.deepcopy(d), channel='terminal_ok', cleanup='reaped', diagnosticStatus='valid')
            collector.finalize(entry)
        report = collector.report()
        self.assertEqual(len(report['sessions']), 24)
        self.assertLessEqual(len(c.canonical_json({'workerOutcomes': report})), 65536)
        with patch.object(c.subprocess, 'Popen') as spawned:
            with self.assertRaises(c.OwnedCleanupError): c.fresh_worker(channel_assignment(), collector=collector)
        spawned.assert_not_called()
        self.assertEqual(len(collector.report()['sessions']), 24)
        # A schema/encoder-size regression discards only child observations.
        entry['child'] = d
        original = c.canonical_json
        with patch.object(c, 'canonical_json', side_effect=lambda value: ' '*2561 if value is entry else original(value)):
            collector.finalize(entry)
        self.assertIsNone(entry['child'])
        self.assertEqual(entry['diagnosticStatus'], 'projection_too_large')


    def test_collector_rejects_private_or_malformed_child_before_attempted_serialization(self):
        collector = c.WorkerOutcomeCollector()
        entry = collector.reserve(channel_assignment())
        entry['child'] = {'private': 'PRIVATE_COLLECTOR_CANARY'}
        attempted = []
        original = c.canonical_json
        def encode(value):
            attempted.append(json.dumps(value))
            return original(value)
        with patch.object(c, 'canonical_json', encode):
            collector.finalize(entry)
            final = collector.report()
        self.assertIsNone(entry['child'])
        self.assertEqual(entry['diagnosticStatus'], 'malformed')
        self.assertTrue(all('PRIVATE_COLLECTOR_CANARY' not in value for value in attempted))


    def test_optional_parent_serialization_fault_preserves_core_and_records_only_known_loss(self):
        child = c.WorkerDiagnostics(channel_assignment()).finish()
        for core, code in (({'ok': True}, 0), ({'ok': False, 'error': 'public worker failed'}, 1),
                ({'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True}, 1)):
            stream = c.canonical_json(dict(core, diagnostic=child))
            with patch.object(c, 'canonical_json', side_effect=TypeError('PRIVATE_SERIALIZE_CANARY')):
                parsed, channel, diagnostic, status = c.parse_worker_terminal(stream, code, 'author')
            self.assertEqual(parsed, core)
            self.assertIsNone(diagnostic)
            self.assertEqual(status, 'serialization_failed')
        _, _, _, status = c.parse_worker_terminal('{"ok":true}', 0, 'author')
        self.assertEqual(status, 'absent', 'absence cannot be inferred to be a child serialization failure')


    def test_probe_channel_preserves_both_existing_exact_and_deferred_tool_surfaces_without_d(self):
        for role in ('author', 'reviewer', 'walker'):
            state = rt.RuntimeTests().native_fixture(probe=True, protocol=True, role=role)
            envelope = json.loads(state['output'])
            self.assertNotIn('diagnostic', envelope)
            self.assertEqual(state['conversations'], 0)
            for names in (sorted(c.ROLE_TOOLS[role]), sorted(c.META_TOOLS)):
                core = dict(envelope, toolNames=names)
                parsed, channel, child, status = c.parse_worker_terminal(c.canonical_json(core), 0, role, probe=True)
                self.assertEqual(parsed, core)
                self.assertEqual(channel, 'terminal_ok')
                self.assertIsNone(child)
                self.assertEqual(status, 'absent')


class CaptureAuthorityTests(unittest.TestCase):
    def test_exact_primary_fatal_survives_all_secondary_cleanup_and_capture_faults(self):
        active = c.OwnedCleanupError('OFFLINE_TRUSTED_FATAL')
        def fatal(*args, **kwargs): raise active
        for close_error, guard_error in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(close=close_error, guard=guard_error):
                caught = []
                original_worker = c._native_worker
                def worker(*args, **kwargs):
                    try: return original_worker(*args, **kwargs)
                    except BaseException as error:
                        caught.append(error)
                        raise
                with patch.object(c, '_native_worker', worker):
                    state = rt.RuntimeTests().native_fixture(probe=False, bridge_override=fatal,
                        close_error=close_error, guard_error=guard_error)
                self.assertTrue(state['closed'])
                self.assertTrue(state['restoration_attempted'])
                self.assertIs(caught[0], active)

    def test_observational_cleanup_fault_does_not_create_fatal_or_prevent_real_close(self):
        original = c.WorkerDiagnostics.mark
        def mark(diagnostic, phase):
            if phase == 'cleanup': raise KeyboardInterrupt('OFFLINE_CAPTURE_CONTROL')
            return original(diagnostic, phase)
        with patch.object(c.WorkerDiagnostics, 'mark', mark):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True)
        self.assertTrue(state['closed'])
        self.assertTrue(state['restoration_attempted'])
        self.assertEqual(state['result'], {'ok': False, 'error': 'public worker failed'})
        self.assertEqual(state['exit'], 1)
        self.assertEqual(state['diagnostics'][0]['outcome'], 'interrupted')
        self.assertFalse(state['diagnostics'][0]['fatalCleanup'])


    def test_partial_stdout_failure_has_one_attempt_and_no_second_document(self):
        import io
        for fatal in (False, True):
            class Partial(io.StringIO):
                attempts = 0
                def write(self, value):
                    self.attempts += 1
                    super().write(value[:17])
                    raise BrokenPipeError('PRIVATE_STDOUT_CANARY')
            output = Partial()
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, protocol_stream=output,
                close_error=fatal)
            self.assertEqual(state['exit'], 1)
            self.assertEqual(output.attempts, 1)
            self.assertEqual(len(state['output']), 17)
            self.assertNotIn('PRIVATE_STDOUT_CANARY', state['output'])

    def test_core_serializer_fault_cannot_downgrade_trusted_fatal(self):
        original = c.canonical_json
        def encode(value):
            if type(value) is dict and 'ok' in value:
                raise KeyboardInterrupt('PRIVATE_SERIALIZER_CANARY')
            return original(value)
        with patch.object(c, 'canonical_json', encode):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, close_error=True)
        self.assertEqual(state['result'], {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
        self.assertNotIn('PRIVATE_', state['output'])

    def test_exact_deadline_or_fatal_survives_secondary_capture_and_parent_teardown_faults(self):
        import sys
        from unittest.mock import Mock
        for active in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_TRUSTED_FATAL')):
            original = c.WorkerDiagnostics.finish
            def finish(d, error=None):
                if error is not None: raise SystemExit('PRIVATE_CAPTURE_CANARY')
                return original(d, error)
            with patch.object(c.WorkerDiagnostics, 'finish', finish):
                state = rt.RuntimeTests().native_fixture(probe=False, interrupt=active)
            self.assertIs(state['errorType'], type(active))
            a = channel_assignment()
            proc = Mock(pid=123456789, returncode=1)
            proc.communicate.side_effect = active
            proc.poll.return_value = 1
            collector = c.WorkerOutcomeCollector()
            with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                 patch.object(c.subprocess, 'Popen', return_value=proc), patch.object(c.os, 'killpg'):
                with self.assertRaises(type(active)) as caught: c.fresh_worker(a, collector=collector)
            self.assertIs(caught.exception, active)
            self.assertEqual(collector.report()['sessions'][0]['cleanup'], 'reaped')
        for boundary in ('kill', 'wait', 'poll'):
            active = c.OwnedCleanupError('PRIVATE_PRIMARY_FATAL')
            proc = Mock(pid=123456789, returncode=1)
            proc.communicate.side_effect = active
            proc.poll.return_value = 1
            if boundary in ('wait', 'poll'): getattr(proc, boundary).side_effect = OSError('PRIVATE_SECONDARY')
            with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                 patch.object(c.subprocess, 'Popen', return_value=proc), \
                 patch.object(c.os, 'killpg', side_effect=OSError('PRIVATE_SECONDARY') if boundary == 'kill' else None):
                with self.assertRaises(c.OwnedCleanupError) as caught: c.fresh_worker(channel_assignment())
            self.assertIs(caught.exception, active)

    def test_holder_survives_setup_guard_entry_outer_stack_alarm_and_stream_failures(self):
        import contextlib
        @contextlib.contextmanager
        def entry_fault(*args, **kwargs):
            raise OSError('PRIVATE_GUARD_ENTRY')
            yield
        original_exit = tempfile.TemporaryDirectory.__exit__
        def directory_exit(manager, *args):
            original_exit(manager, *args)
            raise OSError('PRIVATE_OUTER_DIRECTORY')
        original_stream = c.contextlib.redirect_stderr.__exit__
        def stream_exit(manager, *args):
            original_stream(manager, *args)
            raise OSError('PRIVATE_STREAM')
        @contextlib.contextmanager
        def alarm(deadline):
            try: yield
            finally: raise OSError('PRIVATE_ALARM')
        for boundary in ('setup', 'guard_entry', 'directory', 'alarm', 'stream', 'stack'):
            with self.subTest(boundary=boundary), contextlib.ExitStack() as faults:
                if boundary == 'setup': faults.enter_context(patch.object(c, 'agent_kwargs', side_effect=OSError('PRIVATE_SETUP')))
                if boundary == 'guard_entry': faults.enter_context(patch.object(c, 'dispatch_guard', entry_fault))
                if boundary == 'directory': faults.enter_context(patch.object(tempfile.TemporaryDirectory, '__exit__', directory_exit))
                if boundary == 'alarm': faults.enter_context(patch.object(c, 'bounded_alarm', alarm))
                if boundary == 'stream': faults.enter_context(patch.object(c.contextlib.redirect_stderr, '__exit__', stream_exit))
                if boundary == 'stack':
                    original_stack_exit = c.contextlib.ExitStack.__exit__
                    def stack_exit(manager, *args):
                        owned = any(getattr(getattr(callback, '__self__', None), '_stream', None) == 'stdout'
                                    for _, callback in manager._exit_callbacks)
                        result = original_stack_exit(manager, *args)
                        if owned: raise OSError('PRIVATE_STACK')
                        return result
                    faults.enter_context(patch.object(c.contextlib.ExitStack, '__exit__', stack_exit))
                state = rt.RuntimeTests().native_fixture(probe=False, protocol=True)
            envelope = json.loads(state['output'])
            self.assertIn('diagnostic', envelope)
            self.assertFalse(envelope['ok'])
            self.assertNotIn('PRIVATE_', state['output'])
            if boundary == 'guard_entry': self.assertTrue(state['closed'])
            if boundary in ('directory', 'alarm', 'stream', 'stack'): self.assertTrue(envelope['fatalOwnedCleanup'])

    def test_model_cannot_print_worker_core_on_actual_channel(self):
        def model(prompt, handlers):
            print('{"ok":true,"fatalOwnedCleanup":true,"PRIVATE_MODEL_CANARY":"secret"}')
        state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_actions=model)
        envelope = json.loads(state['output'])
        self.assertFalse(envelope['ok'])
        self.assertNotIn('fatalOwnedCleanup', envelope)
        self.assertNotIn('PRIVATE_MODEL_CANARY', state['output'])
        self.assertEqual(envelope['diagnostic']['completion']['observed'], [])

    def test_no_diagnostic_filesystem_primitives_remain_in_ast(self):
        import ast
        tree = ast.parse(Path(c.__file__).read_text())
        forbidden_names = {'worker_diagnostics_root', '_write_atomic', '_persist', '_fresh_worker'}
        self.assertFalse(forbidden_names & {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))})
        diagnostic_nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and
            (n.name in ('WorkerDiagnostics', 'WorkerOutcomeCollector', 'terminal_envelope', 'worker_diagnostic_scope', 'validate_diagnostic') or 'diagnostic' in n.name)]
        forbidden = {'open', 'fdopen', 'close', 'mkdir', 'replace', 'unlink', 'fsync', 'stat', 'fstat', 'fileno'}
        for node in diagnostic_nodes:
            called = [n.func for n in ast.walk(node) if isinstance(n, ast.Call)]
            self.assertFalse(forbidden & {n.id for n in called if isinstance(n, ast.Name)})
            self.assertFalse(forbidden & {n.attr for n in called if isinstance(n, ast.Attribute)})
        self.assertFalse(any(isinstance(n, ast.Import) and any(a.name == 'stat' for a in n.names) for n in tree.body))

    def test_parent_main_exposes_run_local_collector_after_generic_error_and_no_change_is_silent(self):
        import contextlib, io, sys, types
        from unittest.mock import Mock
        modules = {name: types.ModuleType(name) for name in ('hermes_bootstrap', 'hermes_cli.env_loader')}
        modules['hermes_cli.env_loader'].load_hermes_dotenv = lambda **kwargs: None
        for action in ('update', 'no-change'):
            fixture = rt.FixtureBridge(no_change=action == 'no-change')
            proc = Mock(pid=123456789, returncode=1)
            proc.communicate.return_value = ('{"ok":false,"error":"public worker failed"}', None)
            proc.poll.return_value = 1
            output = io.StringIO()
            with patch.dict(sys.modules, modules), patch.object(c, 'host_lock', side_effect=lambda _: contextlib.nullcontext()), \
                 patch.object(c, 'runtime_attestation', return_value={}), patch.object(c, 'NativeBridge', return_value=fixture), \
                 patch.object(c, 'private_consultation', return_value=None), patch.object(c, 'project_interpreter', return_value=sys.executable), \
                 patch.object(c.subprocess, 'Popen', return_value=proc), patch.object(c.os, 'killpg'), contextlib.redirect_stdout(output):
                result = c.main(['--no-publish'])
            text = output.getvalue()
            if action == 'no-change':
                self.assertEqual(result, 0)
                self.assertEqual(text, '')
            else:
                self.assertEqual(result, 1)
                self.assertTrue(text.startswith('Manual trial failed'))
                self.assertNotIn('CRON', text)
                report = json.loads(text.splitlines()[-1])['workerOutcomes']
                self.assertEqual(len(report['sessions']), 3)
                self.assertTrue(all(s['channel'] == 'terminal_error' and s['child'] is None for s in report['sessions']))


    def test_parent_report_serialization_fault_cannot_replace_primary_failure(self):
        import contextlib, io, sys, types
        modules = {name: types.ModuleType(name) for name in ('hermes_bootstrap', 'hermes_cli.env_loader')}
        modules['hermes_cli.env_loader'].load_hermes_dotenv = lambda **kwargs: None
        output = io.StringIO()
        active = c.OwnedCleanupError('PRIVATE_PRIMARY_FATAL')
        with patch.dict(sys.modules, modules), patch.object(c, 'host_lock', side_effect=lambda _: contextlib.nullcontext()), \
             patch.object(c, 'runtime_attestation', return_value={}), patch.object(c, 'NativeBridge'), \
             patch.object(c.Coordinator, 'run', side_effect=active), \
             patch.object(c.WorkerOutcomeCollector, 'report', side_effect=SystemExit('PRIVATE_REPORT_ERROR')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(c.main(['--no-publish']), 1)
        self.assertEqual(output.getvalue(), 'Manual trial failed; last good deployment retained.\n')


    def test_setup_stdout_is_suppressed_before_trusted_terminal_emission(self):
        original = c.sdk_root
        def root():
            print('{"ok":true,"private":"PRIVATE_SETUP_CANARY"}')
            return original()
        with patch.object(c, 'sdk_root', root):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True)
        self.assertEqual(state['result'], {'ok': True})
        self.assertNotIn('PRIVATE_SETUP_CANARY', state['output'])
        self.assertTrue(state['closed'])


    def test_diagnostic_serialization_fault_from_initial_capture_only_loses_observations(self):
        original = c.canonical_json
        def encode(value):
            if type(value) is dict and 'schemaVersion' in value:
                raise TypeError('PRIVATE_INITIAL_SERIALIZER_CANARY')
            return original(value)
        for submit in (False, True):
            with patch.object(c, 'canonical_json', encode):
                state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_submit=submit)
            self.assertEqual(state['conversations'], 1)
            self.assertTrue(state['closed'])
            self.assertEqual(json.loads(state['output']), {'ok': True} if submit else
                             {'ok': False, 'error': 'public worker failed'})
            self.assertNotIn('PRIVATE_', state['output'])


class WorkerOutcomeDiagnosticsTests(unittest.TestCase):
    def test_normal_return_without_finalizations_is_distinct_and_not_completion(self):
        state = rt.RuntimeTests().native_fixture(probe=False, model_submit=False)
        self.assertIs(state['errorType'], c.CoordinatorError)
        self.assertEqual(state['conversations'], 1)
        self.assertTrue(state['closed'])
        self.assertEqual(len(state['diagnostics']), 1, 'missing safe terminal worker diagnostic')
        record = state['diagnostics'][0]
        self.assertEqual(record['outcome'], 'normal_return_incomplete')
        self.assertTrue(record['modelStarted'])
        self.assertTrue(record['modelReturned'])
        self.assertEqual(record['completion']['required'], ['paykit_submit_blueprint:'+v for v in c.VERSIONS])
        self.assertEqual(record['completion']['observed'], [])
        self.assertFalse(record['completion']['checked'])
        self.assertFalse(record['fatalCleanup'])

    def test_unknown_falsy_sdk_fields_stay_unknown_without_receipts(self):
        cases = [{'error': []}, {'error': {}}, {'error': 0},
                 {'interrupted': None}, {'interrupted': 0}, {'interrupted': ''}]
        for result in cases:
            with self.subTest(result=result):
                state = rt.RuntimeTests().native_fixture(probe=False, model_submit=False, model_result=result)
                self.assertIs(state['errorType'], c.CoordinatorError)
                self.assertEqual(state['conversations'], 1)
                self.assertTrue(state['closed'])
                self.assertTrue(state['restoration_attempted'])
                record = state['diagnostics'][0]
                self.assertEqual(record['completion']['observed'], [])
                self.assertFalse(record['completion']['checked'])
                self.assertEqual(record['sdk']['status'], 'unknown')
                self.assertIsNone(record['sdk']['interrupted'])
                self.assertIs(record['sdk']['errorPresent'], None if 'error' in result else False)
                self.assertEqual(record['outcome'], 'model_result_other')
        state = rt.RuntimeTests().native_fixture(probe=False, model_submit=False,
            model_result={'completed': True, 'interrupted': False, 'error': None})
        self.assertIs(state['errorType'], c.CoordinatorError)
        self.assertEqual(state['diagnostics'][0]['sdk']['status'], 'normal')
        self.assertEqual(state['diagnostics'][0]['outcome'], 'normal_return_incomplete')

    def test_real_receipts_complete_even_when_sdk_classification_is_unknown(self):
        cases = [{'error': []}, {'error': {}}, {'error': 0},
                 {'interrupted': None}, {'interrupted': 0}, {'interrupted': ''}]
        normal = {'completed': True, 'interrupted': False, 'error': None, 'api_calls': 2}
        for result in [*cases, normal]:
            with self.subTest(result=result):
                state = rt.RuntimeTests().native_fixture(probe=False, model_result=result)
                self.assertEqual(state['result'], {'ok': True})
                record = state['diagnostics'][0]
                self.assertEqual(record['outcome'], 'completion_confirmed')
                self.assertTrue(record['completion']['checked'])
                self.assertEqual(record['completion']['observed'], record['completion']['required'])
                self.assertEqual(record['sdk']['status'], 'normal' if result is normal else 'unknown')
                self.assertIs(record['sdk']['completed'], True if result is normal else None)
                self.assertIs(record['sdk']['interrupted'], False if result is normal else None)
                self.assertIs(record['sdk']['errorPresent'], None if result in cases[:3] else False)

    def test_real_temporary_draft_edits_without_finalize_stay_incomplete(self):
        from automation.tests.test_draft_bridge import DraftTests
        DraftTests.setUpClass()
        fixture = DraftTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bp, raw = fixture.seed()
        b, p = fixture.bridge, fixture.bridge.p
        for path in c.REQUIRED_POLICY_PATHS:
            target = fixture.root/path
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('OfflineSyntheticPublic policy '+path)
        owner = p.read_json(p.CACHE/'run.lock/owner.json')
        owner['acceptanceHash'] = p.acceptance_digest()
        p.save_json(p.CACHE/'run.lock/owner.json', owner)
        def transport(op, **payload):
            return b.dispatch(payload['runId'], payload['role'], payload['workerId'],
                {'op': op, 'args': {k: v for k, v in payload.items()
                    if k not in ('runId', 'role', 'workerId', 'deadline', 'artifactHash')}})
        def model(prompt, handlers):
            opened = json.loads(handlers['paykit_draft_open']({'version': 'coming-soon'}))
            section = dict(bp['pages'][0]['sections'][0], paragraphs=['OFFLINE_EDIT_CANARY'])
            reply = json.loads(handlers['paykit_draft_edit']({'version': 'coming-soon',
                'expectedRevision': opened['revision'], 'edits': [{'op': 'replaceSection',
                'pageSlug': 'overview', 'sectionId': 'fixture', 'section': section}]}))
            self.assertIn('revision', reply)
        state = rt.RuntimeTests().native_fixture(probe=False, assignment_run_id=fixture.runid,
            policy_bridge_override=transport, bridge_override=transport, model_actions=model)
        self.assertIs(state.get('errorType'), c.CoordinatorError)
        record = state['diagnostics'][0]
        self.assertEqual(record['outcome'], 'normal_return_incomplete')
        self.assertEqual(record['completion']['observed'], [])
        self.assertIn('operations', record, 'missing bounded operation counters')
        self.assertEqual(record['operations']['paykit_draft_open']['succeeded'], 1)
        self.assertEqual(record['operations']['paykit_draft_edit']['succeeded'], 1)
        self.assertEqual(record['operations']['paykit_draft_finalize']['succeeded'], 0)
        draft = p.read_json(b.session_path(fixture.runid, 'fixture-worker', 'draft-coming-soon.json'))
        self.assertEqual(draft['blueprint']['pages'][0]['sections'][0]['paragraphs'], ['OFFLINE_EDIT_CANARY'])
        self.assertEqual((fixture.root/'automation/blueprints/released.json').read_bytes(), raw)
        self.assertNotIn('OFFLINE_EDIT_CANARY', json.dumps(record))

    def test_saved_finalizations_are_observed_but_diagnostics_never_grant_them(self):
        state = rt.RuntimeTests().native_fixture(probe=False)
        self.assertEqual(state['result'], {'ok': True})
        record = state['diagnostics'][0]
        self.assertEqual(record['outcome'], 'completion_confirmed')
        self.assertTrue(record['completion']['checked'])
        self.assertEqual(record['completion']['observed'], record['completion']['required'])
        self.assertIn('operations', record, 'missing bounded operation counters')
        self.assertEqual(record['operations']['paykit_draft_finalize']['succeeded'], 2)

    def test_result_and_exception_metadata_are_allowlisted_not_payloads(self):
        canary = 'OFFLINE_UNTRUSTED_NESTED_CANARY'
        cases = [({'error': canary, 'completed': False, 'api_calls': 9}, 'model_error', 9),
                 ({'interrupted': True, 'api_calls': 4}, 'model_interrupted', 4),
                 ({'error': {'nested': [canary]}, 'api_calls': True}, 'model_result_other', None),
                 ({'interrupted': [canary], 'api_calls': float('inf')}, 'model_result_other', None),
                 (None, 'model_result_other', None),
                 ({'completed': False, 'api_calls': 10**100,
                   'turn_exit_reason': 'max_iterations_reached('+canary+')',
                   'messages': [{'secret': canary}], 'unknown': canary},
                  'normal_return_incomplete', 1_000_000)]
        for result, expected, count in cases:
            with self.subTest(expected=expected):
                state = rt.RuntimeTests().native_fixture(probe=False, model_submit=False, model_result=result)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                record = state['diagnostics'][0]
                self.assertEqual(record['outcome'], expected)
                self.assertIn('sdk', record, 'missing pinned SDK scalar projection')
                self.assertEqual(record['sdk']['apiCalls'], count)
                self.assertEqual(record['sdk']['exitReason'], 'unknown')
                self.assertNotIn(canary, json.dumps(record))
                self.assertTrue(record['modelReturned'])
        for error, expected in ((RuntimeError(canary), 'model_exception'),
                                (KeyboardInterrupt(canary), 'interrupted')):
            state = rt.RuntimeTests().native_fixture(probe=False, interrupt=error)
            record = state['diagnostics'][0]
            self.assertEqual(record['outcome'], expected)
            self.assertFalse(record['modelReturned'])
            self.assertNotIn(canary, json.dumps(record))

    def test_preload_integrity_failure_is_not_model_failure(self):
        fixture = rt.FixturePublicPolicies()
        def bridge(op, **payload):
            result = fixture(op, **payload)
            if op == 'read_inputs': result['sha256'] = '0'*64
            return result
        state = rt.RuntimeTests().native_fixture(probe=False, policy_bridge_override=bridge)
        self.assertIs(state['errorType'], c.CoordinatorError)
        record = state['diagnostics'][0]
        self.assertEqual(record['outcome'], 'preload_error')
        self.assertFalse(record['modelStarted'])
        self.assertEqual(record['lastPhase'], 'policies')
        self.assertFalse(record['preload']['policiesDone'])

    def test_soft_expiry_and_real_alarm_are_trusted_deadlines_not_inferred_sdk_reasons(self):
        fixture = rt.FixturePublicPolicies()
        clock = [1000.0]
        def bridge(op, **payload):
            result = fixture(op, **payload)
            if op == 'bootstrap': clock[0] += 61
            return result
        with patch.object(c.time, 'time', side_effect=lambda: clock[0]):
            state = rt.RuntimeTests().native_fixture(probe=False, policy_bridge_override=bridge)
        self.assertEqual(state['diagnostics'][0]['outcome'], 'deadline_expired')
        original = c.bounded_alarm
        def short_alarm(deadline): return original(min(deadline, time.time()+.04))
        def stalled_model(prompt, handlers): time.sleep(.2)
        with patch.object(c, 'bounded_alarm', short_alarm):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_actions=stalled_model)
        self.assertEqual(state['result'], {'ok': False, 'error': 'public worker failed'})
        self.assertEqual(state['diagnostics'][0]['outcome'], 'deadline_expired')
        self.assertTrue(state['closed'])

    def test_parent_timeout_is_separate_and_cannot_import_forged_diagnostic_authority(self):
        from unittest.mock import Mock
        import sys
        a = channel_assignment()
        for fail_cleanup in (False, True):
            collector = c.WorkerOutcomeCollector()
            proc = Mock(pid=123456789)
            proc.communicate.side_effect = c.subprocess.TimeoutExpired('UNTRUSTED_CMD_CANARY', 1,
                output='UNTRUSTED_OUTPUT_CANARY', stderr='UNTRUSTED_STDERR_CANARY')
            proc.poll.return_value = 1
            with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                 patch.object(c.subprocess, 'Popen', return_value=proc), \
                 patch.object(c.os, 'killpg', side_effect=OSError('UNTRUSTED_KILL_CANARY') if fail_cleanup else None):
                with self.assertRaises(c.OwnedCleanupError): c.fresh_worker(a, collector=collector)
            record = collector.report()['sessions'][0]
            self.assertEqual(record['channel'], 'timeout')
            self.assertEqual(record['cleanup'], 'failed' if fail_cleanup else 'reaped')
            self.assertIsNone(record['child'])
            self.assertEqual(record['diagnosticStatus'], 'absent')
            self.assertNotIn('UNTRUSTED_', json.dumps(record))
        child = c.WorkerDiagnostics(a).finish()
        child['fatalCleanup'] = True
        child['completion']['checked'] = True
        core, channel, _, _ = c.parse_worker_terminal(c.canonical_json(dict(
            ok=False, error='public worker failed', diagnostic=child)), 1, 'author')
        self.assertEqual(channel, 'terminal_error')
        self.assertEqual(core, {'ok': False, 'error': 'public worker failed'})

    def test_fatal_callback_latch_wins_even_over_diagnostic_control_interrupt(self):
        a = channel_assignment()
        diagnostic = c.WorkerDiagnostics(a)
        def fatal(*args, **kwargs): raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
        broker = c.WorkerTools(a, fatal, diagnostics=diagnostic)
        diagnostic.broker = broker
        with self.assertRaises(c.OwnedCleanupError): broker.dispatch('paykit_draft_open', {'version': 'released'})
        record = diagnostic.finish()
        self.assertTrue(record['fatalCleanup'])
        self.assertEqual(record['outcome'], 'fatal_cleanup')
        with patch.object(diagnostic, 'snapshot', side_effect=c.deadline_error()):
            with self.assertRaises(c.OwnedCleanupError): broker.dispatch('paykit_draft_open', {'version': 'released'})
            with self.assertRaises(c.OwnedCleanupError): broker.require_completion()

    def test_completion_invalidation_is_observed_before_mutation_io(self):
        a = channel_assignment()
        with tempfile.TemporaryDirectory() as directory:
            diagnostic = c.WorkerDiagnostics(a)
            def transport(op, **payload):
                if op == 'draft_finalize':
                    return dict(version=payload['version'], revision='d'*64, saved=True, sha256='a'*64, unchanged=True)
                before = json.loads(diagnostic.latest)
                self.assertEqual(before['completion']['observed'], ['paykit_submit_blueprint:coming-soon'])
                self.assertEqual(before['completion']['pendingMutations']['released'], 1)
                (Path(directory)/'actual-temporary-edit').write_text('SYNTHETIC_EDIT')
                raise c.CoordinatorError('synthetic lost acknowledgement')
            broker = c.WorkerTools(a, transport, diagnostics=diagnostic)
            diagnostic.broker = broker
            for version in c.VERSIONS:
                broker.dispatch('paykit_draft_finalize', dict(version=version, expectedRevision='d'*64))
            broker.require_completion()
            with self.assertRaises(c.CoordinatorError):
                broker.dispatch('paykit_draft_edit', dict(version='released', expectedRevision='d'*64,
                    edits=[dict(op='replaceSourceLinks', pageSlug='overview', sourceLinks=[])]))
            self.assertEqual((Path(directory)/'actual-temporary-edit').read_text(), 'SYNTHETIC_EDIT')
            with self.assertRaises(c.CoordinatorError): broker.require_completion()
            self.assertEqual(diagnostic.finish()['completion']['pendingMutations']['released'], 0)

    def test_reviewer_saved_rejection_is_not_approval_or_missing_submission(self):
        state = rt.RuntimeTests().native_fixture(probe=False, role='reviewer', review_status='fail')
        self.assertEqual(state['result'], {'ok': True, 'reviewPassed': False})
        record = state['diagnostics'][0]
        self.assertIn('review', record, 'missing safe saved-verdict state')
        self.assertEqual(record['review'], {'saved': True, 'verdict': 'fail'})
        self.assertTrue(record['preload']['candidatesDone'])
        state = rt.RuntimeTests().native_fixture(probe=False, role='reviewer', model_submit=False)
        record = state['diagnostics'][0]
        self.assertEqual(record['review'], {'saved': False, 'verdict': 'unknown'})
        self.assertEqual(record['outcome'], 'normal_return_incomplete')

    def test_cleanup_precedes_terminal_capture_and_does_not_claim_crash_durability(self):
        events = []
        original = c.WorkerDiagnostics.finish
        def finish(d, error=None):
            events.append('capture')
            return original(d, error)
        with patch.object(c.WorkerDiagnostics, 'finish', finish):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, close_hook=lambda: events.append('close'))
        self.assertEqual(state['result'], {'ok': True})
        self.assertEqual(events, ['close', 'capture'])
        self.assertEqual(state['diagnostics'][0]['lastPhase'], 'completion')

    def test_projection_faults_do_not_replace_fatal_protocol_or_grant_completion(self):
        def fatal(*args, **kwargs): raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
        for submit, fatal_callback in ((False, False), (True, False), (True, True)):
            with patch.object(c.WorkerDiagnostics, 'finish', side_effect=TypeError('OFFLINE_PRIVATE_CANARY')):
                state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_submit=submit,
                    bridge_override=fatal if fatal_callback else None)
            expected = {'ok': True} if submit and not fatal_callback else {'ok': False, 'error': 'public worker failed'}
            if fatal_callback: expected['fatalOwnedCleanup'] = True
            self.assertEqual(json.loads(state['output']), expected)
            self.assertNotIn('CANARY', state['output'])

    def test_fixed_schema_bounded_counts_and_unknown_labels_stay_safe_in_memory(self):
        a = channel_assignment()
        diagnostic = c.WorkerDiagnostics(a)
        for value, expected in ((True, None), (-1, None), (1.5, None), (float('nan'), None),
                                (float('inf'), None), ('OFFLINE_PAYLOAD_CANARY', None),
                                (None, None), (10**100, 1_000_000), (0, 0)):
            diagnostic.model_result({'api_calls': value})
            self.assertEqual(json.loads(diagnostic.latest)['sdk']['apiCalls'], expected)
        for counters in diagnostic.operations.values():
            for key in counters: counters[key] = 10**100
        diagnostic.phase = diagnostic.last_phase = 'OFFLINE_PAYLOAD_CANARY'
        diagnostic.outcome = diagnostic.last_operation = diagnostic.last_operation_outcome = 'OFFLINE_PAYLOAD_CANARY'
        record = diagnostic.finish()
        c.validate_diagnostic(record, 'author')
        self.assertEqual(record['schemaVersion'], 2)
        self.assertLessEqual(len(diagnostic.latest), c.DIAGNOSTIC_MAX_BYTES)
        self.assertNotIn('CANARY', diagnostic.latest.decode())
        self.assertTrue(all(v == 1_000_000 for item in record['operations'].values() for v in item.values()))
        for key in ('runId', 'workerId'):
            for bad in ('../escape', '/tmp/escape', 'a/b', '.', '..', 'a'*101, None, 7, 'bad\x00name'):
                with self.subTest(key=key, bad=bad), self.assertRaises(c.CoordinatorError):
                    c.WorkerDiagnostics(dict(a, **{key: bad}))

    def test_real_hard_kill_has_parent_only_observations_without_claiming_child_cause(self):
        import subprocess, sys
        original = subprocess.Popen
        def spawn(command, **kwargs):
            return original([sys.executable, '-c', 'import os,signal; os.kill(os.getpid(),signal.SIGKILL)'], **kwargs)
        collector = c.WorkerOutcomeCollector()
        with patch.object(c, 'project_interpreter', return_value=sys.executable), patch.object(c.subprocess, 'Popen', spawn):
            with self.assertRaises(c.OwnedCleanupError): c.fresh_worker(channel_assignment(), collector=collector)
        record = collector.report()['sessions'][0]
        self.assertEqual(record['channel'], 'protocol_error')
        self.assertEqual(record['cleanup'], 'reaped')
        self.assertIsNone(record['child'])

    def test_probe_mode_does_not_create_diagnostics_or_read_data(self):
        for role in ('author', 'reviewer', 'walker'):
            state = rt.RuntimeTests().native_fixture(probe=True, role=role)
            self.assertTrue(state['result']['ok'])
            self.assertEqual(state['diagnostics'], [])
            self.assertEqual(state['policyBridge'].events, [])
            self.assertEqual(state['conversations'], 0)

    def test_parent_observation_finalization_cannot_skip_unconditional_cleanup(self):
        import sys
        from unittest.mock import Mock
        collector = c.WorkerOutcomeCollector()
        events = []
        proc = Mock(pid=123456789, returncode=0)
        proc.communicate.return_value = ('{"ok":true}', None)
        proc.poll.side_effect = lambda: events.append('poll') or 0
        active = c.deadline_error()
        def finalize(entry):
            events.append('capture')
            raise active
        with patch.object(c, 'project_interpreter', return_value=sys.executable), \
             patch.object(c.subprocess, 'Popen', return_value=proc), \
             patch.object(c.os, 'killpg', side_effect=lambda *args: events.append('kill')), \
             patch.object(collector, 'finalize', side_effect=finalize):
            with self.assertRaises(c.CoordinatorError) as caught:
                c.fresh_worker(channel_assignment(), collector=collector)
            self.assertIs(caught.exception, active)
        self.assertEqual(events, ['kill', 'kill', 'poll', 'capture'])
        self.assertEqual(collector.report()['sessions'][0]['cleanup'], 'reaped')

    def test_untrusted_callback_kwargs_and_unknown_labels_are_never_persisted(self):
        canary = 'OFFLINE_CALLBACK_KWARGS_CANARY'
        def model(prompt, handlers):
            reply = json.loads(handlers['paykit_draft_open']({'version': 'released',
                'privateSummary': {'nested': [{'query': canary}]}}))
            self.assertEqual(reply, {'error': 'public operation denied or failed'})
        state = rt.RuntimeTests().native_fixture(probe=False, model_actions=model)
        self.assertIs(state['errorType'], c.CoordinatorError)
        record = state['diagnostics'][0]
        self.assertEqual(record['operations']['paykit_draft_open']['failed'], 1)
        self.assertEqual(record['lastOperationOutcome'], 'failed')
        self.assertEqual(record['outcome'], 'normal_return_incomplete')
        self.assertNotIn(canary, json.dumps(record))


class RCH01ControlTests(unittest.TestCase):
    def test_rch01_real_alarm_inside_projection_cannot_authorize_success(self):
        original = c.canonical_json
        delayed = []
        def slow(record):
            if type(record) is dict and record.get('modelReturned') is True and not delayed:
                delayed.append(True)
                time.sleep(.3)
            return original(record)
        with patch.object(c, 'canonical_json', slow):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, assignment_seconds=.15)
        self.assertEqual(delayed, [True])
        self.assertEqual(state['exit'], 1)
        self.assertEqual(state['result'], {'ok': False, 'error': 'public worker failed'})
        self.assertTrue(state['closed'])
        self.assertTrue(state['restoration_attempted'])
        self.assertEqual(state['diagnostics'][0]['outcome'], 'deadline_expired')
        self.assertNotIn('PRIVATE_', state['output'])

    def test_rch01_optional_fault_stages_propagate_exact_controls_without_primary(self):
        import contextlib
        for stage in ('record', 'validate', 'snapshot_encode', 'finish', 'envelope_encode',
                      'scope', 'parent_validate', 'parent_encode', 'collector_validate'):
            for control in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_FATAL'),
                            KeyboardInterrupt('PRIVATE_CONTROL'), SystemExit('PRIVATE_CONTROL')):
                with self.subTest(stage=stage, control=type(control).__name__):
                    a = channel_assignment()
                    d = c.WorkerDiagnostics(a)
                    valid = d.finish()
                    stream = c.canonical_json(dict(ok=True, diagnostic=valid))
                    collector = c.WorkerOutcomeCollector()
                    entry = collector.reserve(a)
                    entry['child'] = valid
                    with contextlib.ExitStack() as faults:
                        target, name = ((d, '_record') if stage == 'record' else
                            (c, 'validate_diagnostic') if stage in ('validate', 'parent_validate', 'collector_validate') else
                            (d, 'finish') if stage in ('finish', 'scope') else (c, 'canonical_json'))
                        encode = c.canonical_json
                        def envelope_encode(value):
                            if type(value) is dict and 'ok' in value: raise control
                            return encode(value)
                        faults.enter_context(patch.object(target, name,
                            side_effect=envelope_encode if stage == 'envelope_encode' else control))
                        if stage == 'scope':
                            faults.enter_context(patch.object(c, 'WorkerDiagnostics', return_value=d))
                        with self.assertRaises(type(control)) as caught:
                            if stage in ('record', 'validate', 'snapshot_encode'): d.snapshot()
                            elif stage in ('finish', 'envelope_encode'): c.terminal_envelope({'ok': True}, d)
                            elif stage == 'scope':
                                with c.worker_diagnostic_scope(a): pass
                            elif stage in ('parent_validate', 'parent_encode'): c.parse_worker_terminal(stream, 0, 'author')
                            else: collector.finalize(entry)
                        self.assertIs(caught.exception, control)

    def test_rch01_primary_control_survives_secondary_capture_and_cleanup_observation(self):
        for primary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_PRIMARY'),
                        KeyboardInterrupt('PRIVATE_PRIMARY'), SystemExit('PRIVATE_PRIMARY')):
            for secondary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_SECONDARY'),
                              KeyboardInterrupt('PRIVATE_SECONDARY'), SystemExit('PRIVATE_SECONDARY'),
                              OSError('PRIVATE_SECONDARY')):
                a = channel_assignment()
                d = c.WorkerDiagnostics(a)
                with patch.object(c, 'WorkerDiagnostics', return_value=d), \
                     patch.object(d, 'finish', side_effect=secondary):
                    with self.assertRaises(type(primary)) as caught:
                        with c.worker_diagnostic_scope(a): raise primary
                self.assertIs(caught.exception, primary)
                original = c.WorkerDiagnostics.mark
                seen = []
                def mark(diagnostic, phase):
                    if phase == 'cleanup':
                        seen.append(True)
                        raise secondary
                    return original(diagnostic, phase)
                errors = []
                original_worker = c._native_worker
                def worker(*args, **kwargs):
                    try: return original_worker(*args, **kwargs)
                    except BaseException as error:
                        errors.append(error)
                        raise
                with patch.object(c.WorkerDiagnostics, 'mark', mark), patch.object(c, '_native_worker', worker):
                    state = rt.RuntimeTests().native_fixture(probe=False, interrupt=primary)
                self.assertEqual(seen, [True])
                self.assertIs(errors[0], primary)
                self.assertTrue(state['closed'])
                self.assertTrue(state['restoration_attempted'])

    def test_rch01_explicit_primary_finish_error_survives_each_secondary_capture_stage(self):
        for primary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_PRIMARY')):
            for stage in ('record', 'validate', 'decode', 'encode'):
                for secondary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_SECONDARY'),
                                  KeyboardInterrupt('PRIVATE_SECONDARY'), SystemExit('PRIVATE_SECONDARY'),
                                  OSError('PRIVATE_SECONDARY')):
                    d = c.WorkerDiagnostics(channel_assignment())
                    target, name = ((d, '_record') if stage == 'record' else
                        (c, 'validate_diagnostic') if stage == 'validate' else
                        (c.json, 'loads') if stage == 'decode' else (c, 'canonical_json'))
                    with self.subTest(stage=stage, secondary=type(secondary).__name__), \
                         patch.object(target, name, side_effect=secondary):
                        if stage == 'record' and type(secondary) is OSError:
                            record = d.finish(primary)  # Ordinary snapshot loss is still optional.
                            self.assertEqual(record['outcome'], 'deadline_expired' if c.is_deadline_error(primary) else 'fatal_cleanup')
                            self.assertTrue(record['diagnosticsFailed'])
                            continue
                        with self.assertRaises(type(primary)) as caught: d.finish(primary)
                    self.assertIs(caught.exception, primary)

    def test_rch01_callback_observation_deadline_is_retained_even_if_sdk_suppresses_it(self):
        import hashlib
        active = c.deadline_error()
        original_operation = c.WorkerDiagnostics.operation
        caught = []
        def operation(d, name, status):
            if name == 'paykit_draft_finalize' and status == 'succeeded': raise active
            return original_operation(d, name, status)
        original_worker = c._native_worker
        def worker(*args, **kwargs):
            try: return original_worker(*args, **kwargs)
            except BaseException as error:
                caught.append(error)
                raise
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory)/'released-finalization'
            def transport(op, **payload):
                if op == 'draft_finalize':
                    saved.write_bytes(b'OFFLINE_ACTUAL_FINALIZATION')
                    return dict(version=payload['version'], revision=payload['expectedRevision'],
                                saved=True, sha256=hashlib.sha256(saved.read_bytes()).hexdigest(), unchanged=False)
                return {'ok': True}
            with patch.object(c.WorkerDiagnostics, 'operation', operation), patch.object(c, '_native_worker', worker):
                state = rt.RuntimeTests().native_fixture(probe=False, bridge_override=transport)
            self.assertEqual(saved.read_bytes(), b'OFFLINE_ACTUAL_FINALIZATION')
        self.assertIs(state.get('errorType'), c.CoordinatorError)
        self.assertIs(caught[0], active)
        self.assertTrue(state['closed'])
        self.assertFalse(state['diagnostics'][0]['completion']['checked'])
        self.assertEqual(state['diagnostics'][0]['outcome'], 'deadline_expired')

    def test_rch01_collector_finalization_controls_preserve_primary_and_cannot_skip_cleanup(self):
        import sys
        from unittest.mock import Mock
        for primary in (None, c.deadline_error(), c.OwnedCleanupError('PRIVATE_PRIMARY')):
            for secondary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_SECONDARY'),
                              KeyboardInterrupt('PRIVATE_SECONDARY'), SystemExit('PRIVATE_SECONDARY')):
                collector = c.WorkerOutcomeCollector()
                proc = Mock(pid=123456789, returncode=0)
                proc.communicate.return_value = ('{"ok":true}', None)
                proc.communicate.side_effect = primary
                proc.poll.return_value = 0
                events = []
                def finalize(entry):
                    events.append('finalize')
                    raise secondary
                with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                     patch.object(c.subprocess, 'Popen', return_value=proc), \
                     patch.object(c.os, 'killpg', side_effect=lambda *args: events.append('kill')), \
                     patch.object(collector, 'finalize', finalize):
                    with self.assertRaises(type(primary or secondary)) as caught:
                        c.fresh_worker(channel_assignment(), collector=collector)
                self.assertIs(caught.exception, primary or secondary)
                self.assertEqual(events, ['kill', 'kill', 'finalize'])
                self.assertEqual(collector.sessions[0]['cleanup'], 'reaped')

    def test_rch01_success_report_control_cannot_be_swallowed_as_optional_output(self):
        import contextlib, io, sys, types
        modules = {name: types.ModuleType(name) for name in ('hermes_bootstrap', 'hermes_cli.env_loader')}
        modules['hermes_cli.env_loader'].load_hermes_dotenv = lambda **kwargs: None
        for control in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_REPORT'),
                        KeyboardInterrupt('PRIVATE_REPORT'), SystemExit('PRIVATE_REPORT')):
            output = io.StringIO()
            with patch.dict(sys.modules, modules), patch.object(c, 'host_lock', side_effect=lambda _: contextlib.nullcontext()), \
                 patch.object(c, 'runtime_attestation', return_value={}), patch.object(c, 'NativeBridge'), \
                 patch.object(c.Coordinator, 'run', return_value={'action': 'validated-no-publish'}), \
                 patch.object(c.WorkerOutcomeCollector, 'report', side_effect=control), contextlib.redirect_stdout(output):
                self.assertEqual(c.main(['--no-publish']), 1)
            self.assertIn('Manual trial failed', output.getvalue())
            self.assertNotIn('PRIVATE_', output.getvalue())


class RCH02ParentDeadlineTests(unittest.TestCase):
    def test_rch02_expiry_after_spawn_without_terminal_preserves_exact_deadline_and_never_repairs(self):
        import sys
        from unittest.mock import Mock
        active = c.deadline_error()
        original_remaining = c.remaining
        clock, spawned = [1000.0], []
        bridge, collector = rt.FixtureBridge(), c.WorkerOutcomeCollector()
        proc = Mock(pid=123456789, returncode=1)
        proc.poll.return_value = 1
        def interpreter(root):
            clock[0] += 501
            return sys.executable
        def remaining(deadline, cap=c.MAX_SECONDS):
            if spawned and deadline <= clock[0]: raise active
            return original_remaining(deadline, cap)
        def spawn(*args, **kwargs):
            spawned.append(True)
            return proc
        with patch.object(c.time, 'time', side_effect=lambda: clock[0]), \
             patch.object(c, 'remaining', remaining), patch.object(c, 'project_interpreter', interpreter), \
             patch.object(c.subprocess, 'Popen', side_effect=spawn), patch.object(c.os, 'killpg') as kill:
            with self.assertRaises(c.CoordinatorError) as caught:
                c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
        self.assertIs(caught.exception, active)
        self.assertEqual(spawned, [True])
        proc.communicate.assert_not_called()
        self.assertEqual(kill.call_count, 2)
        self.assertEqual([op for op, _ in bridge.events], ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])
        self.assertEqual(len(collector.sessions), 1)
        self.assertEqual(collector.sessions[0]['cleanup'], 'reaped')
        self.assertEqual(collector.sessions[0]['channel'], 'protocol_error')
        self.assertIsNone(collector.sessions[0]['child'])

    def test_rch02_parent_parse_and_collector_deadlines_never_start_repair_or_later_stage(self):
        import sys
        from unittest.mock import Mock
        for boundary in ('parse', 'optional_validate', 'optional_encode', 'finalize'):
            for role in ('author', 'reviewer'):
                with self.subTest(boundary=boundary, role=role):
                    active = c.deadline_error()
                    collector, bridge = c.WorkerOutcomeCollector(), rt.FixtureBridge()
                    valid = c.WorkerDiagnostics(channel_assignment(role)).finish()
                    stream = c.canonical_json(dict(ok=True, **({'reviewPassed': True} if role == 'reviewer' else {}), diagnostic=valid))
                    proc = Mock(pid=123456789, returncode=0)
                    proc.communicate.return_value = (stream, None)
                    proc.poll.return_value = 0
                    original_parse, original_validate, original_encode = c.parse_worker_terminal, c.validate_diagnostic, c.canonical_json
                    def worker(a):
                        if a['role'] != role: return {'ok': True}
                        target, name, original = ((c, 'parse_worker_terminal', original_parse) if boundary == 'parse' else
                            (c, 'validate_diagnostic', original_validate) if boundary == 'optional_validate' else
                            (c, 'canonical_json', original_encode) if boundary == 'optional_encode' else
                            (collector, 'finalize', collector.finalize))
                        with patch.object(target, name, side_effect=active):
                            return c.fresh_worker(a, collector=collector)
                    with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                         patch.object(c.subprocess, 'Popen', return_value=proc) as popen, patch.object(c.os, 'killpg') as kill:
                        with self.assertRaises(c.CoordinatorError) as caught:
                            c.Coordinator(bridge, worker, lambda: None).run()
                    self.assertIs(caught.exception, active)
                    self.assertEqual(popen.call_count, 1)
                    self.assertEqual(kill.call_count, 2)
                    events = [op for op, _ in bridge.events]
                    self.assertNotIn('repair', events)
                    self.assertNotIn('validate', events)
                    self.assertNotIn('publish', events)
                    self.assertEqual(len(collector.sessions), 1)
                    self.assertEqual(collector.sessions[0]['cleanup'], 'reaped')

    def test_rch02_trusted_bridge_callback_deadline_is_not_repairable_or_generic_replaced(self):
        for op in ('private', 'plan', 'status', 'examples', 'generate', 'build', 'artifact_hash', 'validate'):
            active = c.deadline_error()
            bridge = rt.FixtureBridge()
            original = bridge.__call__
            def callback(operation, **payload):
                if operation == op: raise active
                return original(operation, **payload)
            def private():
                if op == 'private': raise active
            workers = []
            def worker(a):
                workers.append(a['role'])
                return dict(ok=True, **({'reviewPassed': True} if a['role'] == 'reviewer' else {}))
            with self.subTest(op=op), self.assertRaises(c.CoordinatorError) as caught:
                c.Coordinator(callback, worker, private).run()
            self.assertIs(caught.exception, active)
            self.assertNotIn('repair', [event for event, _ in bridge.events])
            self.assertNotIn('publish', [event for event, _ in bridge.events])
            self.assertLessEqual(workers.count('author'), 1)

    def test_rch02_primary_deadline_or_fatal_survives_failure_recording_control(self):
        for primary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_PRIMARY')):
            for secondary in (c.deadline_error(), c.OwnedCleanupError('PRIVATE_SECONDARY'),
                              KeyboardInterrupt('PRIVATE_SECONDARY'), SystemExit('PRIVATE_SECONDARY')):
                fixture = rt.FixtureBridge()
                def bridge(op, **payload):
                    if op == 'fail': raise secondary
                    return fixture(op, **payload)
                def worker(a): raise primary
                with self.subTest(primary=type(primary).__name__, secondary=type(secondary).__name__), \
                     self.assertRaises(type(primary)) as caught:
                    c.Coordinator(bridge, worker, lambda: None).run()
                self.assertIs(caught.exception, primary)
                self.assertNotIn('repair', [op for op, _ in fixture.events])

    def test_rch02_native_bridge_retains_actual_trusted_deadline(self):
        active = c.deadline_error()
        def terminal(**kwargs): raise active
        bridge = c.NativeBridge(terminal_tool=terminal, deadline=time.time()+60)
        with self.assertRaises(c.CoordinatorError) as caught:
            bridge('status', runId='offline-run')
        self.assertIs(caught.exception, active)

    def test_rch02_real_child_reaped_cleanup_failure_wins_over_deadline_and_secondary_observation(self):
        import subprocess, sys
        active = c.deadline_error()
        spawned = []
        original_popen = c.subprocess.Popen
        def spawn(command, **kwargs):
            proc = original_popen([sys.executable, '-c', 'import time;time.sleep(30)'], **kwargs)
            spawned.append(proc)
            proc.communicate = lambda *args, **kwargs: (_ for _ in ()).throw(active)
            return proc
        original_wait = subprocess.Popen.wait
        def wait(proc, *args, **kwargs):
            result = original_wait(proc, *args, **kwargs)
            raise OSError('PRIVATE_REAL_WAIT_REPLY_LOST')
        collector, bridge = c.WorkerOutcomeCollector(), rt.FixtureBridge()
        finalizers = []
        def finalize(entry):
            finalizers.append(entry['cleanup'])
            raise SystemExit('PRIVATE_SECONDARY_CAPTURE')
        try:
            with patch.object(c, 'project_interpreter', return_value=sys.executable), \
                 patch.object(c.subprocess, 'Popen', spawn), patch.object(original_popen, 'wait', wait), \
                 patch.object(collector, 'finalize', finalize):
                with self.assertRaises(c.OwnedCleanupError) as caught:
                    c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
            self.assertIsNot(caught.exception, active)
            self.assertEqual(len(spawned), 1)
            self.assertIsNotNone(spawned[0].poll())
            self.assertEqual(finalizers, ['failed'])
            self.assertNotIn('repair', [op for op, _ in bridge.events])
        finally:
            for proc in spawned:
                if proc.poll() is None: proc.kill()
                original_wait(proc, timeout=3)
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None: stream.close()

    def test_rch02_child_diagnostic_deadline_does_not_turn_known_ordinary_failure_fatal(self):
        import sys
        from unittest.mock import Mock
        child = c.WorkerDiagnostics(channel_assignment()).finish(c.deadline_error())
        wire = c.canonical_json(dict(ok=False, error='public worker failed', diagnostic=child))
        bridge, collector = rt.FixtureBridge(), c.WorkerOutcomeCollector()
        proc = Mock(pid=123456789, returncode=1)
        proc.communicate.return_value = (wire, None)
        proc.poll.return_value = 1
        with patch.object(c, 'project_interpreter', return_value=sys.executable), \
             patch.object(c.subprocess, 'Popen', return_value=proc) as popen, patch.object(c.os, 'killpg'):
            with self.assertRaises(c.CoordinatorError) as caught:
                c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
        self.assertNotIsInstance(caught.exception, c.OwnedCleanupError)
        self.assertFalse(c.is_deadline_error(caught.exception))
        self.assertEqual(popen.call_count, 3)
        self.assertEqual([op for op, _ in bridge.events].count('repair'), 2)
        self.assertEqual([entry['channel'] for entry in collector.sessions], ['terminal_error']*3)


class RCH03FiniteJSONTests(unittest.TestCase):
    def test_rch03_positive_and_negative_exponent_overflow_rejected_before_core_or_any_serializer(self):
        for role in ('author', 'reviewer', 'walker'):
            for kind in ('success', 'error', 'fatal'):
                core = (dict(ok=True, **({'reviewPassed': False} if role == 'reviewer' else {})) if kind == 'success'
                        else dict(ok=False, error='public worker failed', **({'fatalOwnedCleanup': True} if kind == 'fatal' else {})))
                for token in ('1e999', '-1e999'):
                    for fragment in ('"diagnostic":{"private":[{"nested":[TOKEN]}]}',
                                     '"diagnostic":[{"private":{"nested":TOKEN}}]',
                                     '"private":{"nested":[{"overflow":TOKEN}]}'):
                        stream = json.dumps(core)[:-1]+','+fragment.replace('TOKEN', token)+'}'
                        with self.subTest(role=role, kind=kind, token=token, fragment=fragment), \
                             patch.object(c, 'canonical_json') as canonical, patch.object(c.json, 'dumps') as dumps:
                            with self.assertRaises(c.OwnedCleanupError):
                                c.parse_worker_terminal(stream, 0 if kind == 'success' else 1, role)
                            canonical.assert_not_called()
                            dumps.assert_not_called()

    def test_rch03_overflow_document_stops_one_worker_without_repairs_for_every_core(self):
        import sys
        from unittest.mock import Mock
        for kind in ('success', 'error', 'fatal'):
            for token in ('1e999', '-1e999'):
                core = ({'ok': True} if kind == 'success' else dict(ok=False, error='public worker failed',
                    **({'fatalOwnedCleanup': True} if kind == 'fatal' else {})))
                stream = json.dumps(core)[:-1]+',"diagnostic":{"private":[{"nested":'+token+'}]}}'
                bridge, collector = rt.FixtureBridge(), c.WorkerOutcomeCollector()
                proc = Mock(pid=123456789, returncode=0 if kind == 'success' else 1)
                proc.communicate.return_value = (stream, None)
                proc.poll.return_value = proc.returncode
                with self.subTest(kind=kind, token=token), \
                     patch.object(c, 'project_interpreter', return_value=sys.executable), \
                     patch.object(c.subprocess, 'Popen', return_value=proc) as popen, patch.object(c.os, 'killpg') as kill:
                    with self.assertRaises(c.OwnedCleanupError):
                        c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
                self.assertEqual(popen.call_count, 1)
                self.assertEqual(kill.call_count, 2)
                self.assertEqual([op for op, _ in bridge.events], ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])
                self.assertEqual(len(collector.sessions), 1)
                self.assertEqual(collector.sessions[0]['channel'], 'protocol_error')
                self.assertEqual(collector.sessions[0]['cleanup'], 'reaped')
                self.assertIsNone(collector.sessions[0]['child'])

    def test_rch03_finite_floats_in_invalid_diagnostic_are_optional_not_protocol_errors(self):
        base = c.WorkerDiagnostics(channel_assignment()).finish()
        for token in ('0.0', '-0.0', '1.5', '-1.5', '1e308', '-1e308', '1e-999', '-1e-999'):
            for field in ('elapsedMs', 'private'):
                malformed = dict(base)
                malformed[field] = 'FLOAT_TOKEN'
                stream = json.dumps(dict(ok=True, diagnostic=malformed)).replace('"FLOAT_TOKEN"', token)
                with self.subTest(token=token, field=field), patch.object(c, 'canonical_json') as encode:
                    core, channel, child, status = c.parse_worker_terminal(stream, 0, 'author')
                self.assertEqual(core, {'ok': True})
                self.assertEqual(channel, 'terminal_ok')
                self.assertIsNone(child)
                self.assertEqual(status, 'malformed')
                encode.assert_not_called()
        # Finite integers remain integers and valid D is transferred losslessly.
        base['elapsedMs'] = 0
        core, channel, child, status = c.parse_worker_terminal(c.canonical_json(dict(ok=True, diagnostic=base)), 0, 'author')
        self.assertEqual(child, base)
        self.assertEqual(status, 'valid')

    def test_rch03_default_integer_digit_limit_stays_fail_closed_before_serialization(self):
        import sys
        limit = sys.get_int_max_str_digits()
        self.assertGreater(limit, 0, 'fixture requires the interpreter default integer digit guard')
        stream = '{"ok":true,"diagnostic":{"private":'+'9'*(limit+1)+'}}'
        self.assertLess(len(stream), 100000)
        with patch.object(c, 'canonical_json') as encode:
            with self.assertRaises(c.OwnedCleanupError): c.parse_worker_terminal(stream, 0, 'author')
        encode.assert_not_called()


class RCH04CallbackPriorityTests(unittest.TestCase):
    @staticmethod
    def faults():
        return (RuntimeError('PRIVATE_ORDINARY'), c.CoordinatorError('deadline expired'),
                c.deadline_error(), c.OwnedCleanupError('PRIVATE_CONTROL'),
                KeyboardInterrupt('PRIVATE_CONTROL'), SystemExit('PRIVATE_CONTROL'))

    def test_failed_callback_preserves_first_trusted_control_matrix(self):
        for primary in self.faults():
            for secondary in self.faults():
                with self.subTest(primary=type(primary).__name__, trusted=c.is_control_error(primary),
                                  secondary=type(secondary).__name__, secondaryTrusted=c.is_control_error(secondary)):
                    d = c.WorkerDiagnostics(channel_assignment())
                    broker = c.WorkerTools(d.assignment, lambda *a, **k: {}, d)
                    original = d.operation
                    def operation(name, status):
                        if status == 'failed': raise secondary
                        return original(name, status)
                    expected = secondary if not c.is_control_error(primary) and c.is_control_error(secondary) else primary
                    with patch.object(broker, '_dispatch', side_effect=primary), patch.object(d, 'operation', operation):
                        try: broker.dispatch('paykit_public_bundle', {})
                        except BaseException as error: actual = error
                        else: actual = None
                    self.assertIs(actual, expected)
                    self.assertIs(broker.control_error, expected if c.is_control_error(expected) else None)

    def test_both_callback_controls_retained_after_sdk_suppression_and_real_finalization_bytes(self):
        import hashlib
        for status in ('failed', 'succeeded'):
            for active in self.faults()[2:]:
                with self.subTest(status=status, control=type(active).__name__), tempfile.TemporaryDirectory() as directory:
                    caught, observed, saved = [], [], []
                    original_operation, original_worker = c.WorkerDiagnostics.operation, c._native_worker
                    def operation(d, name, outcome):
                        if name == 'paykit_source_read' and outcome == status:
                            observed.append(True)
                            raise active
                        return original_operation(d, name, outcome)
                    def worker(*args, **kwargs):
                        try: return original_worker(*args, **kwargs)
                        except BaseException as error:
                            caught.append(error)
                            raise
                    def bridge(op, **payload):
                        if op == 'draft_finalize':
                            target = Path(directory)/payload['version']
                            target.write_bytes(b'ACTUAL_OFFLINE_FINALIZATION')
                            saved.append(target)
                            return dict(version=payload['version'], revision=payload['expectedRevision'], saved=True,
                                        sha256=hashlib.sha256(target.read_bytes()).hexdigest(), unchanged=False)
                        return {}
                    def model(prompt, handlers):
                        for version in c.VERSIONS:
                            handlers['paykit_draft_finalize'](dict(version=version, expectedRevision='d'*64))
                        try:
                            handlers['paykit_source_read'](dict(version='released',
                                path='../PRIVATE_CALLBACK' if status == 'failed' else 'src/lib.rs', offset=0, limit=10))
                        except BaseException: pass  # Simulate SDK suppression of the handler control.
                    with patch.object(c.WorkerDiagnostics, 'operation', operation), patch.object(c, '_native_worker', worker):
                        state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_actions=model, bridge_override=bridge)
                    self.assertEqual(observed, [True])
                    self.assertEqual(len(saved), 2)
                    self.assertTrue(all(target.read_bytes() == b'ACTUAL_OFFLINE_FINALIZATION' for target in saved))
                    self.assertEqual(len(caught), 1)
                    self.assertIs(caught[0], active)
                    self.assertIs(state['diagnosticObjects'][0].broker.control_error, active)
                    self.assertTrue(state['closed'] and state['restoration_attempted'])
                    self.assertFalse(Path(state['agentKwargs']['cwd']).exists())
                    self.assertEqual(state['exit'], 1)
                    self.assertFalse(state['result']['ok'])
                    self.assertNotIn('PRIVATE_', state['output'])

    def test_real_alarm_in_failed_observation_after_two_real_finalizations(self):
        import hashlib
        original, original_worker = c.canonical_json, c._native_worker
        delayed, caught, attempts, saved = [], [], [], []
        def encode(value):
            attempts.append(json.dumps(value, ensure_ascii=True))
            if (type(value) is dict and value.get('lastOperation') == 'paykit_source_read'
                    and value.get('lastOperationOutcome') == 'failed' and not delayed):
                delayed.append(True)
                time.sleep(.4)
            return original(value)
        def worker(*args, **kwargs):
            try: return original_worker(*args, **kwargs)
            except BaseException as error:
                caught.append(error)
                raise
        with tempfile.TemporaryDirectory() as directory:
            def bridge(op, **payload):
                if op == 'draft_finalize':
                    target = Path(directory)/payload['version']
                    target.write_bytes(b'ACTUAL_ALARM_FINALIZATION')
                    saved.append(target)
                    return dict(version=payload['version'], revision=payload['expectedRevision'], saved=True,
                                sha256=hashlib.sha256(target.read_bytes()).hexdigest(), unchanged=False)
                return {}
            def model(prompt, handlers):
                for version in c.VERSIONS:
                    handlers['paykit_draft_finalize'](dict(version=version, expectedRevision='d'*64))
                handlers['paykit_source_read'](dict(version='released', path='../PRIVATE_CALLBACK', offset=0, limit=10))
            with patch.object(c, 'canonical_json', encode), patch.object(c, '_native_worker', worker):
                state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, assignment_seconds=.2,
                    model_actions=model, bridge_override=bridge)
            self.assertEqual(len(saved), 2)
            self.assertTrue(all(target.read_bytes() == b'ACTUAL_ALARM_FINALIZATION' for target in saved))
        self.assertEqual(delayed, [True])
        self.assertEqual(state['exit'], 1)
        self.assertFalse(state['result']['ok'])
        self.assertTrue(caught and c.is_deadline_error(caught[0]))
        self.assertIs(state['diagnosticObjects'][0].broker.control_error, caught[0])
        self.assertTrue(state['closed'] and state['restoration_attempted'])
        self.assertFalse(Path(state['agentKwargs']['cwd']).exists())
        self.assertEqual(state['diagnostics'][0]['outcome'], 'deadline_expired')
        self.assertTrue(all('PRIVATE_' not in value for value in attempts))
        self.assertNotIn('PRIVATE_', state['output'])


class RCH05EnvelopePriorityTests(unittest.TestCase):
    def test_terminal_envelope_preserves_exact_first_trusted_primary_matrix(self):
        core = {'ok': False, 'error': 'public worker failed'}
        for primary in RCH04CallbackPriorityTests.faults():
            for secondary in RCH04CallbackPriorityTests.faults():
                d = c.WorkerDiagnostics(channel_assignment())
                with self.subTest(primary=type(primary).__name__, trusted=c.is_control_error(primary),
                                  secondary=type(secondary).__name__, secondaryTrusted=c.is_control_error(secondary)), \
                     patch.object(d, 'finish', side_effect=secondary):
                    if not c.is_control_error(primary) and c.is_control_error(secondary):
                        try: c.terminal_envelope(core, d, primary)
                        except BaseException as actual: self.assertIs(actual, secondary)
                        else: self.fail('winning secondary control suppressed')
                    else:
                        self.assertEqual(json.loads(c.terminal_envelope(core, d, primary)), core)

    def test_main_failure_fallback_classifies_winning_control_after_all_cleanup(self):
        import signal
        original_encode, original_directory = c.canonical_json, c.tempfile.TemporaryDirectory
        for primary in RCH04CallbackPriorityTests.faults():
            for secondary in RCH04CallbackPriorityTests.faults():
                attempts, finished, closed, directories = [], [], [], []
                old_handler = signal.getsignal(signal.SIGALRM)
                def directory(*args, **kwargs):
                    manager = original_directory(*args, **kwargs)
                    directories.append(Path(manager.name))
                    return manager
                def encode(value):
                    attempts.append(json.dumps(value, ensure_ascii=True))
                    return original_encode(value)
                def finish(d, error=None):
                    finished.append(error)
                    self.assertEqual(closed, [True])
                    self.assertTrue(directories and all(not path.exists() for path in directories))
                    self.assertIs(signal.getsignal(signal.SIGALRM), old_handler)
                    raise secondary
                winner = secondary if not c.is_control_error(primary) and c.is_control_error(secondary) else primary
                expected = dict(ok=False, error='public worker failed',
                                **({'fatalOwnedCleanup': True} if isinstance(winner, c.OwnedCleanupError) else {}))
                with self.subTest(primary=type(primary).__name__, trusted=c.is_control_error(primary),
                                  secondary=type(secondary).__name__, secondaryTrusted=c.is_control_error(secondary)), \
                     patch.object(c.WorkerDiagnostics, 'finish', finish), patch.object(c, 'canonical_json', encode), \
                     patch.object(c.tempfile, 'TemporaryDirectory', directory):
                    state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, interrupt=primary,
                        close_hook=lambda: closed.append(True))
                    self.assertEqual(state['result'], expected)
                    self.assertEqual(state['exit'], 1)
                    self.assertEqual(finished, [primary])
                    self.assertIs(finished[0], primary)
                    self.assertTrue(state['closed'] and state['restoration_attempted'])
                    parsed, channel, child, status = c.parse_worker_terminal(state['output'], state['exit'], 'author')
                    self.assertEqual(parsed, expected)
                    self.assertEqual(channel, 'terminal_fatal' if isinstance(winner, c.OwnedCleanupError) else 'terminal_error')
                    self.assertIsNone(child)
                    self.assertEqual(status, 'absent')
                    self.assertTrue(all('PRIVATE_' not in value for value in attempts))
                    self.assertNotIn('PRIVATE_', state['output'])

    def test_secondary_owned_failure_envelope_is_one_worker_zero_repairs(self):
        import sys
        from unittest.mock import Mock
        active = c.OwnedCleanupError('PRIVATE_ENVELOPE_CONTROL')
        original = c.WorkerDiagnostics.finish
        def finish(d, error=None):
            if error is not None: raise active
            return original(d, error)
        with patch.object(c.WorkerDiagnostics, 'finish', finish):
            state = rt.RuntimeTests().native_fixture(probe=False, protocol=True, model_submit=False)
        bridge, collector = rt.FixtureBridge(), c.WorkerOutcomeCollector()
        proc = Mock(pid=123456789, returncode=state['exit'])
        proc.communicate.return_value = (state['output'], None)
        proc.poll.return_value = state['exit']
        with patch.object(c, 'project_interpreter', return_value=sys.executable), \
             patch.object(c.subprocess, 'Popen', return_value=proc) as spawned, patch.object(c.os, 'killpg'):
            with self.assertRaises(c.CoordinatorError) as caught:
                c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
        self.assertIsInstance(caught.exception, c.OwnedCleanupError)
        self.assertEqual(spawned.call_count, 1)
        self.assertNotIn('repair', [op for op, _ in bridge.events])
        self.assertEqual(collector.sessions[0]['channel'], 'terminal_fatal')
        self.assertEqual(collector.sessions[0]['cleanup'], 'reaped')
        self.assertTrue(state['closed'] and state['restoration_attempted'])
        self.assertNotIn('PRIVATE_', state['output'])


class RCH06FinalizerPriorityTests(unittest.TestCase):
    def test_scope_finalizer_first_trusted_control_identity_matrix(self):
        for primary in (None, *RCH04CallbackPriorityTests.faults()):
            for secondary in RCH04CallbackPriorityTests.faults():
                d = c.WorkerDiagnostics(channel_assignment())
                expected = (primary if c.is_control_error(primary) else secondary if c.is_control_error(secondary) else primary)
                with self.subTest(primary=type(primary).__name__, trusted=c.is_control_error(primary),
                                  secondary=type(secondary).__name__, secondaryTrusted=c.is_control_error(secondary)), \
                     patch.object(c, 'WorkerDiagnostics', return_value=d), patch.object(d, 'finish', side_effect=secondary):
                    try:
                        with c.worker_diagnostic_scope(d.assignment):
                            if primary is not None: raise primary
                    except BaseException as actual: self.assertIs(actual, expected)
                    else: self.assertIsNone(expected)

    def test_native_api_finalizer_winner_follows_real_close_guard_and_directory_cleanup(self):
        original_worker, original_directory = c.native_worker, c.tempfile.TemporaryDirectory
        for primary in RCH04CallbackPriorityTests.faults():
            for secondary in RCH04CallbackPriorityTests.faults():
                caught, closed, directories, finalized = [], [], [], []
                def directory(*args, **kwargs):
                    manager = original_directory(*args, **kwargs)
                    directories.append(Path(manager.name))
                    return manager
                def finish(d, error=None):
                    finalized.append(error)
                    self.assertEqual(closed, [True])
                    self.assertTrue(directories and all(not path.exists() for path in directories))
                    raise secondary
                def worker(*args, **kwargs):
                    try: return original_worker(*args, **kwargs)
                    except BaseException as error:
                        caught.append(error)
                        raise
                expected = secondary if not c.is_control_error(primary) and c.is_control_error(secondary) else primary
                with self.subTest(primary=type(primary).__name__, trusted=c.is_control_error(primary),
                                  secondary=type(secondary).__name__, secondaryTrusted=c.is_control_error(secondary)), \
                     patch.object(c.WorkerDiagnostics, 'finish', finish), patch.object(c, 'native_worker', worker), \
                     patch.object(c.tempfile, 'TemporaryDirectory', directory):
                    state = rt.RuntimeTests().native_fixture(probe=False, interrupt=primary,
                        close_hook=lambda: closed.append(True))
                    self.assertEqual(len(caught), 1)
                    self.assertIs(caught[0], expected)
                    self.assertEqual(finalized, [primary])
                    self.assertTrue(state['closed'] and state['restoration_attempted'])

    def test_deferred_cleanup_observation_never_prevents_real_cleanup_or_overrides_owned_uncertainty(self):
        original_mark, original_worker = c.WorkerDiagnostics.mark, c._native_worker
        for primary in RCH04CallbackPriorityTests.faults():
            for secondary in RCH04CallbackPriorityTests.faults()[2:]:
                for close_error in (False, True):
                    caught, observed = [], []
                    def mark(d, phase):
                        if phase == 'cleanup':
                            observed.append(True)
                            raise secondary
                        return original_mark(d, phase)
                    def worker(*args, **kwargs):
                        try: return original_worker(*args, **kwargs)
                        except BaseException as error:
                            caught.append(error)
                            raise
                    with self.subTest(primary=type(primary).__name__, secondary=type(secondary).__name__, close=close_error), \
                         patch.object(c.WorkerDiagnostics, 'mark', mark), patch.object(c, '_native_worker', worker):
                        state = rt.RuntimeTests().native_fixture(probe=False, interrupt=primary, close_error=close_error)
                        self.assertEqual(observed, [True])
                        self.assertTrue(state['closed'] and state['restoration_attempted'])
                        self.assertFalse(Path(state['agentKwargs']['cwd']).exists())
                        if close_error:
                            self.assertIsInstance(caught[0], c.OwnedCleanupError)
                            if isinstance(primary, c.OwnedCleanupError): self.assertIs(caught[0], primary)
                        else:
                            self.assertIs(caught[0], primary if c.is_control_error(primary) else secondary)


class RCH07AuthenticatedFatalPriorityTests(unittest.TestCase):
    def test_authenticated_fatal_retains_owned_primary_at_every_optional_boundary(self):
        for role in ('author', 'reviewer', 'walker'):
            d = c.WorkerDiagnostics(channel_assignment(role)).finish()
            core = dict(ok=False, error='public worker failed', fatalOwnedCleanup=True)
            wire = c.canonical_json(dict(core, diagnostic=d))
            for boundary in ('validate', 'encode'):
                for secondary in RCH04CallbackPriorityTests.faults():
                    seen = []
                    original_failure = c.observation_failure
                    def observe(error, primary=None):
                        seen.append((error, primary))
                        return original_failure(error, primary)
                    with self.subTest(role=role, boundary=boundary, secondary=type(secondary).__name__), \
                         patch.object(c, 'validate_diagnostic' if boundary == 'validate' else 'canonical_json', side_effect=secondary), \
                         patch.object(c, 'observation_failure', observe):
                        try: actual = c.parse_worker_terminal(wire, 1, role)
                        except BaseException as error:
                            self.fail('authenticated fatal authority escaped as '+type(error).__name__)
                        self.assertEqual(actual, (core, 'terminal_fatal', None,
                            'malformed' if boundary == 'validate' else 'serialization_failed'))
                        self.assertEqual(len(seen), 1)
                        self.assertIs(seen[0][0], secondary)
                        self.assertIsInstance(seen[0][1], c.OwnedCleanupError)

    def test_fatal_channel_reaches_parent_before_collector_controls_one_worker_zero_repairs(self):
        import sys
        from unittest.mock import Mock
        for boundary in ('validate', 'encode'):
            for secondary in RCH04CallbackPriorityTests.faults()[2:]:
                for collector_control in RCH04CallbackPriorityTests.faults()[2:]:
                    d = c.WorkerDiagnostics(channel_assignment()).finish()
                    wire = c.canonical_json(dict(ok=False, error='public worker failed', fatalOwnedCleanup=True, diagnostic=d))
                    bridge, collector, finalized = rt.FixtureBridge(), c.WorkerOutcomeCollector(), []
                    proc = Mock(pid=123456789, returncode=1)
                    proc.communicate.return_value = (wire, None)
                    proc.poll.return_value = 1
                    target = 'validate_diagnostic' if boundary == 'validate' else 'canonical_json'
                    original = getattr(c, target)
                    def optional(value, *args):
                        if type(value) is dict and value.get('schemaVersion') == 2 and 'operations' in value:
                            raise secondary
                        return original(value, *args)
                    def finalize(entry):
                        finalized.append((entry['channel'], entry['cleanup'], entry['diagnosticStatus']))
                        raise collector_control
                    with self.subTest(boundary=boundary, secondary=type(secondary).__name__, collector=type(collector_control).__name__), \
                         patch.object(c, 'project_interpreter', return_value=sys.executable), \
                         patch.object(c.subprocess, 'Popen', return_value=proc) as spawned, patch.object(c.os, 'killpg'), \
                         patch.object(c, target, optional), patch.object(collector, 'finalize', finalize):
                        try: c.Coordinator(bridge, lambda a: c.fresh_worker(a, collector=collector), lambda: None).run()
                        except BaseException as error: actual = error
                        else: actual = None
                        self.assertIsInstance(actual, c.OwnedCleanupError)
                        self.assertIsNot(actual, secondary)
                        self.assertIsNot(actual, collector_control)
                        self.assertEqual(spawned.call_count, 1)
                        self.assertNotIn('repair', [op for op, _ in bridge.events])
                        self.assertEqual(finalized, [('terminal_fatal', 'reaped',
                            'malformed' if boundary == 'validate' else 'serialization_failed')])

    def test_fatal_prose_never_bypasses_strict_whole_document_authentication(self):
        import sys
        core = '{"ok":false,"error":"public worker failed","fatalOwnedCleanup":true'
        streams = [core+',"diagnostic":{"private":'+token+'}}' for token in
            ('NaN', 'Infinity', '-Infinity', '1e999', '-1e999', '9'*(sys.get_int_max_str_digits()+1))]
        valid = core+'}'
        streams += [core+',"diagnostic":{"x":1,"x":2}}', valid+valid, valid+' trailing', core+',"ok":false}']
        for wire in streams:
            with patch.object(c, 'validate_diagnostic') as validate, patch.object(c, 'canonical_json') as encode:
                with self.assertRaises(c.OwnedCleanupError): c.parse_worker_terminal(wire, 1, 'author')
                validate.assert_not_called()
                encode.assert_not_called()


if __name__ == '__main__':
    unittest.main()
