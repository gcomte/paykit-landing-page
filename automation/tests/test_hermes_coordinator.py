"""Stdlib tests. SDK and transport substitutes below are explicit fixtures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import unittest.mock

SPEC = importlib.util.spec_from_file_location("hermes_coordinator", Path(__file__).parents[1] / "hermes_coordinator.py")
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)

FIXTURE_POLICY_PATHS = (
    'AGENTS.md', 'automation/COORDINATOR.md', 'automation/README.md', 'automation/SOURCE-BOUNDARY.md',
    'automation/config.json', 'automation/corrections.json', 'automation/skills/coordinator.md',
    'automation/skills/sources.md', 'automation/skills/writing.md', 'automation/skills/diagrams.md',
    'automation/skills/evaluation.md')


class FixturePublicPolicies:
    """Synthetic public policy bytes only; never reads the live repository."""
    def __init__(self, role='author'):
        self.role, self.events = role, []
        self.texts = {path: 'PUBLIC_FIXTURE_POLICY ' + path + '\n' + ('é fixture policy\n'*500)
                      for path in FIXTURE_POLICY_PATHS}

    def __call__(self, op, **payload):
        import hashlib
        self.events.append((op, dict(payload)))
        if op == 'bootstrap':
            return {'protocolVersion': 1, 'runId': 'run-123', 'role': self.role, 'sources': {},
                    'inputPaths': list(self.texts), 'versions': list(c.VERSIONS),
                    'requiredPolicyPaths': list(FIXTURE_POLICY_PATHS), 'deltaPlan': {}, 'repairFeedback': None}
        if op == 'read_inputs':
            text = self.texts[payload['path']]
            offset, end = payload['offset'], min(payload['offset'] + payload['limit'], len(text))
            return {'text': text[offset:end], 'offset': offset, 'nextOffset': end if end < len(text) else None,
                    'totalChars': len(text), 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
        return None


class FixturePublicReview(FixturePublicPolicies):
    """Offline public temporary corpus; not a production candidate or verdict."""
    def __init__(self, role='reviewer'):
        super().__init__(role)
        self.temp = tempfile.TemporaryDirectory(prefix='offline-public-review-')
        root = Path(self.temp.name)
        self.blueprint_texts = {}
        for version in c.VERSIONS:
            text = json.dumps({'sourceCommit': 'b'*40, 'pages': [
                {'fixture': version + ' é🦀 PUBLIC_UNTRUSTED_CANDIDATE\n'*900}],
                'evidence': [], 'diagrams': []}, ensure_ascii=False, indent=2) + '\n'
            path = root/'automation/blueprints'/f'{version}.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode('utf-8'))
            self.blueprint_texts[version] = path.read_bytes().decode('utf-8')

    def __call__(self, op, **payload):
        result = super().__call__(op, **payload)
        if op == 'get_review_inputs':
            return {'artifactHash': 'a'*64, 'versions': list(c.VERSIONS),
                'inputPaths': list(self.texts) + [f'automation/blueprints/{v}.json' for v in c.VERSIONS]}
        if op == 'read_blueprint':
            text = self.blueprint_texts[payload['version']]
            offset, end = payload['offset'], min(payload['offset'] + payload['limit'], len(text))
            return {'text': text[offset:end], 'offset': offset, 'nextOffset': end if end < len(text) else None,
                'totalChars': len(text), 'sha256': c.hashlib.sha256(text.encode('utf-8')).hexdigest()}
        return result


class BoundaryTests(unittest.TestCase):
    def test_assignment_rejects_private_extra_fields(self):
        assignment = dict(runId="run-123", role="author", deadline=time.time()+60,
                          workerId="worker-123", artifactHash=None, version=None, audience=None)
        self.assertEqual(c.validate_assignment(assignment)["role"], "author")
        for key in ("privateSummary", "history", "instructions", "environment", "command"):
            with self.subTest(key=key), self.assertRaises(c.CoordinatorError):
                c.validate_assignment(dict(assignment, **{key: "FIXTURE_PRIVATE_CANARY"}))


class FixtureBridge:
    """Deliberately synthetic transport, not host/runtime attestation."""
    def __init__(self, no_change=False, failures=0):
        self.events = []
        self.no_change = no_change
        self.failures = failures
        self.hash = "a"*64

    def __call__(self, op, **payload):
        self.events.append((op, payload))
        if op == "begin":
            return {"action": "no-change"} if self.no_change else {"action": "update", "runId": "run-123", "deadline": time.time()+1000}
        if op == "artifact_hash":
            return {"artifactHash": self.hash}
        if op == "validate" and self.failures:
            self.failures -= 1
            raise c.CoordinatorError("fixture validation failure")
        if op == "publish":
            return {"gitCommit": "b"*40}
        if op == "verify":
            return {"verifiedDeployment": True, "gitCommit": "b"*40, "stableAliasVerified": True}
        if op == "bootstrap":
            return {"sources": ["approved-public-source"], "instructions": "Public fixture guidance"}
        if op == "walkthrough_prepare":
            text = (f"Public fixture guide\ntar -xzf paykit-{payload['audience']}-{payload['version']}.tar.gz\n"
                    f"cd paykit-{payload['audience']}-demo\ncargo run --locked")
            return {"artifactHash": self.hash, "guide": {"text": text}}
        if op == "submit_review":
            return {"saved": True}
        return {"ok": True}


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.bridge = FixtureBridge()
        self.assignment = dict(runId="run-123", role="author", deadline=time.time()+60,
                               workerId="worker-123", artifactHash=None, version=None, audience=None)
        self.tools = c.WorkerTools(self.assignment, self.bridge)

    def test_extra_arguments_fail_before_transport(self):
        for args in ({"privatePath": "/home/private"}, {"url": "https://private.invalid"}, {"command": "pwd"}):
            with self.assertRaises(c.CoordinatorError):
                self.tools.dispatch("paykit_public_bundle", args)
        self.assertEqual(self.bridge.events, [])

    def test_author_draft_tools_map_exact_arguments_and_bound_identity(self):
        revision = "d"*64
        calls = [
            ("paykit_draft_open", {"version": "released"}),
            ("paykit_draft_read", {"version": "released", "revision": revision,
                "selector": {"kind": "section", "pageSlug": "wallets", "sectionId": "fixture"},
                "offset": 0, "limit": 6000}),
            ("paykit_draft_edit", {"version": "coming-soon", "expectedRevision": revision,
                "edits": [{"op": "replaceSection", "pageSlug": "wallets", "sectionId": "fixture",
                    "section": {"id": "fixture", "title": "Fixture", "evidence": ["public-evidence"],
                                "paragraphs": ["Explicit public delta."]}}]}),
            ("paykit_draft_finalize", {"version": "released", "expectedRevision": revision}),
        ]
        for name, args in calls:
            with self.subTest(name=name):
                self.tools.dispatch(name, args)
                op, payload = self.bridge.events[-1]
                self.assertEqual(op, name.removeprefix("paykit_"))
                self.assertEqual({key: payload[key] for key in args}, args)
                self.assertEqual(payload["workerId"], self.assignment["workerId"])
                self.assertEqual(set(c.TOOL_FIELDS[name]), set(args))
        draft_names = {name for name, _ in calls}
        self.assertTrue(draft_names <= c.ROLE_TOOLS["author"])
        self.assertFalse(draft_names & c.ROLE_TOOLS["reviewer"])
        self.assertFalse(draft_names & c.ROLE_TOOLS["walker"])

    def test_draft_arguments_are_strict_and_bounded_before_transport(self):
        revision = "d"*64
        read = {"version": "released", "revision": revision, "selector": {"kind": "summary"}, "offset": 0, "limit": 6000}
        edit = {"version": "released", "expectedRevision": revision, "edits": [
            {"op": "replaceSection", "pageSlug": "wallets", "sectionId": "fixture",
             "section": {"id": "fixture", "title": "Fixture", "evidence": [], "paragraphs": ["Public fixture"]}}]}
        import copy
        invalid = [("paykit_draft_read", dict(read, revision=value)) for value in ("D"*64, "d"*63, None, 7)]
        invalid += [("paykit_draft_read", dict(read, selector=value)) for value in (
            {"kind": "summary", "path": "/private"}, {"kind": "pointer", "pointer": "/pages/0"},
            {"kind": "section", "pageSlug": "../wallets", "sectionId": "fixture"},
            {"kind": "section", "pageSlug": "wallets"}, {"kind": "evidence", "evidenceId": "x;pwd"})]
        invalid += [("paykit_draft_edit", dict(edit, edits=value)) for value in ([], edit["edits"]*17,
            [{"op": "remove", "path": "/pages/0"}], [{"op": "replaceSourceLinks", "pageSlug": "wallets", "sourceLinks": [], "privateSummary": "FIXTURE_PRIVATE"}])]
        for changes in ({"privateSummary": "FIXTURE_PRIVATE"}, {"code": [{"language": "sh", "caption": "Fixture", "code": "x", "command": "pwd"}]},
                        {"id": "other"}, {"paragraphs": ["é"*17000]}, {"diagram": {"id": "x", "caption": "Fixture", "alt": "Fixture", "path": "/private"}}):
            bad = copy.deepcopy(edit)
            bad["edits"][0]["section"].update(changes)
            invalid.append(("paykit_draft_edit", bad))
        for key in ("runId", "session", "deadline", "privateSummary", "command"):
            invalid.append(("paykit_draft_open", {"version": "released", key: "FIXTURE_PRIVATE"}))
        for name in c.DRAFT_TOOLS:
            args = {"paykit_draft_open": {"version": "released"}, "paykit_draft_read": read,
                    "paykit_draft_edit": edit, "paykit_draft_finalize": {"version": "released", "expectedRevision": revision}}[name]
            invalid.append((name, dict(args, version="master")))
        for name, args in invalid:
            with self.subTest(tool=name, args=str(args)[:160]), self.assertRaises(c.CoordinatorError):
                self.tools.dispatch(name, args)
        self.assertEqual(self.bridge.events, [])

    def test_all_draft_selectors_and_public_edit_shapes_are_supported(self):
        revision = "d"*64
        selectors = [{"kind": "summary"}, {"kind": "section", "pageSlug": "wallets", "sectionId": "fixture"},
                     {"kind": "evidence", "evidenceId": "public-evidence"}, {"kind": "sourceLinks", "pageSlug": "wallets"},
                     {"kind": "diagram", "diagramId": "flow"}]
        for selector in selectors:
            self.tools.dispatch("paykit_draft_read", {"version": "released", "revision": revision,
                "selector": selector, "offset": 0, "limit": 100})
        evidence = {"id": "public-evidence", "version": "released", "commit": "b"*40, "path": "src/lib.rs",
                    "startLine": 1, "endLine": 2, "excerpt": "public source", "sourceHash": "a"*64,
                    "kind": "implemented", "claim": "Public fixture", "anchor": {"start": "a", "end": "b"}}
        edits = [{"op": "putEvidence", "record": evidence},
                 {"op": "replaceSourceLinks", "pageSlug": "wallets", "sourceLinks": [{"href": "/docs/released/wallets", "label": "Fixture"}]},
                 {"op": "replaceDiagram", "diagramId": "flow", "diagram": {"id": "flow", "mermaid": "flowchart TD; A-->B",
                    "caption": "Fixture", "alt": "Fixture", "relationships": [{"text": "A to B", "evidence": ["public-evidence"]}]}}]
        self.tools.dispatch("paykit_draft_edit", {"version": "released", "expectedRevision": revision, "edits": edits})
        self.assertEqual(self.bridge.events[-1][1]["edits"], edits)
        for change in ({"version": "coming-soon"}, {"path": "../private"}, {"sourceHash": "A"*64}, {"startLine": True}):
            with self.subTest(change=change), self.assertRaises(c.CoordinatorError):
                self.tools.dispatch("paykit_draft_edit", {"version": "released", "expectedRevision": revision,
                    "edits": [{"op": "putEvidence", "record": dict(evidence, **change)}]})

    def test_author_completion_requires_saved_finalization_for_both_versions(self):
        revision = "d"*64
        response = {"version": "released", "revision": revision, "saved": False, "sha256": "a"*64, "unchanged": True}
        tools = c.WorkerTools(self.assignment, lambda *a, **k: dict(response))
        for version in c.VERSIONS:
            response["version"] = version
            tools.dispatch("paykit_draft_open", {"version": version})
            tools.dispatch("paykit_draft_finalize", {"version": version, "expectedRevision": revision})
        with self.assertRaises(c.CoordinatorError): tools.require_completion()
        for saved in (None, 1, "true"):
            response["saved"] = saved
            tools.dispatch("paykit_draft_finalize", {"version": "coming-soon", "expectedRevision": revision})
            with self.assertRaises(c.CoordinatorError): tools.require_completion()
        response["saved"] = True
        for version in c.VERSIONS:
            response["version"] = version
            tools.dispatch("paykit_draft_finalize", {"version": version, "expectedRevision": revision})
        tools.require_completion()
        self.assertTrue({"paykit_submit_blueprint:" + v for v in c.VERSIONS} <= tools.completed)
        tools.dispatch("paykit_draft_edit", {"version": "coming-soon", "expectedRevision": revision,
            "edits": [{"op": "replaceSourceLinks", "pageSlug": "wallets", "sourceLinks": []}]})
        with self.assertRaises(c.CoordinatorError): tools.require_completion()

    def test_uncertain_mutation_invalidates_only_its_version_before_io(self):
        from unittest.mock import patch
        revision = 'd'*64
        for name in ('paykit_submit_blueprint', 'paykit_draft_edit'):
            for outcome in ('lost_response', 'invalid_reply', 'nondict', 'deadline'):
                with self.subTest(name=name, outcome=outcome):
                    def transport(op, **payload):
                        if op == 'draft_finalize':
                            return dict(version=payload['version'], revision=revision, saved=True,
                                        sha256='a'*64, unchanged=True)
                        self.assertNotIn('paykit_submit_blueprint:released', tools.completed)
                        if outcome in ('lost_response', 'invalid_reply'):
                            raise c.CoordinatorError(outcome)
                        return None if outcome == 'nondict' else {'saved': True}
                    tools = c.WorkerTools(self.assignment, transport)
                    for version in c.VERSIONS:
                        tools.dispatch('paykit_draft_finalize', dict(version=version, expectedRevision=revision))
                    tools.require_completion()
                    args = dict(version='released', blueprint={}) if name == 'paykit_submit_blueprint' else dict(
                        version='released', expectedRevision=revision,
                        edits=[dict(op='replaceSourceLinks', pageSlug='wallets', sourceLinks=[])])
                    with patch.object(c, 'remaining', side_effect=[1, c.CoordinatorError('deadline expired')]), self.assertRaises(c.CoordinatorError):
                        tools.dispatch(name, args)
                    self.assertIn('paykit_submit_blueprint:coming-soon', tools.completed)
                    with self.assertRaises(c.CoordinatorError): tools.require_completion()

    def test_late_finalization_ack_cannot_restore_completion_after_mutation(self):
        import threading
        revision = 'd'*64
        for name in ('paykit_submit_blueprint', 'paykit_draft_edit'):
            for failed in (False, True):
                with self.subTest(name=name, failed=failed):
                    ready, release = threading.Event(), threading.Event()
                    hold, errors, writes = [], [], []
                    def transport(op, **payload):
                        if op == 'draft_finalize':
                            if hold and payload['version'] == 'released':
                                ready.set()
                                if not release.wait(5): raise RuntimeError('barrier timed out')
                            return dict(version=payload['version'], revision=revision, saved=True,
                                        sha256='a'*64, unchanged=True)
                        writes.append(op)
                        if failed: raise c.CoordinatorError('lost mutation response')
                        return {'saved': True}
                    tools = c.WorkerTools(self.assignment, transport)
                    for version in c.VERSIONS:
                        tools.dispatch('paykit_draft_finalize', dict(version=version, expectedRevision=revision))
                    hold.append(True)
                    def finalize():
                        try: tools.dispatch('paykit_draft_finalize', dict(version='released', expectedRevision=revision))
                        except BaseException as error: errors.append(error)
                    worker = threading.Thread(target=finalize)
                    worker.start()
                    try:
                        self.assertTrue(ready.wait(5))
                        args = dict(version='released', blueprint={}) if name == 'paykit_submit_blueprint' else dict(
                            version='released', expectedRevision=revision,
                            edits=[dict(op='replaceSourceLinks', pageSlug='wallets', sourceLinks=[])])
                        if failed:
                            with self.assertRaises(c.CoordinatorError): tools.dispatch(name, args)
                        else:
                            tools.dispatch(name, args)
                    finally:
                        release.set()
                        worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(writes, [name.removeprefix('paykit_')])
                    self.assertIn('paykit_submit_blueprint:coming-soon', tools.completed)
                    with self.assertRaises(c.CoordinatorError): tools.require_completion()

    def test_legacy_submit_without_saved_confirmation_does_not_complete(self):
        for version in c.VERSIONS:
            self.tools.dispatch("paykit_submit_blueprint", {"version": version, "blueprint": {}})
        with self.assertRaises(c.CoordinatorError): self.tools.require_completion()

    def test_legacy_saved_submission_cannot_replace_draft_finalization(self):
        tools = c.WorkerTools(self.assignment, lambda *a, **k: {'saved': True, 'sha256': 'a'*64})
        for version in c.VERSIONS:
            tools.dispatch('paykit_submit_blueprint', {'version': version, 'blueprint': {}})
        with self.assertRaises(c.CoordinatorError): tools.require_completion()
        self.assertFalse(any(token.startswith('paykit_submit_blueprint:') for token in tools.completed))

    def test_saved_draft_receipt_must_match_exact_version_revision_and_digest(self):
        revision = 'd'*64
        good = {'version': 'released', 'revision': revision, 'saved': True, 'sha256': 'a'*64, 'unchanged': True}
        for patch in ({'version': 'coming-soon'}, {'revision': 'e'*64}, {'sha256': 'A'*64},
                      {'unchanged': 1}, {'privateSummary': 'FIXTURE_PRIVATE'}):
            with self.subTest(patch=patch):
                tools = c.WorkerTools(self.assignment, lambda *a, **k: dict(good, **patch))
                with self.assertRaises(c.CoordinatorError):
                    tools.dispatch('paykit_draft_finalize', {'version': 'released', 'expectedRevision': revision})
                self.assertNotIn('paykit_submit_blueprint:released', tools.completed)

    def test_draft_batch_exact_byte_and_operation_boundaries(self):
        edit = {'op': 'replaceSection', 'pageSlug': 'wallets', 'sectionId': 'fixture',
                'section': {'id': 'fixture', 'title': 'Fixture', 'evidence': [], 'paragraphs': ['x']}}
        args = {'version': 'released', 'expectedRevision': 'd'*64, 'edits': [edit]}
        base = len(c.json_value(args).encode('utf-8'))
        edit['section']['paragraphs'][0] += 'x'*(32768-base)
        self.assertEqual(len(c.json_value(args).encode('utf-8')), 32768)
        self.tools.dispatch('paykit_draft_edit', args)
        edit['section']['paragraphs'][0] += 'x'
        count = len(self.bridge.events)
        with self.assertRaises(c.CoordinatorError): self.tools.dispatch('paykit_draft_edit', args)
        self.assertEqual(len(self.bridge.events), count)
        args['edits'] = [{'op': 'replaceSourceLinks', 'pageSlug': 'wallets', 'sourceLinks': []}]*16
        self.tools.dispatch('paykit_draft_edit', args)
        args['edits'].append(args['edits'][0])
        count = len(self.bridge.events)
        with self.assertRaises(c.CoordinatorError): self.tools.dispatch('paykit_draft_edit', args)
        self.assertEqual(len(self.bridge.events), count)

    def test_role_separation(self):
        reviewer = c.WorkerTools(dict(self.assignment, role="reviewer", artifactHash="a"*64), self.bridge)
        with self.assertRaises(c.CoordinatorError):
            reviewer.dispatch("paykit_submit_blueprint", {"version": "released", "blueprint": {}})
        with self.assertRaises(c.CoordinatorError):
            self.tools.dispatch("paykit_submit_review", {"status": "pass", "findings": [], "versions": [], "notes": ""})
        self.assertEqual(self.bridge.events, [])

    def test_snapshot_reference_is_not_arbitrary_path(self):
        for source in ("../../private", "/home/hermes/.env", "https://private.invalid", "x;pwd"):
            with self.assertRaises(c.CoordinatorError):
                self.tools.dispatch("paykit_source_read", {"version": "released", "path": source, "offset": 0, "limit": 100})

    def test_review_binding_is_injected_not_model_controlled(self):
        tools = c.WorkerTools(dict(self.assignment, role="reviewer", artifactHash="a"*64), self.bridge)
        tools.dispatch("paykit_submit_review", {"status": "pass", "findings": [], "versions": ["released", "coming-soon"], "notes": "fixture"})
        op, payload = self.bridge.events[-1]
        self.assertEqual(op, "submit_review")
        self.assertEqual(payload["report"]["artifactHash"], "a"*64)
        self.assertEqual(payload["workerId"], "worker-123")
        self.assertNotIn("reviewerRunId", payload["report"])

    def test_review_pass_requires_saved_confirmation(self):
        confirmed = c.WorkerTools(dict(self.assignment, role="reviewer", artifactHash="a"*64), self.bridge)
        confirmed.dispatch("paykit_submit_review", {"status": "pass", "findings": [],
                           "versions": list(c.VERSIONS), "notes": "fixture"})
        self.assertIs(getattr(confirmed, "review_passed", None), True)
        tools = c.WorkerTools(dict(self.assignment, role="reviewer", artifactHash="a"*64),
                              lambda *a, **k: {"ok": True})
        tools.dispatch("paykit_submit_review", {"status": "pass", "findings": [],
                       "versions": list(c.VERSIONS), "notes": "fixture"})
        self.assertIsNot(getattr(tools, "review_passed", None), True)

    def test_walker_forwards_only_supplied_literal_commands(self):
        tools = c.WorkerTools(dict(self.assignment, role="walker", artifactHash="a"*64,
                                  version="released", audience="wallet"), self.bridge)
        commands = ["tar -xzf paykit-wallet-released.tar.gz", "cd paykit-wallet-demo", "cargo run --locked"]
        with self.assertRaises(c.CoordinatorError):
            tools.dispatch("paykit_run_literal_plan", {"commands": commands})
        guide = tools.dispatch("paykit_rendered_guide", {"offset": 0, "limit": 6000})
        commands = guide["guide"]["text"].splitlines()[1:]
        count = len(self.bridge.events)
        for args in ({}, {"commands": ["cat /home/private"]},
                     {"commands": [commands[0], commands[1], "cargo run --locked; pwd"]},
                     {"commands": [commands[0], commands[1], 42]}):
            with self.assertRaises(c.CoordinatorError):
                tools.dispatch("paykit_run_literal_plan", args)
        self.assertEqual(len(self.bridge.events), count)
        tools.dispatch("paykit_run_literal_plan", {"commands": commands})
        self.assertEqual(self.bridge.events[-1][0], "walkthrough_run")
        self.assertIs(self.bridge.events[-1][1]["commands"], commands)
        schema = c.TOOL_FIELDS["paykit_run_literal_plan"]["commands"]
        self.assertEqual((schema["minItems"], schema["maxItems"]), (3, 3))
        self.assertEqual(schema["items"], {"type": "string"})
        self.assertIn("extract", c.ROLE_PROMPTS["walker"])
        self.assertIn("unchanged", c.ROLE_PROMPTS["walker"])

    def test_unexpected_native_tools_are_rejected(self):
        expected = self.tools.names
        with self.assertRaises(c.CoordinatorError):
            c.assert_grant([{"type": "function", "function": {"name": n}} for n in expected | {"terminal"}], expected)
        c.assert_grant([{"type": "function", "function": {"name": n}} for n in expected], expected)

    def test_expired_worker_cannot_call_transport(self):
        self.tools.assignment["deadline"] = time.time()-1
        with self.assertRaises(c.CoordinatorError):
            self.tools.dispatch("paykit_public_bundle", {})
        self.assertEqual(self.bridge.events, [])


class LifecycleTests(unittest.TestCase):
    def run_fixture(self, bridge, private=None, worker=None, **kwargs):
        calls = []
        def fixture_worker(assignment):
            calls.append(assignment)
            return {"ok": True, "reviewPassed": True} if assignment["role"] == "reviewer" else {"ok": True}
        coordinator = c.Coordinator(bridge, worker or fixture_worker,
            private or (lambda: "FIXTURE_PRIVATE_CANARY_WITH_PRIVATE_PRIORITIES"),
            deadline=time.time()+1200, **kwargs)
        return coordinator, calls

    def test_role_allocations_preserve_the_global_hard_deadline(self):
        now, assignments = 1000.0, []
        def capture(assignment):
            assignments.append(assignment)
            return {"ok": True, "reviewPassed": True}
        with unittest.mock.patch.object(c.time, 'time', return_value=now):
            coordinator = c.Coordinator(FixtureBridge(), capture, lambda: None,
                                        deadline=now+10000)
            coordinator.run_id = 'run-123'
            self.assertEqual(c.MAX_SECONDS, 3500)
            self.assertEqual(coordinator.deadline, now+3500)
            for role, seconds in (('author', 500), ('reviewer', 600), ('walker', 300)):
                with self.subTest(role=role):
                    coordinator.worker(role, 'a'*64 if role != 'author' else None,
                        'released' if role == 'walker' else None,
                        'wallet' if role == 'walker' else None)
                    self.assertEqual(assignments[-1]['deadline'], now+seconds)
                    self.assertLessEqual(assignments[-1]['deadline'], coordinator.deadline-15)
            self.assertEqual(coordinator.deadline, now+3500)
        self.assertEqual(len(assignments), 3)

    def test_reviewer_allocation_is_clamped_to_remaining_lease_with_reserve(self):
        now = 1000.0
        for lease_seconds in (1000, 615, 614, 60, 16):
            with self.subTest(lease_seconds=lease_seconds), \
                 unittest.mock.patch.object(c.time, 'time', return_value=now):
                assignments = []
                def capture(assignment):
                    assignments.append(assignment)
                    return {"ok": True, "reviewPassed": True}
                coordinator = c.Coordinator(FixtureBridge(), capture, lambda: None,
                                            deadline=now+lease_seconds)
                coordinator.run_id = 'run-123'
                coordinator.worker('reviewer', 'a'*64)
                self.assertEqual(assignments[0]['deadline'], now+min(600, lease_seconds-15))
                self.assertEqual(coordinator.deadline, now+lease_seconds)

    def test_insufficient_lease_never_starts_a_reviewer(self):
        now = 1000.0
        for lease_seconds in (15, 14, 0, -1):
            with self.subTest(lease_seconds=lease_seconds), \
                 unittest.mock.patch.object(c.time, 'time', return_value=now):
                worker = unittest.mock.Mock()
                coordinator = c.Coordinator(FixtureBridge(), worker, lambda: None,
                                            deadline=now+lease_seconds)
                coordinator.run_id = 'run-123'
                with self.assertRaises(c.CoordinatorError):
                    coordinator.worker('reviewer', 'a'*64)
                worker.assert_not_called()

    def test_private_failure_prevents_begin(self):
        bridge = FixtureBridge()
        def unavailable():
            raise RuntimeError("FIXTURE_PRIVATE_EXCEPTION")
        coordinator, calls = self.run_fixture(bridge, private=unavailable)
        with self.assertRaises(c.CoordinatorError) as caught:
            coordinator.run()
        self.assertNotIn("FIXTURE_PRIVATE", str(caught.exception))
        self.assertEqual(bridge.events, [])
        self.assertEqual(calls, [])

    def test_no_change_skips_every_worker_and_publication(self):
        bridge = FixtureBridge(no_change=True)
        coordinator, calls = self.run_fixture(bridge)
        self.assertEqual(coordinator.run()["action"], "no-change")
        self.assertEqual(calls, [])
        self.assertEqual([op for op, _ in bridge.events], ["runtime_attestation", "begin"])
        self.assertNotIn("FIXTURE_PRIVATE", json.dumps(bridge.events))

    def test_full_lifecycle_fresh_identities_and_hash_bound_reports(self):
        bridge = FixtureBridge()
        coordinator, calls = self.run_fixture(bridge)
        result = coordinator.run()
        self.assertEqual(result["action"], "published")
        self.assertEqual([a["role"] for a in calls], ["author", "reviewer"] + ["walker"]*4)
        self.assertEqual(len({a["workerId"] for a in calls}), 6)
        self.assertTrue(all(a["artifactHash"] == "a"*64 for a in calls[1:]))
        events = [op for op, _ in bridge.events]
        self.assertLess(events.index("validate"), events.index("publish"))
        self.assertEqual(events[-1], "verify")
        self.assertNotIn("FIXTURE_PRIVATE", json.dumps(calls)+json.dumps(bridge.events))

    def test_failed_or_unconfirmed_review_repairs_before_walkers(self):
        for review_result in ({"ok": True, "reviewPassed": False}, {"ok": True}):
            bridge = FixtureBridge()
            roles = []
            def worker(assignment):
                roles.append(assignment["role"])
                return review_result if assignment["role"] == "reviewer" else {"ok": True}
            coordinator, _ = self.run_fixture(bridge, worker=worker)
            with self.assertRaises(c.CoordinatorError): coordinator.run()
            self.assertEqual(roles, ["author", "reviewer"]*3)
            events = [op for op, _ in bridge.events]
            self.assertEqual(events.count("repair"), 2)
            self.assertNotIn("validate", events)
            self.assertNotIn("publish", events)
            self.assertEqual(events[-1], "fail")

    def test_two_repairs_then_fail_without_publish(self):
        bridge = FixtureBridge(failures=3)
        coordinator, calls = self.run_fixture(bridge)
        with self.assertRaises(c.CoordinatorError):
            coordinator.run()
        events = [op for op, _ in bridge.events]
        self.assertEqual(events.count("repair"), 2)
        self.assertNotIn("publish", events)
        self.assertEqual(events[-1], "fail")
        self.assertEqual(len({a["workerId"] for a in calls}), len(calls))

    def test_failure_cleanup_preserves_safe_reason(self):
        bridge = FixtureBridge()
        def fail_worker(a):
            raise RuntimeError("FIXTURE_PRIVATE_WORKER_OUTPUT")
        coordinator, calls = self.run_fixture(bridge, worker=fail_worker)
        with self.assertRaises(c.CoordinatorError):
            coordinator.run()
        self.assertEqual(bridge.events[-1][0], "fail")
        self.assertNotIn("FIXTURE_PRIVATE", json.dumps(bridge.events))

    def test_pending_verification_polls_then_accepts_confirmed_sha(self):
        bridge = FixtureBridge()
        coordinator, _ = self.run_fixture(bridge)
        clock = [time.time()]
        attempts = []
        def pending(op, **payload):
            if op == "verify":
                attempts.append(clock[0])
                if len(attempts) <= 2:
                    return {"pending": True}
            return bridge(op, **payload)
        coordinator.bridge = pending
        with unittest.mock.patch.object(c.time, "time", side_effect=lambda: clock[0]), \
             unittest.mock.patch.object(c.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
            self.assertEqual(coordinator.run()["action"], "published")
        self.assertEqual(len(attempts), 3)
        self.assertGreater(attempts[-1], attempts[0])

    def test_pending_verification_timeout_respects_cap_and_job_deadline(self):
        for budget in (120, 1200):
            with self.subTest(budget=budget):
                bridge = FixtureBridge()
                coordinator, _ = self.run_fixture(bridge)
                started = time.time()
                clock = [started]
                coordinator.deadline = started + budget
                attempts = []
                def pending(op, **payload):
                    if op == "verify":
                        attempts.append(clock[0])
                        return {"pending": True}
                    return bridge(op, **payload)
                coordinator.bridge = pending
                with unittest.mock.patch.object(c.time, "time", side_effect=lambda: clock[0]), \
                     unittest.mock.patch.object(c.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
                    with self.assertRaises(c.CoordinatorError): coordinator.run()
                self.assertGreater(len(attempts), 1)
                self.assertEqual(clock[0]-started, min(600, budget))
                self.assertEqual(bridge.events[-1][0], "fail")

    def test_unverified_deployment_fails_closed(self):
        bridge = FixtureBridge()
        original = bridge.__call__
        def invalid_verify(op, **payload):
            if op == "verify":
                return {"verifiedDeployment": True, "gitCommit": "c"*40, "stableAliasVerified": True}
            return original(op, **payload)
        coordinator, calls = self.run_fixture(invalid_verify)
        with self.assertRaises(c.CoordinatorError):
            coordinator.run()
        self.assertEqual(bridge.events[-1][0], "fail")

    def test_no_publish_mode_never_pushes_or_checkpoints(self):
        bridge = FixtureBridge()
        coordinator, calls = self.run_fixture(bridge, no_publish=True)
        self.assertEqual(coordinator.run()["action"], "validated-no-publish")
        events = [op for op, _ in bridge.events]
        self.assertNotIn("publish", events)
        self.assertNotIn("verify", events)
        self.assertEqual(events[-1], "fail")


class FinalizerTests(unittest.TestCase):
    """Protocol fault injection only: no real PIDs, signals or native SDK."""
    def test_fresh_worker_finalizer_faults_never_repair_or_start_worker_two(self):
        import sys
        for boundary in ('killpg', 'wait', 'poll'):
            for prior_fatal in (True, False):
                for no_publish in (False, True):
                    with self.subTest(boundary=boundary, prior_fatal=prior_fatal, no_publish=no_publish):
                        fixture, roles = FixtureBridge(), []
                        result = {'ok': False, 'error': 'public worker failed'}
                        if prior_fatal:
                            result['fatalOwnedCleanup'] = True
                        proc = unittest.mock.Mock(pid=123456789, returncode=1)
                        proc.communicate.return_value = (json.dumps(result), None)
                        proc.poll.return_value = 1
                        if boundary != 'killpg':
                            getattr(proc, boundary).side_effect = OSError('FIXTURE_PRIVATE_FINALIZER')
                        def worker(assignment):
                            roles.append(assignment['role'])
                            return c.fresh_worker(assignment)
                        with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                             unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc) as popen, \
                             unittest.mock.patch.object(c.os, 'killpg', side_effect=PermissionError('FIXTURE_PRIVATE_FINALIZER') if boundary == 'killpg' else None):
                            with self.assertRaises(c.CoordinatorError) as caught:
                                c.Coordinator(fixture, worker, lambda: None, no_publish=no_publish).run()
                        self.assertIsInstance(caught.exception, c.OwnedCleanupError)
                        self.assertNotIn('FIXTURE_PRIVATE', str(caught.exception))
                        self.assertEqual(popen.call_count, 1)
                        self.assertEqual(roles, ['author'])
                        self.assertEqual([op for op, _ in fixture.events],
                                         ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])


    def test_outer_worker_directory_finalizer_cannot_downgrade_fatal(self):
        import sys
        original_exit = tempfile.TemporaryDirectory.__exit__
        def broken_exit(directory, *args):
            original_exit(directory, *args)
            raise OSError('FIXTURE_PRIVATE_DIRECTORY_CLEANUP')
        for prior_fatal in (True, False):
            with self.subTest(prior_fatal=prior_fatal):
                def fatal_bridge(*args, **kwargs):
                    raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
                with unittest.mock.patch.object(tempfile.TemporaryDirectory, '__exit__', broken_exit):
                    state = RuntimeTests().native_fixture(probe=False, protocol=True,
                        bridge_override=fatal_bridge if prior_fatal else None)
                with self.subTest(boundary='native'):
                    self.assertEqual(state.get('result'),
                        {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
                fixture, roles = FixtureBridge(), []
                proc = unittest.mock.Mock(pid=123456789, returncode=1)
                proc.communicate.return_value = (json.dumps(
                    {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True}), None)
                proc.poll.return_value = 1
                def worker(assignment):
                    roles.append(assignment['role'])
                    return c.fresh_worker(assignment)
                with unittest.mock.patch.object(tempfile.TemporaryDirectory, '__exit__', broken_exit), \
                     unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                     unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc) as popen, \
                     unittest.mock.patch.object(c.os, 'killpg'):
                    with self.assertRaises(c.CoordinatorError) as caught:
                        c.Coordinator(fixture, worker, lambda: None, no_publish=True).run()
                self.assertIsInstance(caught.exception, c.OwnedCleanupError)
                self.assertEqual(popen.call_count, 1)
                self.assertEqual(roles, ['author'])
                self.assertEqual([op for op, _ in fixture.events],
                                 ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])

    def test_worker_alarm_finalizer_preserves_fatal_protocol(self):
        import contextlib
        @contextlib.contextmanager
        def broken_alarm(deadline):
            try:
                yield
            finally:
                raise OSError('FIXTURE_PRIVATE_ALARM_RESTORE')
        with unittest.mock.patch.object(c, 'bounded_alarm', broken_alarm):
            state = RuntimeTests().native_fixture(probe=False, protocol=True, close_error=True)
        self.assertEqual(state.get('result'),
            {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})


    def test_worker_stream_finalizer_preserves_fatal_protocol(self):
        original_exit = c.contextlib.redirect_stderr.__exit__
        def broken_exit(manager, *args):
            original_exit(manager, *args)
            raise OSError('FIXTURE_PRIVATE_STREAM_RESTORE')
        with unittest.mock.patch.object(c.contextlib.redirect_stderr, '__exit__', broken_exit):
            state = RuntimeTests().native_fixture(probe=False, protocol=True, close_error=True)
        self.assertEqual(state.get('result'),
            {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})

    def test_fatal_protocol_requires_exact_boolean_fields(self):
        import sys
        assignment = dict(runId='run-123', role='author', workerId='fixture-worker',
                          deadline=time.time()+60, artifactHash=None, version=None, audience=None)
        for result in ({'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': 1},
                       {'ok': 0, 'error': 'public worker failed', 'fatalOwnedCleanup': True},
                       {'ok': False, 'error': 'fatalOwnedCleanup true'},
                       {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True, 'extra': True}):
            with self.subTest(result=result):
                proc = unittest.mock.Mock(pid=123456789, returncode=1)
                proc.communicate.return_value = (json.dumps(result), None)
                proc.poll.return_value = 1
                with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                     unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc), \
                     unittest.mock.patch.object(c.os, 'killpg'):
                    with self.assertRaises(c.CoordinatorError) as caught:
                        c.fresh_worker(assignment)
                self.assertIsInstance(caught.exception, c.OwnedCleanupError)
                self.assertEqual(str(caught.exception), 'worker terminal protocol or cleanup uncertain')

    def test_interrupts_survive_confirmed_cleanup_but_not_uncertain_cleanup(self):
        import sys
        assignment = dict(runId='run-123', role='author', workerId='fixture-worker',
                          deadline=time.time()+60, artifactHash=None, version=None, audience=None)
        for error_type in (KeyboardInterrupt, SystemExit):
            for uncertain in (False, True):
                with self.subTest(error=error_type.__name__, uncertain=uncertain):
                    state = RuntimeTests().native_fixture(probe=False, interrupt=error_type(), close_error=uncertain)
                    self.assertIs(state['errorType'], c.OwnedCleanupError if uncertain else error_type)
                    self.assertTrue(state['closed'])
                    self.assertTrue(state['restoration_attempted'])
                    proc = unittest.mock.Mock(pid=123456789)
                    proc.communicate.side_effect = error_type()
                    proc.poll.return_value = 1
                    with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                         unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc), \
                         unittest.mock.patch.object(c.os, 'killpg', side_effect=OSError('FIXTURE_CONTROL') if uncertain else None):
                        with self.assertRaises(c.OwnedCleanupError if uncertain else error_type):
                            c.fresh_worker(assignment)


class NativeTransportTests(unittest.TestCase):
    """Explicit SDK protocol fixtures, not a live native runtime certificate."""
    def test_nt1_uncertain_native_cleanup_aborts_coordinator_without_repair(self):
        # Actual adapter + coordinator, supported synthetic lifecycle responses.
        # A tempting retry is deliberately available: taking it is the bug.
        for no_publish in (False, True):
            with self.subTest(no_publish=no_publish):
                fixture, actions, workers = FixtureBridge(), [], []
                terminal = unittest.mock.Mock(side_effect=[{'session_id': 'proc_cleanup_failed'},
                                                          {'session_id': 'proc_retry'}])
                encoded = c.encode_message({'ok': True, 'result': {'ok': True}})
                def process(args, **kwargs):
                    actions.append((args['action'], args['session_id']))
                    if args['session_id'] == 'proc_cleanup_failed':
                        if args['action'] == 'wait':
                            raise RuntimeError('FIXTURE_PRIVATE_CONTROL_FAILURE')
                        return {'status': 'killed', 'scope_stop_failed': True, 'process_running': True}
                    return {'status': 'exited', 'exit_code': 0, 'output': encoded}
                native = c.NativeBridge(terminal_tool=terminal, process_handler=process,
                                        deadline=time.time()+1000)
                def bridge(op, **payload):
                    if op == 'examples':
                        fixture.events.append((op, payload))
                        return native(op, **payload)
                    return fixture(op, **payload)
                def worker(assignment):
                    workers.append(assignment['role'])
                    return {'ok': True, 'reviewPassed': True}
                coordinator = c.Coordinator(bridge, worker, lambda: None,
                                            deadline=time.time()+1000, no_publish=no_publish)
                with self.assertRaises(c.CoordinatorError) as caught:
                    coordinator.run()
                self.assertIsInstance(caught.exception, c.OwnedCleanupError)
                self.assertNotIn('FIXTURE_PRIVATE', str(caught.exception))
                self.assertEqual(terminal.call_count, 1)
                self.assertEqual(actions, [('wait', 'proc_cleanup_failed'), ('kill', 'proc_cleanup_failed')])
                self.assertEqual(workers, ['author'])
                events = [op for op, _ in fixture.events]
                self.assertEqual(events[events.index('examples')+1:], ['fail'])
                self.assertNotIn('repair', events)
                self.assertNotIn('publish', events)
                self.assertNotIn('verify', events)

    def test_promoted_start_waits_for_owned_completion_without_rerun(self):
        terminal = unittest.mock.Mock(return_value=json.dumps({
            "output": "Background process started", "session_id": "proc_fixture_owned",
            "pid": 123, "exit_code": 0, "promoted_from_foreground": "fixture promotion"}))
        encoded = c.encode_message({"ok": True, "result": {"ok": True}})
        process = unittest.mock.Mock(side_effect=[
            json.dumps({"status": "running", "output": encoded, "exit_code": 0}),
            json.dumps({"status": "timeout", "process_running": True, "output": encoded}),
            json.dumps({"status": "exited", "exit_code": 0, "output": encoded})])
        bridge = c.NativeBridge(terminal_tool=terminal, deadline=time.time()+3400)
        bridge.process_handler = process
        with unittest.mock.patch.object(c.time, "sleep") as sleep:
            self.assertEqual(bridge("runtime_attestation", attestation={}), {"ok": True})
        terminal.assert_called_once()
        self.assertLessEqual(terminal.call_args.kwargs["timeout"], 600)
        self.assertEqual(process.call_count, 3)
        for call in process.call_args_list:
            self.assertEqual(call.args[0]["action"], "wait")
            self.assertEqual(call.args[0]["session_id"], "proc_fixture_owned")
            self.assertLessEqual(call.args[0]["timeout"], 30)
            self.assertEqual(call.kwargs, {"task_id": bridge.task_id})
        self.assertEqual(sleep.call_count, 2)


    def test_owned_failure_cancels_only_captured_session_with_safe_error(self):
        encoded = c.encode_message({"ok": True, "result": {"ok": True}})
        failures = [
            {"status": "exited", "exit_code": 2, "output": encoded},
            {"status": "exited", "exit_code": 0, "output": "FIXTURE_PRIVATE_CANARY"},
            {"status": "exited", "exit_code": 0, "output": None},
            {"status": "exited", "exit_code": True, "output": encoded},
            {"status": "exited", "exit_code": 0, "output": c.encode_message({"ok": False})},
            {"status": "unknown", "exit_code": 0, "output": encoded},
            {"status": "error", "error": "FIXTURE_PRIVATE_CANARY"},
            "not-json-FIXTURE_PRIVATE_CANARY", [],
            RuntimeError("FIXTURE_PRIVATE_CANARY"), KeyboardInterrupt(), SystemExit(17)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                terminal = unittest.mock.Mock(return_value={"session_id": "proc_owned", "exit_code": 0})
                process = unittest.mock.Mock(side_effect=[failure, {"status": "killed", "output": "stopped fixture"}])
                bridge = c.NativeBridge(terminal_tool=terminal, process_handler=process, deadline=time.time()+1000)
                expected = type(failure) if isinstance(failure, (KeyboardInterrupt, SystemExit)) else c.CoordinatorError
                with self.assertRaises(expected) as caught:
                    bridge("examples", runId="fixture")
                if expected is c.CoordinatorError:
                    self.assertEqual(str(caught.exception), "Docker bridge operation failed")
                terminal.assert_called_once()
                self.assertEqual(process.call_count, 2)
                self.assertEqual(process.call_args.args, ({"action": "kill", "session_id": "proc_owned"},))
                self.assertTrue(all(call.kwargs == {"task_id": bridge.task_id} for call in process.call_args_list))

    def test_expiry_during_wait_cancels_owned_session(self):
        clock = [1000.0]
        def process(args, **kwargs):
            if args["action"] == "wait":
                clock[0] = 1002.0
                return {"status": "timeout", "process_running": True, "output": "partial"}
            return {"status": "killed"}
        handler = unittest.mock.Mock(side_effect=process)
        terminal = unittest.mock.Mock(return_value={"session_id": "proc_owned", "exit_code": 0})
        bridge = c.NativeBridge(terminal_tool=terminal, process_handler=handler, deadline=1001.0)
        active = c.deadline_error()
        with unittest.mock.patch.object(c.time, "time", side_effect=lambda: clock[0]), \
             unittest.mock.patch.object(c, 'deadline_error', return_value=active) as issued:
            with self.assertRaises(c.CoordinatorError) as caught:
                bridge("examples")
        self.assertIs(caught.exception, active)
        self.assertTrue(c.is_deadline_error(caught.exception))
        issued.assert_called_once_with()
        self.assertEqual(handler.call_args.args[0], {"action": "kill", "session_id": "proc_owned"})
        self.assertLessEqual(handler.call_args_list[0].args[0]["timeout"], 1)
        terminal.assert_called_once()

    def test_long_stages_explicitly_disable_notifications_without_shortening_budget(self):
        for op in ("examples", "validate", "verify"):
            with self.subTest(op=op):
                terminal = unittest.mock.Mock(return_value={"session_id": "proc_owned", "exit_code": 0})
                process = unittest.mock.Mock(return_value={"status": "exited", "exit_code": 0,
                    "output": c.encode_message({"ok": True, "result": {"ok": True}})})
                bridge = c.NativeBridge(terminal_tool=terminal, process_handler=process, deadline=time.time()+3400)
                self.assertEqual(bridge(op), {"ok": True})
                terminal.assert_called_once()
                self.assertIs(terminal.call_args.kwargs.get("background"), True)
                self.assertIs(terminal.call_args.kwargs.get("notify_on_complete"), False)
                self.assertGreater(terminal.call_args.kwargs["timeout"], 600)
                self.assertEqual(process.call_args.kwargs["task_id"], terminal.call_args.kwargs["task_id"])

    def test_short_operations_stay_foreground_within_native_cap(self):
        for op in ("runtime_attestation", "begin", "status", "plan"):
            with self.subTest(op=op):
                terminal = unittest.mock.Mock(return_value={"exit_code": 0,
                    "output": c.encode_message({"ok": True, "result": {"ok": True}})})
                process = unittest.mock.Mock()
                bridge = c.NativeBridge(terminal_tool=terminal, process_handler=process, deadline=time.time()+3400)
                self.assertEqual(bridge(op), {"ok": True})
                self.assertLessEqual(terminal.call_args.kwargs["timeout"], 600)
                self.assertIs(terminal.call_args.kwargs.get("background"), False)
                process.assert_not_called()

    def test_contradictory_completion_metadata_fails_closed_and_cleans_owned_sid(self):
        encoded = c.encode_message({"ok": True, "result": {"ok": True}})
        for extra in ({"process_running": True}, {"session_id": "proc_unrelated"},
                      {"error": "PRIVATE"}, {"status": "interrupted"}, {"status": "not_found"}):
            with self.subTest(extra=extra):
                final = dict(status="exited", exit_code=0, output=encoded, **{})
                final.update(extra)
                process = unittest.mock.Mock(side_effect=[final, {"status": "killed"}])
                bridge = c.NativeBridge(terminal_tool=lambda **kw: {"session_id": "proc_owned"}, process_handler=process)
                with self.assertRaisesRegex(c.CoordinatorError, "^Docker bridge operation failed$"):
                    bridge("examples")
                self.assertEqual(process.call_args.args[0], {"action": "kill", "session_id": "proc_owned"})

    def test_foreground_partial_or_error_is_not_completion(self):
        encoded = c.encode_message({"ok": True, "result": {"ok": True}})
        for extra in ({"status": "running"}, {"status": "timeout"}, {"error": "PRIVATE"}, {"process_running": True}):
            with self.subTest(extra=extra):
                outer = dict(exit_code=0, output=encoded, **extra)
                bridge = c.NativeBridge(terminal_tool=lambda **kw: outer)
                with self.assertRaisesRegex(c.CoordinatorError, "^Docker bridge operation failed$"):
                    bridge("status")

    def test_native_defaults_load_existing_process_handler_and_actual_cap(self):
        import sys, types
        terminal = types.ModuleType("tools.terminal_tool")
        processes = types.ModuleType("tools.process_registry")
        terminal.FOREGROUND_MAX_TIMEOUT = 73
        terminal.terminal_tool = unittest.mock.Mock(return_value={"session_id": "proc_owned"})
        processes._handle_process = unittest.mock.Mock(return_value={"status": "exited", "exit_code": 0,
            "output": c.encode_message({"ok": True, "result": {"ok": True}})})
        with unittest.mock.patch.dict(sys.modules, {"tools.terminal_tool": terminal, "tools.process_registry": processes}):
            self.assertEqual(c.NativeBridge()("status"), {"ok": True})
        self.assertLessEqual(terminal.terminal_tool.call_args.kwargs["timeout"], 73)
        processes._handle_process.assert_called_once()
        self.assertEqual(processes._handle_process.call_args.kwargs["task_id"], terminal.terminal_tool.call_args.kwargs["task_id"])
        self.assertTrue(all("process_manage" not in names for names in c.ROLE_TOOLS.values()))

    def test_cleanup_failure_is_not_reported_as_stopped(self):
        process = unittest.mock.Mock(side_effect=[RuntimeError("PRIVATE"), {"status": "error", "error": "PRIVATE"}])
        bridge = c.NativeBridge(terminal_tool=lambda **kw: {"session_id": "proc_owned"}, process_handler=process)
        with self.assertRaisesRegex(c.CoordinatorError, "^owned Docker bridge cleanup failed$"):
            bridge("examples")
        self.assertEqual(process.call_args.args[0]["action"], "kill")


FIXTURE_DEFAULT_RESULT = object()


class RuntimeTests(unittest.TestCase):
    def native_fixture(self, before=(), after=(), probe=True, role="author", review_status="pass", bridge_override=None,
                       close_error=False, guard_error=False, protocol=False, interrupt=None, close_hook=None,
                       policy_bridge_override=None, review_bridge_override=None, model_submit=True,
                       assignment_seconds=60, model_actions=None, assignment_run_id='run-123',
                       model_result=FIXTURE_DEFAULT_RESULT, protocol_stream=None):
        import types, sys
        state = {"built": 0, "conversations": 0, "closed": False}
        policies = FixturePublicReview(role) if role == 'reviewer' else FixturePublicPolicies(role)
        state['policyBridge'] = policies
        handlers = {}
        original_dispatch = lambda *a, **k: None
        class FixtureRegistry(types.SimpleNamespace):
            def __setattr__(self, name, value):
                if name == 'dispatch' and value is original_dispatch and 'dispatch' in self.__dict__:
                    state['restoration_attempted'] = True
                    if guard_error:
                        raise RuntimeError('FIXTURE_PRIVATE_GUARD_RESTORE')
                super().__setattr__(name, value)
        registry = FixtureRegistry(dispatch=original_dispatch)
        registry.register = lambda **kw: handlers.update({kw["name"]: kw["handler"]})
        class FixtureAgent:
            def __init__(self, **kwargs):
                state['agentKwargs'] = kwargs
                self.run_budget_seconds = kwargs['run_budget_seconds']
                state['cwdWasEmpty'] = not any(Path(kwargs['cwd']).iterdir())
                self.tools = [{"type": "function", "function": {"name": n}} for n in handlers]
                self.valid_tool_names = set(handlers)
            def _build_system_prompt(self):
                state["built"] += 1
                if not hasattr(self, "_plugin_system_prompt_sections_snapshot"):
                    self._plugin_system_prompt_sections_snapshot = before
                return "FIXTURE_PRIVATE_PROMPT" if before else "fixture public prompt"
            def run_conversation(self, *args, **kwargs):
                state["conversations"] += 1
                state['conversationArgs'], state['conversationKwargs'] = args, kwargs
                state['bridgeEventsAtConversation'] = list(policies.events)
                state['budgetAtConversation'] = self.run_budget_seconds
                state['remainingAtConversation'] = assignment['deadline'] - time.time()
                if not model_submit:
                    return {} if model_result is FIXTURE_DEFAULT_RESULT else model_result
                if model_actions:
                    model_actions(args[0], handlers)
                    return {} if model_result is FIXTURE_DEFAULT_RESULT else model_result
                if role == "author":
                    for version in c.VERSIONS:
                        handlers["paykit_draft_open"]({"version": version})
                        handlers["paykit_draft_finalize"]({"version": version, "expectedRevision": "d"*64})
                elif role == 'reviewer':
                    handlers["paykit_review_inputs"]({})
                    handlers["paykit_submit_review"]({"status": review_status, "findings": [],
                        "versions": list(c.VERSIONS), "notes": "fixture"})
                else:
                    handlers['paykit_rendered_guide']({'offset': 0, 'limit': 6000})
                    handlers['paykit_run_literal_plan']({'commands': ['tar -xzf paykit-wallet-released.tar.gz',
                        'cd paykit-wallet-demo', 'cargo run --locked']})
                    handlers['paykit_submit_walkthrough']({})
                self._plugin_system_prompt_sections_snapshot = after
                if interrupt:
                    raise interrupt
                return {} if model_result is FIXTURE_DEFAULT_RESULT else model_result
            def close(self):
                state["closed"] = True
                if close_hook:
                    close_hook()
                if close_error:
                    raise RuntimeError('FIXTURE_PRIVATE_CLOSE')
        modules = {name: types.ModuleType(name) for name in
                   ("tools.registry", "run_agent", "toolsets", "hermes_cli.runtime_provider")}
        modules["tools.registry"].registry = registry
        modules["run_agent"].AIAgent = FixtureAgent
        modules["toolsets"].get_toolset_names = lambda: []
        modules["toolsets"].resolve_toolset = lambda group: list(handlers)
        provider = modules["hermes_cli.runtime_provider"]
        provider.load_config = lambda: {"model": "fixture-model", "approvals": {"cron_mode": "deny"}}
        provider.resolve_runtime_with_fallback = lambda *a, **k: ({}, None)
        assignment = dict(runId=assignment_run_id, role=role, deadline=time.time()+assignment_seconds,
                          workerId="fixture-worker", artifactHash="a"*64 if role != "author" else None,
                          version='released' if role == 'walker' else None, audience='wallet' if role == 'walker' else None)
        def bridge(op, **kwargs):
            if op in ('get_review_inputs', 'read_blueprint') and review_bridge_override:
                return review_bridge_override(op, **kwargs)
            if op in ('bootstrap', 'read_inputs') and policy_bridge_override:
                return policy_bridge_override(op, **kwargs)
            policy = policies(op, **kwargs)
            if policy is not None: return policy
            if bridge_override: return bridge_override(op, **kwargs)
            if op == "get_review_inputs": return {"artifactHash": "a"*64}
            if op == 'walkthrough_prepare': return {'artifactHash': 'a'*64, 'guide': {'text': 'Public fixture guide'}}
            if op == 'draft_finalize': return {'version': kwargs['version'], 'revision': kwargs['expectedRevision'],
                'saved': True, 'sha256': 'a'*64, 'unchanged': True}
            return {"saved": True} if op in ("submit_review", "submit_blueprint", "draft_finalize") else {"ok": True}
        diagnostic_instances = []
        original_init = c.WorkerDiagnostics.__init__
        def capture_init(diagnostic, *args, **kwargs):
            original_init(diagnostic, *args, **kwargs)
            diagnostic_instances.append(diagnostic)
        with unittest.mock.patch.dict(sys.modules, modules), \
             unittest.mock.patch.object(c.WorkerDiagnostics, '__init__', capture_init):
            try:
                if protocol:
                    import contextlib, io
                    bootstrap = types.ModuleType('hermes_bootstrap')
                    loader = types.ModuleType('hermes_cli.env_loader')
                    loader.load_hermes_dotenv = lambda **kwargs: None
                    output = protocol_stream if protocol_stream is not None else io.StringIO()
                    with unittest.mock.patch.dict(sys.modules, {'hermes_bootstrap': bootstrap, 'hermes_cli.env_loader': loader}), \
                         unittest.mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(assignment))), \
                         unittest.mock.patch.object(c, 'NativeBridge', return_value=bridge), \
                         contextlib.redirect_stdout(output):
                        state['exit'] = c.main(['--worker', '--probe'] if probe else ['--worker'])
                    state['output'] = output.getvalue()
                    state['result'] = json.loads(state['output'])
                    state['result'].pop('diagnostic', None)  # Existing injected API stays core-only.
                else:
                    state["result"] = c.native_worker(assignment, bridge=bridge, probe=probe)
            except BaseException as error:
                state["error"] = str(error)
                state["errorType"] = type(error)
        state['diagnostics'] = ([] if probe else [json.loads(d.latest) for d in diagnostic_instances if d.latest is not None])
        state['diagnosticObjects'] = diagnostic_instances
        if isinstance(policies, FixturePublicReview):
            policies.temp.cleanup()
        return state

    def test_current_issued_rejection_reaches_first_author_model_once_with_empty_delta(self):
        """OfflineSyntheticPublic: current bridge-issued public fields, not a live verdict."""
        feedback = {'status': 'fail', 'findings': ['PUBLIC_REPAIR_FEEDBACK_SENTINEL é🦀'],
                    'versions': list(c.VERSIONS), 'notes': 'OfflineSyntheticPublic rejection notes'}
        fixture = FixturePublicPolicies()
        def bridge(op, **payload):
            result = fixture(op, **payload)
            if op == 'bootstrap':
                result['repairFeedback'] = feedback
                self.assertEqual(result['deltaPlan'], {})
            return result
        state = self.native_fixture(probe=False, policy_bridge_override=bridge)
        self.assertTrue(state['result']['ok'])
        prompt = state['conversationArgs'][0]
        serialized = c.json_value(feedback)
        self.assertEqual(prompt.count(serialized), 1)
        self.assertIn('PUBLIC ISSUED REJECTION FEEDBACK', prompt)
        self.assertIn('runId=run-123', prompt)
        self.assertEqual([op for op, _ in fixture.events].count('bootstrap'), 1)
        self.assertFalse(any(op in ('read_blueprint', 'get_review_inputs') for op, _ in fixture.events))
        self.assertNotIn('reviewPassed', state['result'])

    def test_repair_feedback_schema_and_private_fields_fail_before_model(self):
        """OfflineSyntheticPublic malformed frames are not public evidence."""
        good = {'status': 'fail', 'findings': [{'evidenceId': 'fixture-eid', 'message': 'Public fixture'}],
                'versions': list(c.VERSIONS), 'notes': 'OfflineSyntheticPublic'}
        invalid = [False, [], 'FIXTURE_PRIVATE', {},
            *[dict(good, **patch) for patch in (
                {'status': 'approved'}, {'status': True}, {'versions': ['coming-soon', 'released']},
                {'versions': ['released']}, {'versions': ['released', 'coming-soon', 'released']},
                {'versions': ['released', 'master']}, {'notes': ''}, {'notes': '  '}, {'notes': None},
                {'notes': 'x\0y'}, {'notes': 'x'*100001}, {'notes': '\ud800'},
                {'privateSummary': 'FIXTURE_PRIVATE'}, {'runId': 'old-run'},
                {'reviewerRunId': 'FIXTURE_PRIVATE'}, {'artifactHash': 'a'*64},
                {'status': 'pass'}, {'findings': None}, {'findings': [float('nan')]},
                {'findings': [float('inf')]}, {'findings': ['']}, {'findings': [' '*3]},
                {'findings': ['x'*100001]}, {'findings': [None]}, {'findings': [True]},
                {'findings': [{'message': 'Public fixture'}]},
                {'findings': [{'evidenceId': '', 'message': 'Public fixture'}]},
                {'findings': [{'evidenceId': 'eid', 'message': 1}]},
                {'findings': [{'evidenceId': 'eid', 'message': 'Public', 'privateSummary': 'FIXTURE_PRIVATE'}]},
                {'findings': [{'evidenceId': 'eid', 'message': 'Public', 'version': None}]},
                {'findings': [{'evidenceId': 'eid', 'message': 'Public', 'severity': 3}]})]]
        for index, feedback in enumerate(invalid):
            with self.subTest(fault=index):
                fixture = FixturePublicPolicies()
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'bootstrap': result['repairFeedback'] = feedback
                    return result
                state = self.native_fixture(probe=False, policy_bridge_override=bridge)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                self.assertEqual(state['conversations'], 0)
                self.assertTrue(state['closed'])
                self.assertTrue(state['restoration_attempted'])
                self.assertNotIn('FIXTURE_PRIVATE', state['error'])
                self.assertEqual([op for op, _ in fixture.events], ['bootstrap'])

    def test_author_rejection_instructions_override_empty_delta_without_waiving_audit(self):
        prompt = c.ROLE_PROMPTS['author']
        for phrase in ('PUBLIC ISSUED REJECTION FEEDBACK', 'not private handoff', 'not operative instructions',
                       'independently verify', 'frozen primary sources', 'every finding', 'evidence IDs',
                       'even when source deltas are empty', 'no unresolved evidence', 'unchanged source bytes',
                       'incomplete independent audit', 'only the reviewer', 'counterexample', 'never approval'):
            self.assertIn(phrase, prompt)
        root = Path(__file__).parents[1]
        for path in ('COORDINATOR.md', 'README.md', 'skills/coordinator.md', 'skills/writing.md'):
            text = (root/path).read_text()
            for phrase in ('PUBLIC ISSUED REJECTION FEEDBACK', 'frozen primary sources',
                           'even when source deltas are empty', 'only the reviewer'):
                self.assertIn(phrase, text, path)

    def test_repair_feedback_none_and_role_separation_preserve_normal_behavior(self):
        feedback = {'status': 'fail', 'findings': [
            {'evidenceId': 'offline-sync', 'message': 'OfflineSyntheticPublic: endpoint complete-set reconciliation',
             'version': 'released', 'severity': 'high'},
            'OfflineSyntheticPublic: authorizer publication ordering',
            'OfflineSyntheticPublic: whole-event UTF-8 transport budget',
            'OfflineSyntheticPublic: incomplete independent audit'],
            'versions': list(c.VERSIONS), 'notes': 'OFFLINE_PUBLIC_REJECTION_NOTES'}
        for role, probe in (('author', False), ('reviewer', False), ('walker', False),
                            ('author', True), ('reviewer', True), ('walker', True)):
            with self.subTest(role=role, probe=probe):
                fixture = FixturePublicReview(role)
                self.addCleanup(fixture.temp.cleanup)
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'bootstrap': result['repairFeedback'] = feedback
                    return result
                state = self.native_fixture(role=role, probe=probe, policy_bridge_override=bridge)
                self.assertTrue(state['result']['ok'])
                prompt = state.get('conversationArgs', ('',))[0]
                self.assertEqual(prompt.count(c.json_value(feedback)), int(role == 'author' and not probe))
                if role != 'author' or probe: self.assertNotIn(feedback['notes'], prompt)
                if role == 'walker' and not probe: self.assertEqual(prompt, c.ROLE_PROMPTS['walker'])
                if probe: self.assertEqual(fixture.events, [])
        state = self.native_fixture(probe=False)
        self.assertTrue(state['result']['ok'])
        # The role instructions name feedback, but absent reports add no frame/data.
        self.assertNotIn('--- PUBLIC ISSUED', state['conversationArgs'][0])
        self.assertNotIn('"findings":', state['conversationArgs'][0])
        for status in ('pass', 'fail'):
            fixture = FixturePublicPolicies()
            report = dict(feedback, status=status, findings=[])
            def bridge(op, **payload):
                result = fixture(op, **payload)
                if op == 'bootstrap': result['repairFeedback'] = report
                return result
            state = self.native_fixture(probe=False, policy_bridge_override=bridge)
            self.assertTrue(state['result']['ok'])
            self.assertEqual(state['conversationArgs'][0].count(c.json_value(report)), 1)
            self.assertNotIn('reviewPassed', state['result'])

    def test_repair_feedback_utf8_aggregate_cap_includes_policy_and_all_headers(self):
        fixture = FixturePublicPolicies()
        feedback = {'status': 'fail', 'findings': ['OfflineSyntheticPublic é🦀'],
                    'versions': list(c.VERSIONS), 'notes': 'OfflineSyntheticPublic'}
        assignment = dict(runId='run-123', role='author', workerId='fixture-worker',
            deadline=time.time()+60, artifactHash=None, version=None, audience=None)
        def bridge(op, **payload):
            result = fixture(op, **payload)
            if op == 'bootstrap': result['repairFeedback'] = feedback
            return result
        def load(): return c.preload_public_policies(c.WorkerTools(assignment, bridge))
        context = load()
        self.assertIn(c.json_value(feedback), context)
        fixture.texts[FIXTURE_POLICY_PATHS[-1]] += 'x'*(1_048_576-len(context.encode('utf-8')))
        self.assertEqual(len(load().encode('utf-8')), 1_048_576)
        feedback['notes'] += 'é'
        state = self.native_fixture(probe=False, policy_bridge_override=bridge)
        self.assertIs(state.get('errorType'), c.CoordinatorError)
        self.assertEqual(state['conversations'], 0)
        feedback['findings'] = ['é'*90000]*6  # Each text valid; JSON alone exceeds 1 MiB.
        fixture.events.clear()
        state = self.native_fixture(probe=False, policy_bridge_override=bridge)
        self.assertIs(state.get('errorType'), c.CoordinatorError)
        self.assertEqual(state['conversations'], 0)
        self.assertEqual([op for op, _ in fixture.events], ['bootstrap'])

    def test_feedback_bootstrap_binding_faults_and_preparation_deadline_stop_model(self):
        fixture = FixturePublicPolicies()
        feedback = {'status': 'fail', 'findings': ['OfflineSyntheticPublic'],
                    'versions': list(c.VERSIONS), 'notes': 'OfflineSyntheticPublic'}
        for change in ({'runId': 'old-run'}, {'role': 'reviewer'}, {'role': None},
                       {'role': ['author']}, {'privateSummary': 'FIXTURE_PRIVATE'},
                       {'reviewerRunId': 'extra-identity'}):
            with self.subTest(change=change):
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'bootstrap': result.update(repairFeedback=feedback, **change)
                    return result
                state = self.native_fixture(probe=False, policy_bridge_override=bridge)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                self.assertEqual(state['conversations'], 0)
                self.assertNotIn('FIXTURE_PRIVATE', state['error'])
        for elapsed in (20, 61):
            clock = [1000.0]
            def bridge(op, **payload):
                result = fixture(op, **payload)
                if op == 'bootstrap':
                    result['repairFeedback'] = feedback
                    clock[0] += elapsed
                return result
            with unittest.mock.patch.object(c.time, 'time', side_effect=lambda: clock[0]):
                state = self.native_fixture(probe=False, policy_bridge_override=bridge)
            self.assertEqual(state['conversations'], int(elapsed < 60))
            if elapsed < 60:
                self.assertEqual(state['budgetAtConversation'], 40)
                self.assertEqual(state['agentKwargs']['max_iterations'], 30)
            else:
                self.assertIs(state['errorType'], c.CoordinatorError)

    def test_issued_rejection_can_drive_temporary_draft_repair_with_unchanged_source(self):
        """OfflineSyntheticPublic SDK stand-in; real bridge/drafts/source, never live acceptance."""
        from automation.tests.test_draft_bridge import DraftTests
        DraftTests.setUpClass()
        fixture = DraftTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bp, released_raw = fixture.seed()
        b, p = fixture.bridge, fixture.bridge.p
        for path in c.REQUIRED_POLICY_PATHS:
            target = fixture.root/path
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('OfflineSyntheticPublic policy '+path)
        owner = p.read_json(p.CACHE/'run.lock/owner.json')
        owner['acceptanceHash'] = p.acceptance_digest()
        p.save_json(p.CACHE/'run.lock/owner.json', owner)
        for version, delta in p.plan_changes(fixture.lock).items():
            self.assertEqual(delta['changedSourceFiles'], [])
            self.assertEqual(delta['affectedPages'], [])
        report = {'status': 'fail', 'artifactHash': p.artifact_digest(),
            'findings': [{'evidenceId': 'released-fixture', 'message': 'OFFLINE_ISSUED_REPAIR: inspect README contract',
                          'version': 'coming-soon', 'severity': 'high'},
                         'OfflineSyntheticPublic: incomplete independent audit'],
            'versions': list(c.VERSIONS), 'notes': 'OfflineSyntheticPublic, no approval'}
        fixture.call('submit_review', {'report': report}, role='reviewer', session='offline-reviewer')
        before_blob = fixture.git(fixture.source, 'rev-parse', fixture.commit+':README.md')
        receipts = []
        def transport(op, **payload):
            return b.dispatch(payload['runId'], payload['role'], payload['workerId'],
                {'op': op, 'args': {key: value for key, value in payload.items()
                    if key not in ('runId', 'role', 'workerId', 'deadline', 'artifactHash')}})
        def offline_model(prompt, handlers):
            self.assertIn('OFFLINE_ISSUED_REPAIR', prompt)
            self.assertIn('even when source deltas are empty', prompt)
            for version in c.VERSIONS:
                opened = json.loads(handlers['paykit_draft_open']({'version': version}))
                self.assertEqual(opened['summary']['unresolvedEvidenceIds'], [])
                self.assertEqual(opened['summary']['staleReferences'], [])
                revision = opened['revision']
                if version == 'coming-soon':
                    source = json.loads(handlers['paykit_source_read'](
                        {'version': version, 'path': 'README.md', 'offset': 0, 'limit': 6000}))
                    self.assertIn('PUBLIC FIXTURE', source['text'])
                    selected = json.loads(handlers['paykit_draft_read']({'version': version,
                        'revision': revision, 'selector': {'kind': 'section', 'pageSlug': 'overview', 'sectionId': 'fixture'},
                        'offset': 0, 'limit': 6000}))
                    section = json.loads(selected['text'])
                    section['paragraphs'] = ['OfflineSyntheticPublic: public fixture contract verified.']
                    changed = json.loads(handlers['paykit_draft_edit']({'version': version,
                        'expectedRevision': revision, 'edits': [{'op': 'replaceSection', 'pageSlug': 'overview',
                        'sectionId': 'fixture', 'section': section}]}))
                    revision = changed['revision']
                receipts.append(json.loads(handlers['paykit_draft_finalize'](
                    {'version': version, 'expectedRevision': revision})))
        state = self.native_fixture(probe=False, assignment_run_id=fixture.runid,
            policy_bridge_override=transport, bridge_override=transport, model_actions=offline_model)
        self.assertTrue(state.get('result', {}).get('ok'), state.get('error'))
        self.assertEqual([r['unchanged'] for r in receipts], [True, False])
        self.assertTrue(all(r['saved'] is True for r in receipts))
        self.assertEqual((fixture.root/'automation/blueprints/released.json').read_bytes(), released_raw)
        saved = p.read_json(fixture.root/'automation/blueprints/coming-soon.json')
        self.assertEqual(saved['pages'][0]['sections'][0]['paragraphs'],
                         ['OfflineSyntheticPublic: public fixture contract verified.'])
        self.assertEqual(fixture.git(fixture.source, 'rev-parse', fixture.commit+':README.md'), before_blob)
        self.assertEqual(p.read_json(p.CACHE/'review.json')['status'], 'fail')
        self.assertFalse((fixture.root/'content').exists())
        self.assertFalse((fixture.root/'public').exists())

    def test_feedback_preload_native_cleanup_stays_fatal_through_protocol_and_parent(self):
        """OfflineSyntheticPublic transport faults; no native provider or real child PIDs."""
        import sys
        for phase in ('bootstrap', 'read_inputs'):
            for close_error, guard_error in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(phase=phase, close=close_error, guard=guard_error):
                    public = FixturePublicPolicies()
                    terminal = unittest.mock.Mock(return_value={'session_id': 'proc_feedback_unsafe'})
                    process = unittest.mock.Mock(side_effect=[RuntimeError('FIXTURE_PRIVATE_WAIT'),
                        {'status': 'killed', 'scope_stop_failed': True, 'process_running': True}])
                    native = c.NativeBridge(terminal_tool=terminal, process_handler=process)
                    def bridge(op, **payload):
                        if op == phase: return native(op, **payload)
                        result = public(op, **payload)
                        if op == 'bootstrap': result['repairFeedback'] = {
                            'status': 'fail', 'findings': ['OfflineSyntheticPublic'],
                            'versions': list(c.VERSIONS), 'notes': 'OfflineSyntheticPublic'}
                        return result
                    state = self.native_fixture(probe=False, protocol=True, policy_bridge_override=bridge,
                        close_error=close_error, guard_error=guard_error)
                    self.assertEqual(state.get('result'),
                        {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
                    self.assertEqual(state['conversations'], 0)
                    self.assertTrue(state['closed'])
                    self.assertTrue(state['restoration_attempted'])
                    terminal.assert_called_once()
                    proc = unittest.mock.Mock(pid=123456789, returncode=1)
                    proc.communicate.return_value = (state['output'], None)
                    proc.poll.return_value = 1
                    fixture, roles = FixtureBridge(), []
                    def worker(assignment):
                        roles.append(assignment['role'])
                        return c.fresh_worker(assignment)
                    with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                         unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc) as popen, \
                         unittest.mock.patch.object(c.os, 'killpg'):
                        with self.assertRaises(c.OwnedCleanupError):
                            c.Coordinator(fixture, worker, lambda: None, no_publish=True).run()
                    self.assertEqual(popen.call_count, 1)
                    self.assertEqual(roles, ['author'])
                    self.assertEqual([op for op, _ in fixture.events],
                        ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])

    def test_feedback_preload_fatal_latch_survives_registered_callback_mask(self):
        calls = []
        def bridge(op, **payload):
            calls.append(op)
            raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
        broker = c.WorkerTools(dict(runId='run-123', role='author', workerId='fixture-worker',
            deadline=time.time()+60, artifactHash=None, version=None, audience=None), bridge)
        handlers, registry = {}, unittest.mock.Mock()
        registry.register.side_effect = lambda **kwargs: handlers.update({kwargs['name']: kwargs['handler']})
        c.register_tools(registry, broker, 'OfflineSyntheticPublic')
        with self.assertRaises(c.OwnedCleanupError): c.preload_public_policies(broker)
        response = handlers['paykit_public_bundle']({})
        self.assertEqual(json.loads(response), {'error': 'public operation denied or failed'})
        self.assertEqual(calls, ['bootstrap'])
        with self.assertRaises(c.OwnedCleanupError): broker.require_completion()

    def test_long_reviewer_assignment_preserves_sdk_cap_and_preload_charge(self):
        for preload_seconds in (85, 120):
            with self.subTest(preload_seconds=preload_seconds):
                fixture = FixturePublicReview()
                self.addCleanup(fixture.temp.cleanup)
                clock, bindings = [1000.0], []
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'get_review_inputs':
                        bindings.append(op)
                        if len(bindings) == 1:
                            clock[0] += preload_seconds
                    return result
                with unittest.mock.patch.object(c.time, 'time', side_effect=lambda: clock[0]):
                    state = self.native_fixture(probe=False, role='reviewer',
                        assignment_seconds=600, review_bridge_override=bridge)
                self.assertTrue(state['result']['ok'])
                self.assertEqual(state['agentKwargs']['max_iterations'], 30)
                self.assertEqual(state['agentKwargs']['run_budget_seconds'], 500)
                self.assertEqual(state['remainingAtConversation'], 600-preload_seconds)
                self.assertEqual(state['budgetAtConversation'], min(500, 600-preload_seconds))

    def test_reviewer_receives_entire_lossless_blueprints_before_first_model(self):
        state = self.native_fixture(probe=False, role='reviewer')
        self.assertTrue(state['result']['ok'])
        context = state['conversationArgs'][0]
        events = state['bridgeEventsAtConversation']
        reads = [payload for op, payload in events if op == 'read_blueprint']
        self.assertEqual({p['version'] for p in reads}, set(c.VERSIONS))
        for version, text in state['policyBridge'].blueprint_texts.items():
            self.assertEqual(context.count(text), 1)
            self.assertIn(f'automation/blueprints/{version}.json', context)
            self.assertIn(c.hashlib.sha256(text.encode('utf-8')).hexdigest(), context)
            self.assertGreater(sum(p['version'] == version for p in reads), 1)
        self.assertIn('PUBLIC UNTRUSTED CANDIDATE', context)
        self.assertEqual([op for op, _ in events].count('get_review_inputs'), 2)
        self.assertEqual(events[-1][0], 'get_review_inputs')
        self.assertFalse(any(op in ('read_source', 'source_inventory', 'submit_review') for op, _ in events))
        self.assertEqual([op for op, _ in state['policyBridge'].events].count('read_blueprint'), len(reads))

    def test_reviewer_preload_integrity_faults_stop_before_model_without_submission(self):
        faults = ('privateBinding', 'missingBinding', 'versions', 'duplicateVersions', 'paths', 'missingPath',
            'artifactMismatch', 'bindingDrift', 'pathsDrift', 'privateAfter', 'unknownPage', 'offset',
            'booleanOffset', 'nonprogress', 'earlyEnd', 'shortPage', 'hash', 'changedHash',
            'changedTotal', 'nonfiniteTotal', 'oversize', 'unknownCandidate', 'nonfiniteCandidate',
            'invalidCommit', 'missingCandidate', 'duplicateCandidateKey', 'overflowCandidate')
        for fault in faults:
            with self.subTest(fault=fault):
                fixture = FixturePublicReview()
                self.addCleanup(fixture.temp.cleanup)
                calls = []
                if fault in ('unknownCandidate', 'nonfiniteCandidate', 'invalidCommit', 'missingCandidate', 'duplicateCandidateKey', 'overflowCandidate'):
                    obj = json.loads(fixture.blueprint_texts['released'])
                    if fault == 'unknownCandidate': obj['privateSummary'] = 'FIXTURE_PRIVATE_CANARY'
                    if fault == 'nonfiniteCandidate': obj['pages'] = [float('nan')]
                    if fault == 'invalidCommit': obj['sourceCommit'] = 'master'
                    if fault == 'missingCandidate': del obj['evidence']
                    text = json.dumps(obj, ensure_ascii=False)
                    if fault == 'duplicateCandidateKey': text = text.replace('{', '{"sourceCommit":"c",', 1)
                    if fault == 'overflowCandidate': text = text.replace('"evidence": []', '"evidence": [1e999]')
                    fixture.blueprint_texts['released'] = text
                def bridge(op, **payload):
                    calls.append(op)
                    result = fixture(op, **payload)
                    if op == 'get_review_inputs':
                        n = calls.count(op)
                        if fault == 'privateBinding': result['privateSummary'] = 'FIXTURE_PRIVATE_CANARY'
                        if fault == 'missingBinding': del result['inputPaths']
                        if fault == 'versions': result['versions'] = ['master']
                        if fault == 'duplicateVersions': result['versions'] += ['released']
                        if fault == 'paths': result['inputPaths'] += ['../private']
                        if fault == 'missingPath': result['inputPaths'] = []
                        if fault == 'artifactMismatch' or (fault == 'bindingDrift' and n == 2): result['artifactHash'] = 'c'*64
                        if fault == 'pathsDrift' and n == 2: result['inputPaths'].reverse()
                        if fault == 'privateAfter' and n == 2: result['privateSummary'] = 'FIXTURE_PRIVATE_CANARY'
                    if op == 'read_blueprint':
                        if fault == 'unknownPage': result['privateSummary'] = 'FIXTURE_PRIVATE_CANARY'
                        if fault == 'offset': result['offset'] += 1
                        if fault == 'booleanOffset': result['offset'] = False
                        if fault == 'nonprogress': result['nextOffset'] = payload['offset']
                        if fault == 'earlyEnd': result['nextOffset'] = None
                        if fault == 'shortPage': result['text'] = result['text'][:-1]
                        if fault == 'hash': result['sha256'] = '0'*64
                        if fault == 'changedHash' and payload['offset']: result['sha256'] = '0'*64
                        if fault == 'changedTotal' and payload['offset']: result['totalChars'] += 1
                        if fault == 'nonfiniteTotal': result['totalChars'] = float('nan')
                        if fault == 'oversize': result['totalChars'] = 1_048_577
                    return result
                state = self.native_fixture(probe=False, role='reviewer', review_bridge_override=bridge)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                self.assertEqual(state['conversations'], 0)
                self.assertTrue(state['closed'])
                self.assertTrue(state['restoration_attempted'])
                self.assertNotIn('submit_review', calls)
                self.assertNotIn('FIXTURE_PRIVATE_CANARY', state['error'])
                if fault in faults[:7]: self.assertEqual(calls, ['get_review_inputs'])

    def test_preload_cap_counts_all_utf8_policies_blueprints_and_headers(self):
        for role in ('author', 'reviewer'):
            with self.subTest(role=role):
                fixture = FixturePublicReview(role)
                self.addCleanup(fixture.temp.cleanup)
                assignment = dict(runId='run-123', role=role, workerId='fixture-worker',
                    deadline=time.time()+60, artifactHash='a'*64 if role == 'reviewer' else None,
                    version=None, audience=None)
                def load():
                    broker = c.WorkerTools(assignment, fixture)
                    policies = c.preload_public_policies(broker)
                    return c.preload_reviewer_blueprints(broker, policies) if role == 'reviewer' else policies
                baseline = load()
                # Non-ASCII byte length is not the pagination character count.
                self.assertGreater(len(baseline.encode('utf-8')), len(baseline))
                fixture.texts[FIXTURE_POLICY_PATHS[-1]] += 'x'*(1_048_576-len(baseline.encode('utf-8')))
                exact = load()
                self.assertEqual(len(exact.encode('utf-8')), 1_048_576)
                fixture.texts[FIXTURE_POLICY_PATHS[-1]] += 'x'
                with self.assertRaises(c.CoordinatorError): load()

    def test_mechanical_preload_is_charged_before_first_model_request(self):
        fixture = FixturePublicReview()
        self.addCleanup(fixture.temp.cleanup)
        clock = [time.time()]
        def bridge(op, **payload):
            result = fixture(op, **payload)
            clock[0] += 1
            return result
        with unittest.mock.patch.object(c.time, 'time', side_effect=lambda: clock[0]):
            state = self.native_fixture(probe=False, role='reviewer', review_bridge_override=bridge)
        self.assertTrue(state['result']['ok'])
        self.assertGreater(state['agentKwargs']['run_budget_seconds'], state['budgetAtConversation'])
        self.assertLessEqual(state['budgetAtConversation'], state['remainingAtConversation'])
        self.assertEqual(state['agentKwargs']['max_iterations'], 30)
        self.assertGreater(state['budgetAtConversation'], 0)

    def test_reviewer_preload_deadline_and_noop_never_approve(self):
        for phase in ('before', 'page', 'after'):
            with self.subTest(phase=phase):
                fixture = FixturePublicReview()
                self.addCleanup(fixture.temp.cleanup)
                clock, bindings = [time.time()], []
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'get_review_inputs': bindings.append(op)
                    if ((phase == 'before' and len(bindings) == 1 and op == 'get_review_inputs')
                            or (phase == 'page' and op == 'read_blueprint')
                            or (phase == 'after' and len(bindings) == 2)):
                        clock[0] += 100
                    return result
                with unittest.mock.patch.object(c.time, 'time', side_effect=lambda: clock[0]):
                    state = self.native_fixture(probe=False, role='reviewer', review_bridge_override=bridge)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                self.assertEqual(state['conversations'], 0)
                self.assertTrue(state['closed'])
                self.assertNotIn('submit_review', [op for op, _ in fixture.events])
        state = self.native_fixture(probe=False, role='reviewer', model_submit=False)
        self.assertEqual(state['conversations'], 1)
        self.assertIs(state.get('errorType'), c.CoordinatorError)
        self.assertNotIn('submit_review', [op for op, _ in state['policyBridge'].events])
        fixture = FixturePublicReview()
        self.addCleanup(fixture.temp.cleanup)
        broker = c.WorkerTools(dict(runId='run-123', role='reviewer', workerId='fixture-worker',
            deadline=time.time()+60, artifactHash='a'*64, version=None, audience=None), fixture)
        c.preload_reviewer_blueprints(broker, c.preload_public_policies(broker))
        self.assertIn('paykit_review_inputs', broker.completed)
        self.assertNotIn('paykit_submit_review', broker.completed)
        self.assertFalse(broker.review_passed)
        with self.assertRaises(c.CoordinatorError): broker.require_completion()

    def test_reviewer_preload_fatal_cleanup_protocol_prevents_another_worker_or_repair(self):
        import sys
        for phase in ('get_review_inputs', 'read_blueprint'):
            for close_error, guard_error in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(phase=phase, close=close_error, guard=guard_error):
                    public = FixturePublicReview()
                    self.addCleanup(public.temp.cleanup)
                    def bridge(op, **payload):
                        if op == phase: raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
                        return public(op, **payload)
                    state = self.native_fixture(probe=False, role='reviewer', protocol=True,
                        review_bridge_override=bridge, close_error=close_error, guard_error=guard_error)
                    self.assertEqual(state.get('result'),
                        {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
                    self.assertEqual(state['conversations'], 0)
                    self.assertEqual(state['exit'], 1)
                    self.assertTrue(state['closed'])
                    self.assertTrue(state['restoration_attempted'])
                    proc = unittest.mock.Mock(pid=123456789, returncode=1)
                    proc.communicate.return_value = (state['output'], None)
                    proc.poll.return_value = 1
                    fixture, roles = FixtureBridge(), []
                    def worker(assignment):
                        roles.append(assignment['role'])
                        return c.fresh_worker(assignment)
                    with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                         unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc) as popen, \
                         unittest.mock.patch.object(c.os, 'killpg'):
                        with self.assertRaises(c.OwnedCleanupError):
                            c.Coordinator(fixture, worker, lambda: None, no_publish=True).run()
                    self.assertEqual(popen.call_count, 1)
                    self.assertEqual(roles, ['author'])  # Protocol propagation only, no live reviewer.
                    self.assertEqual([op for op, _ in fixture.events],
                        ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])

    def test_reviewer_preload_fatal_latch_survives_registered_callback_mask(self):
        fixture = FixturePublicReview()
        self.addCleanup(fixture.temp.cleanup)
        def bridge(op, **payload):
            if op == 'read_blueprint': raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
            return fixture(op, **payload)
        broker = c.WorkerTools(dict(runId='run-123', role='reviewer', workerId='fixture-worker',
            deadline=time.time()+60, artifactHash='a'*64, version=None, audience=None), bridge)
        handlers = {}
        registry = unittest.mock.Mock()
        registry.register.side_effect = lambda **kwargs: handlers.update({kwargs['name']: kwargs['handler']})
        c.register_tools(registry, broker, 'offline-public-fixture')
        with self.assertRaises(c.OwnedCleanupError): c.preload_reviewer_blueprints(broker, '')
        count = len(fixture.events)
        response = handlers['paykit_current_blueprint']({'version': 'released', 'offset': 0, 'limit': 6000})
        self.assertEqual(json.loads(response), {'error': 'public operation denied or failed'})
        self.assertEqual(len(fixture.events), count)
        with self.assertRaises(c.OwnedCleanupError): broker.require_completion()

    def test_public_policy_preload_fully_paginates_before_first_model_request(self):
        for role in ('author', 'reviewer'):
            with self.subTest(role=role):
                state = self.native_fixture(probe=False, role=role)
                self.assertTrue(state['result']['ok'])
                events = state['bridgeEventsAtConversation']
                self.assertEqual(events[0][0], 'bootstrap')
                reads = [payload for op, payload in events if op == 'read_inputs']
                self.assertEqual({p['path'] for p in reads}, set(FIXTURE_POLICY_PATHS))
                self.assertTrue(all(sum(p['path'] == path for p in reads) > 1 for path in FIXTURE_POLICY_PATHS))
                context = state['conversationArgs'][0]
                self.assertIn('PUBLIC operative project policy', context)
                for path, text in state['policyBridge'].texts.items():
                    self.assertEqual(context.count(text), 1, path)
                self.assertEqual(state['conversationKwargs']['system_message'], c.SYSTEM_PROMPT)
                self.assertEqual(state['conversationKwargs']['conversation_history'], [])
                self.assertTrue(state['cwdWasEmpty'])
                self.assertEqual(set(state['result']) - {'reviewPassed'}, {'ok'})

    def test_probes_and_walkers_do_not_preload_public_policies(self):
        reviewer = {'paykit_public_bundle', 'paykit_read_inputs', 'paykit_source_inventory',
            'paykit_source_read', 'paykit_current_blueprint', 'paykit_review_inputs', 'paykit_submit_review'}
        walker = {'paykit_rendered_guide', 'paykit_run_literal_plan', 'paykit_submit_walkthrough'}
        self.assertEqual(c.ROLE_TOOLS['reviewer'], reviewer)
        self.assertEqual(c.ROLE_TOOLS['walker'], walker)
        for role in ('author', 'reviewer', 'walker'):
            state = self.native_fixture(role=role, probe=True)
            self.assertEqual(state['policyBridge'].events, [])
            self.assertEqual(set(state['result']['catalogNames']), c.ROLE_TOOLS[role])
        state = self.native_fixture(role='walker', probe=False)
        self.assertTrue(state['result']['ok'])
        self.assertEqual(state['bridgeEventsAtConversation'], [])
        self.assertEqual(state['conversationArgs'][0], c.ROLE_PROMPTS['walker'])
        state = self.native_fixture(role='author', probe=False)
        self.assertTrue(state['result']['ok'])
        self.assertFalse(any(op in ('read_blueprint', 'get_review_inputs')
            for op, _ in state['policyBridge'].events))

    def test_policy_preload_cleanup_failure_retains_fatal_priority(self):
        def fatal(*args, **kwargs):
            raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
        for close_error in (False, True):
            state = self.native_fixture(probe=False, protocol=True, close_error=close_error,
                policy_bridge_override=fatal)
            self.assertEqual(state.get('result'),
                {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
            self.assertEqual(state['conversations'], 0)
            self.assertEqual(state['exit'], 1)
            self.assertTrue(state['closed'])
            self.assertTrue(state['restoration_attempted'])

    def test_policy_bootstrap_requires_protocol_and_frozen_version_identity(self):
        for patch in ({'protocolVersion': 2}, {'protocolVersion': True}, {'versions': ['master']},
                      {'runId': 'other-run'}, {'role': 'reviewer'}, {'privateSummary': 'FIXTURE_PRIVATE'},
                      {'requiredPolicyPaths': list(FIXTURE_POLICY_PATHS) + ['.automation/deployment.json']},
                      {'requiredPolicyPaths': list(FIXTURE_POLICY_PATHS[:-1])}, {'inputPaths': []}):
            with self.subTest(patch=patch):
                fixture = FixturePublicPolicies()
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'bootstrap': result.update(patch)
                    return result
                state = self.native_fixture(probe=False, policy_bridge_override=bridge)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                self.assertEqual(state['conversations'], 0)
                self.assertTrue(state['closed'])
                self.assertNotIn('FIXTURE_PRIVATE', state['error'])
                self.assertEqual([op for op, _ in fixture.events], ['bootstrap'])

    def test_policy_preload_rejects_incomplete_or_inconsistent_public_bytes(self):
        faults = ('missing', 'hash', 'changedHash', 'changedTotal', 'text', 'offset', 'booleanOffset',
                  'nextOffset', 'earlyEnd', 'empty', 'extra', 'oversize')
        for fault in faults:
            with self.subTest(fault=fault):
                fixture = FixturePublicPolicies()
                def bridge(op, **payload):
                    result = fixture(op, **payload)
                    if op == 'read_inputs':
                        if fault == 'missing': raise c.CoordinatorError('public fixture unavailable')
                        if fault == 'hash': result['sha256'] = '0'*64
                        if fault == 'changedHash' and payload['offset']: result['sha256'] = '0'*64
                        if fault == 'changedTotal' and payload['offset']: result['totalChars'] += 1
                        if fault == 'text': result['text'] = result['text'].replace('fixture', 'Fixture')
                        if fault == 'offset': result['offset'] += 1
                        if fault == 'booleanOffset': result['offset'] = False
                        if fault == 'nextOffset': result['nextOffset'] = result['offset']
                        if fault == 'earlyEnd': result['nextOffset'] = None
                        if fault == 'empty': result['text'] = ''
                        if fault == 'extra': result['privateSummary'] = 'FIXTURE_PRIVATE'
                        if fault == 'oversize': result['totalChars'] = 1_048_577
                    return result
                state = self.native_fixture(probe=False, policy_bridge_override=bridge)
                self.assertIs(state.get('errorType'), c.CoordinatorError)
                self.assertEqual(state['conversations'], 0)
                self.assertTrue(state['closed'])
                self.assertNotIn('FIXTURE_PRIVATE', state['error'])

    def test_policy_preload_tokens_do_not_complete_author_and_expiry_stops_reads(self):
        assignment = dict(runId='run-123', role='author', workerId='fixture-worker',
            deadline=time.time()+60, artifactHash=None, version=None, audience=None)
        fixture = FixturePublicPolicies()
        broker = c.WorkerTools(assignment, fixture)
        c.preload_public_policies(broker)
        with self.assertRaises(c.CoordinatorError): broker.require_completion()
        count = len(fixture.events)
        broker.assignment['deadline'] = time.time()-1
        with self.assertRaises(c.CoordinatorError): c.preload_public_policies(broker)
        self.assertEqual(len(fixture.events), count)
        fixture = FixturePublicPolicies()
        clock = [time.time()]
        def bridge(op, **payload):
            result = fixture(op, **payload)
            if op == 'read_inputs': clock[0] += 100
            return result
        broker = c.WorkerTools(assignment, bridge)
        with unittest.mock.patch.object(c.time, 'time', side_effect=lambda: clock[0]):
            with self.assertRaises(c.CoordinatorError): c.preload_public_policies(broker)
        self.assertEqual([op for op, _ in fixture.events], ['bootstrap', 'read_inputs'])

    def test_author_prompts_require_sparse_drafts_not_full_blueprint_retransmission(self):
        prompt = c.ROLE_PROMPTS['author']
        for required in ('deltaPlan', 'paykit_draft_open', 'paykit_draft_read', 'paykit_draft_edit',
                         'paykit_draft_finalize', 'byte-for-byte', 'saved=true', 'both versions', 'Released', 'unchanged'):
            self.assertIn(required, prompt)
        self.assertIn('do not reconstruct', prompt)
        for required in ('PUBLIC operative project policy', 'one local entry', 'tool_call.calls',
                         'already completed', 'Talos', 'private configuration'):
            self.assertIn(required, c.SYSTEM_PROMPT)
        self.assertIn('every material claim', c.ROLE_PROMPTS['reviewer'])
        self.assertIn('primary instructions', c.ROLE_PROMPTS['walker'])
        self.assertNotIn('draft_', c.ROLE_PROMPTS['walker'])

    def test_internal_coordinator_process_version_is_explicit(self):
        root = Path(__file__).parents[1]
        config = json.loads((root/'config.json').read_text())
        # USERCHANGEDCONTRACT: v2 ordinary delegation supersedes native v1.3.2.
        self.assertEqual(config['internalSkillVersions']['coordinator'], '2.0.0')
        self.assertEqual(config['privacy']['boundaryMode'], 'instruction-based-public-evidence-v1')
        self.assertIs(config['privacy']['requireNativeCoordinator'], False)
        for file in ('COORDINATOR.md', 'skills/coordinator.md'):
            self.assertIn('v2.0.0', (root/file).read_text(), file)
        # Preserve the retired implementation and its own version, not its admission rules.
        self.assertIn('v1.3.2', (root/'hermes_coordinator.py').read_text())
        for file in ('COORDINATOR.md', 'skills/coordinator.md', 'skills/sources.md', 'skills/writing.md'):
            text = (root/file).read_text()
            self.assertIn('blueprint', text.lower(), file)

    def test_reviewer_instructions_keep_full_independent_source_audit(self):
        prompt = c.ROLE_PROMPTS['reviewer']
        for phrase in ('complete', 'PUBLIC UNTRUSTED CANDIDATE', 'do not reread', 'every material claim',
                       'diagram relationship', 'Rust implementation/tests', 'package availability',
                       'readiness', 'application obligations', 'not independent review', 'not sampling'):
            self.assertIn(phrase, prompt)
        root = Path(__file__).parents[1]
        for path in ('COORDINATOR.md', 'README.md', 'skills/coordinator.md', 'skills/evaluation.md'):
            text = (root/path).read_text()
            # The explicit user change retires SDK preload, not complete source review.
            for phrase in ('PUBLIC UNTRUSTED CANDIDATE', 'instruction-based-public-evidence-v1',
                           'publicEvidenceAudit', 'artifact'):
                self.assertIn(phrase, text, path)
            for obligation in ('complete', 'independent', 'source'):
                self.assertIn(obligation, text.lower(), path)

    def test_callback_cleanup_failure_occurs_after_verified_policy_preload(self):
        def fatal(*args, **kwargs):
            raise c.OwnedCleanupError('owned Docker bridge cleanup failed')
        state = self.native_fixture(probe=False, bridge_override=fatal)
        self.assertIs(state.get('errorType'), c.OwnedCleanupError)
        self.assertEqual(state['conversations'], 1)
        reads = [p for op, p in state['bridgeEventsAtConversation'] if op == 'read_inputs']
        self.assertEqual({p['path'] for p in reads}, set(FIXTURE_POLICY_PATHS))
        self.assertTrue(state['closed'])

    def test_native_finalizers_preserve_fatal_protocol_and_stop_parent(self):
        """Actual main/native worker/bridge/coordinator; synthetic SDK/process control."""
        import sys
        for close_error, guard_error in ((True, False), (False, True), (True, True)):
            for prior_fatal in (True, False):
                with self.subTest(close=close_error, guard=guard_error, prior_fatal=prior_fatal):
                    terminal = unittest.mock.Mock(return_value={'session_id': 'proc_unsafe'})
                    process = unittest.mock.Mock(side_effect=[RuntimeError('FIXTURE_PRIVATE_WAIT'),
                        {'status': 'killed', 'scope_stop_failed': True, 'process_running': True}])
                    native = c.NativeBridge(terminal_tool=terminal, process_handler=process)
                    state = self.native_fixture(probe=False, protocol=True,
                        bridge_override=native if prior_fatal else None,
                        close_error=close_error, guard_error=guard_error)
                    self.assertEqual(state.get('result'),
                        {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
                    self.assertEqual(state['exit'], 1)
                    self.assertTrue(state['closed'])
                    self.assertTrue(state['restoration_attempted'])
                    if prior_fatal:
                        terminal.assert_called_once()
                    fixture, roles = FixtureBridge(), []
                    proc = unittest.mock.Mock(pid=123456789, returncode=1)
                    proc.communicate.return_value = (state['output'], None)
                    proc.poll.return_value = 1
                    def worker(assignment):
                        roles.append(assignment['role'])
                        return c.fresh_worker(assignment)
                    with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), \
                         unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc) as popen, \
                         unittest.mock.patch.object(c.os, 'killpg'):
                        with self.assertRaises(c.OwnedCleanupError):
                            c.Coordinator(fixture, worker, lambda: None, no_publish=True).run()
                    self.assertEqual(popen.call_count, 1)
                    self.assertEqual(roles, ['author'])
                    self.assertEqual([op for op, _ in fixture.events],
                                     ['runtime_attestation', 'begin', 'plan', 'status', 'fail'])

    def test_native_worker_latches_cleanup_failure_across_swallowed_callbacks(self):
        terminal = unittest.mock.Mock(return_value={'session_id': 'proc_unsafe'})
        def process(args, **kwargs):
            if args['action'] == 'wait':
                raise RuntimeError('FIXTURE_PRIVATE')
            return {'status': 'killed', 'scope_stop_failed': True, 'process_running': True}
        native = c.NativeBridge(terminal_tool=terminal, process_handler=process)
        state = self.native_fixture(probe=False, bridge_override=native)
        self.assertIs(state.get('errorType'), c.OwnedCleanupError)
        self.assertTrue(state['closed'])
        terminal.assert_called_once()
        with self.assertRaises(c.OwnedCleanupError):
            native('status')
        terminal.assert_called_once()

    def test_broker_fatal_survives_bridge_flag_reset_and_interrupt(self):
        for error_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(error=error_type.__name__):
                terminal = unittest.mock.Mock(return_value={'session_id': 'proc_unsafe'})
                process = unittest.mock.Mock(side_effect=[RuntimeError('FIXTURE_PRIVATE_WAIT'),
                    {'status': 'killed', 'scope_stop_failed': True, 'process_running': True}])
                native = c.NativeBridge(terminal_tool=terminal, process_handler=process)
                state = self.native_fixture(probe=False, bridge_override=native,
                    interrupt=error_type(), close_hook=lambda: setattr(native, 'cleanup_failed', False))
                self.assertIs(state['errorType'], c.OwnedCleanupError)
                self.assertFalse(native.cleanup_failed)
                self.assertTrue(state['closed'])
                terminal.assert_called_once()

    def test_computed_worker_fatal_protocol_reaches_parent_without_repairs(self):
        import contextlib, io, sys, types
        modules = {name: types.ModuleType(name) for name in ('hermes_bootstrap', 'hermes_cli.env_loader')}
        modules['hermes_cli.env_loader'].load_hermes_dotenv = lambda **kwargs: None
        assignment = dict(runId='run-123', role='author', deadline=time.time()+60,
                          workerId='fixture-worker', artifactHash=None, version=None, audience=None)
        output = io.StringIO()
        with unittest.mock.patch.dict(sys.modules, modules), unittest.mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(assignment))), unittest.mock.patch.object(c, 'native_worker', side_effect=c.OwnedCleanupError('owned Docker bridge cleanup failed')), contextlib.redirect_stdout(output):
            self.assertEqual(c.main(['--worker']), 1)
        envelope = json.loads(output.getvalue())
        self.assertTrue(envelope.pop('diagnostic')['fatalCleanup'])
        self.assertEqual(envelope, {'ok': False, 'error': 'public worker failed', 'fatalOwnedCleanup': True})
        proc = unittest.mock.Mock()
        proc.communicate.return_value = (output.getvalue(), None)
        proc.returncode = 1
        proc.poll.return_value = 1
        fixture = FixtureBridge()
        with unittest.mock.patch.object(c, 'project_interpreter', return_value=sys.executable), unittest.mock.patch.object(c.subprocess, 'Popen', return_value=proc), unittest.mock.patch.object(c.os, 'killpg'):
            with self.assertRaises(c.OwnedCleanupError):
                c.Coordinator(fixture, c.fresh_worker, lambda: None, no_publish=True).run()
        events = [op for op, _ in fixture.events]
        self.assertEqual(events[-1], 'fail')
        self.assertNotIn('repair', events)
        self.assertNotIn('examples', events)

    def test_private_plugin_sections_fail_closed_before_probe_or_model(self):
        for probe in (True, False):
            state = self.native_fixture(before=("FIXTURE_PRIVATE_PLUGIN",), probe=probe)
            self.assertEqual(state.get("error"), "public worker plugin prompt sections denied")
            self.assertEqual(state["built"], 1)
            self.assertEqual(state["conversations"], 0)
            self.assertTrue(state["closed"])

    def test_private_plugin_sections_rechecked_after_conversation(self):
        state = self.native_fixture(after=("FIXTURE_PRIVATE_PLUGIN",), probe=False)
        self.assertEqual(state.get("error"), "public worker plugin prompt sections denied")
        self.assertEqual(state["conversations"], 1)
        self.assertTrue(state["closed"])

    def test_reviewer_returns_confirmed_saved_status(self):
        for status, expected in (("pass", True), ("fail", False)):
            state = self.native_fixture(probe=False, role="reviewer", review_status=status)
            self.assertIs(state["result"].get("reviewPassed"), expected)

    def test_empty_plugin_sections_allow_probe(self):
        state = self.native_fixture()
        self.assertTrue(state["result"]["ok"])
        self.assertEqual(state["built"], 1)

    def test_bootstrap_precedes_dependency_imports(self):
        import ast, inspect
        nodes = list(ast.walk(ast.parse(inspect.getsource(c.main))))
        bootstrap = [n.lineno for n in nodes if isinstance(n, ast.Import)
                     and any(alias.name == "hermes_bootstrap" for alias in n.names)]
        env_loader = [n.lineno for n in nodes if isinstance(n, ast.ImportFrom)
                      and n.module == "hermes_cli.env_loader"]
        self.assertEqual(len(bootstrap), 1)
        self.assertLess(bootstrap[0], env_loader[0])

    def test_public_transport_binds_role_session_outside_model_request(self):
        import shlex
        calls = []
        def terminal_tool(**kwargs):
            calls.append(kwargs)
            return json.dumps({"exit_code":0,"output":c.encode_message({"ok":True,"result":{"sources":[]}})})
        bridge = c.NativeBridge(terminal_tool=terminal_tool,deadline=time.time()+30)
        bridge("bootstrap",runId="a"*32,role="reviewer",workerId="worker-123",deadline=time.time()+20,artifactHash="b"*64)
        tokens = shlex.split(calls[0]["command"])
        self.assertEqual(tokens[:2],["python","automation/host_bridge.py"])
        self.assertEqual(tokens[tokens.index("--role")+1],"reviewer")
        self.assertEqual(tokens[tokens.index("--session")+1],"worker-123")
        self.assertEqual(c.decode_message(tokens[-1]),{"op":"bootstrap","args":{}})

    def test_nested_deadline_does_not_extend_outer_alarm(self):
        import signal
        with c.bounded_alarm(time.time()+10):
            with c.bounded_alarm(time.time()+5):
                time.sleep(.02)
            self.assertLess(signal.getitimer(signal.ITIMER_REAL)[0],9.99)

    def test_native_preflight_uses_validated_receipt_writer(self):
        import types, io, contextlib, sys, signal
        pipeline = types.ModuleType("automation.pipeline")
        bridge = types.ModuleType("automation.host_bridge")
        bridge.encode_message, bridge.decode_message = c.encode_message, c.decode_message
        pipeline.record_native_preflight = unittest.mock.Mock()
        automation = types.ModuleType("automation")
        automation.pipeline = pipeline
        attestation = {"hermesRevision": c.EXPECTED_REVISION, "coordinatorSha256": "a"*64,
                       "isolationVersion": c.ISOLATION_VERSION}
        request = c.encode_message({"op": "runtime_attestation", "args": {"attestation": attestation}, "deadline": time.time()+30})
        output = io.StringIO()
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        old_handler = signal.getsignal(signal.SIGALRM)
        try:
            with unittest.mock.patch.dict(sys.modules, {"automation": automation, "automation.pipeline": pipeline,
                 "automation.host_bridge": bridge}), unittest.mock.patch.object(sys, "argv", ["fixture", request]), contextlib.redirect_stdout(output):
                exec(compile(c.TRUSTED_PROGRAM, "fixture-trusted-program", "exec"), {})
        finally:
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
            signal.signal(signal.SIGALRM, old_handler)
        self.assertEqual(c.decode_message(output.getvalue()), {"ok": True, "result": {"ok": True}})
        pipeline.record_native_preflight.assert_called_once_with(attestation)

    def test_trusted_status_verifies_bound_native_context_before_worker_admission(self):
        import types, io, contextlib, sys, signal
        pipeline = types.ModuleType('automation.pipeline')
        bridge = types.ModuleType('automation.host_bridge')
        bridge.encode_message, bridge.decode_message = c.encode_message, c.decode_message
        calls = []
        pipeline.require_native_context = lambda run_id: calls.append(('native-context', run_id))
        pipeline.main = lambda: calls.append(('status', None))
        automation = types.ModuleType('automation')
        automation.pipeline = pipeline
        request = c.encode_message({'op': 'status', 'args': {'runId': 'fixture-run'}, 'deadline': time.time()+30})
        output = io.StringIO()
        old_timer, old_handler = signal.getitimer(signal.ITIMER_REAL), signal.getsignal(signal.SIGALRM)
        try:
            with unittest.mock.patch.dict(sys.modules, {'automation': automation, 'automation.pipeline': pipeline,
                 'automation.host_bridge': bridge}), unittest.mock.patch.object(sys, 'argv', ['fixture', request]), contextlib.redirect_stdout(output):
                exec(compile(c.TRUSTED_PROGRAM, 'fixture-trusted-program', 'exec'), {})
        finally:
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
            signal.signal(signal.SIGALRM, old_handler)
        self.assertTrue(c.decode_message(output.getvalue())['ok'])
        self.assertEqual(calls, [('native-context', 'fixture-run'), ('status', None)])

    def test_trusted_pending_verify_never_reads_last_success(self):
        import types, io, contextlib, sys, signal
        pipeline = types.ModuleType("automation.pipeline")
        bridge = types.ModuleType("automation.host_bridge")
        bridge.encode_message, bridge.decode_message = c.encode_message, c.decode_message
        pipeline.CACHE = Path("/fixture-not-existing")
        pipeline.main = lambda: print(json.dumps({"pending": True}))
        pipeline.read_json = unittest.mock.Mock(side_effect=AssertionError("must not read stale success"))
        automation = types.ModuleType("automation")
        automation.pipeline = pipeline
        request = c.encode_message({"op": "verify", "args": {"runId": "fixture-run"}, "deadline": time.time()+30})
        output = io.StringIO()
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        old_handler = signal.getsignal(signal.SIGALRM)
        try:
            with unittest.mock.patch.dict(sys.modules, {"automation": automation, "automation.pipeline": pipeline,
                 "automation.host_bridge": bridge}), unittest.mock.patch.object(sys, "argv", ["fixture", request]), contextlib.redirect_stdout(output):
                exec(compile(c.TRUSTED_PROGRAM, "fixture-trusted-program", "exec"), {})
        finally:
            signal.setitimer(signal.ITIMER_REAL, *old_timer)
            signal.signal(signal.SIGALRM, old_handler)
        self.assertEqual(c.decode_message(output.getvalue()), {"ok": True, "result": {"pending": True}})
        pipeline.read_json.assert_not_called()

    def test_trusted_build_program_has_one_result_and_fixed_argv(self):
        import types, io, contextlib, sys, signal
        pipeline = types.ModuleType("automation.pipeline")
        bridge = types.ModuleType("automation.host_bridge")
        bridge.encode_message, bridge.decode_message = c.encode_message, c.decode_message
        bridge.public_environment = lambda: {"PATH":"fixture"}
        calls = []
        @contextlib.contextmanager
        def run_scope(run_id): yield
        pipeline.run_scope = run_scope
        pipeline.run = lambda argv, **kwargs: calls.append((argv,kwargs))
        automation = types.ModuleType("automation")
        automation.pipeline = pipeline
        request = c.encode_message({"op":"build","args":{"runId":"fixture-run"},"deadline":time.time()+30})
        output = io.StringIO()
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        old_handler = signal.getsignal(signal.SIGALRM)
        try:
            with unittest.mock.patch.dict(sys.modules,{"automation":automation,"automation.pipeline":pipeline,"automation.host_bridge":bridge}), unittest.mock.patch.object(sys,"argv",["fixture",request]), contextlib.redirect_stdout(output):
                try: exec(compile(c.TRUSTED_PROGRAM,"fixture-trusted-program","exec"),{})
                except SystemExit as error: self.assertEqual(error.code,0)
        finally:
            signal.setitimer(signal.ITIMER_REAL,*old_timer)
            signal.signal(signal.SIGALRM,old_handler)
        self.assertEqual(len(output.getvalue().splitlines()),1)
        self.assertTrue(c.decode_message(output.getvalue())["ok"])
        self.assertEqual(calls[0][0],["npm","run","build"])

    def test_transport_is_fixed_configured_backend_not_docker_cli(self):
        calls = []
        def terminal_tool(**kwargs):
            calls.append(kwargs)
            return json.dumps({"exit_code": 0, "output": c.encode_message({"ok": True, "result": {"action": "no-change"}})})
        bridge = c.NativeBridge(terminal_tool=terminal_tool, deadline=time.time()+60)
        self.assertEqual(bridge("begin")["action"], "no-change")
        self.assertEqual(calls[0]["workdir"], "/workspace/paykit-landing-page")
        self.assertNotIn("force", calls[0])
        self.assertNotIn("_host_local", calls[0])
        self.assertNotIn("docker ", calls[0]["command"])
        self.assertIn("automation.host_bridge", calls[0]["command"])

    def test_native_agent_flags_do_not_load_private_context(self):
        kwargs = c.agent_kwargs({"model": {"default": "fixture-model", "provider": "fixture-provider"}},
                                {"api_key": "fixture-secret", "provider": "fixture-provider"},
                                "public-group", ["terminal", "mcp_talos"], time.time()+60)
        self.assertEqual(kwargs["enabled_toolsets"], ["public-group"])
        for flag in ("skip_memory", "skip_context_files", "skip_background_review", "quiet_mode"):
            self.assertIs(kwargs[flag], True)
        self.assertIs(kwargs["load_soul_identity"], False)
        self.assertEqual(kwargs["prefill_messages"], [])
        self.assertLessEqual(kwargs["max_iterations"], 30)
        self.assertEqual(kwargs["model"], "fixture-model")
        self.assertIs(kwargs["checkpoints_enabled"], False)

    def test_deferred_meta_requires_exact_scoped_catalog(self):
        expected = {"paykit_public_bundle"}
        meta = [{"type":"function","function":{"name":name}} for name in c.META_TOOLS]
        with self.assertRaises(c.CoordinatorError):
            c.assert_grant(meta, expected)
        with self.assertRaises(c.CoordinatorError):
            c.assert_grant(meta, expected, expected | {"private_fixture"})
        c.assert_grant(meta, expected, expected)
        direct = [{"type": "function", "function": {"name": name}} for name in expected]
        with self.assertRaises(c.CoordinatorError):
            c.assert_grant(direct, expected, expected | {"private_fixture"})

    def test_dispatch_guard_allows_meta_routes_but_denies_nested_private(self):
        class FixtureRegistry:
            def __init__(self): self.executed = []
            def dispatch(self, name, args, **kwargs):
                if name == "tool_call":
                    return self.dispatch(args["name"], args.get("arguments", {}))
                if name in ("tool_search", "tool_describe"):
                    return "scoped public catalog"
                self.executed.append(name)
                return "public fixture execution"
        registry = FixtureRegistry()
        with c.dispatch_guard(registry, {"paykit_public_bundle"}):
            for name in ("tool_search", "tool_describe"):
                self.assertEqual(registry.dispatch(name, {}), "scoped public catalog")
            self.assertEqual(registry.dispatch("tool_call", {"name": "paykit_public_bundle"}),
                             "public fixture execution")
            self.assertIn("denied", registry.dispatch("tool_call", {"name": "mcp_talos_find"}))
            self.assertIn("denied", registry.dispatch("terminal", {}))
        self.assertEqual(registry.executed, ["paykit_public_bundle"])

    def test_dispatch_guard_denies_private_direct_calls_after_refresh(self):
        class FixtureRegistry:
            def __init__(self): self.calls = []
            def dispatch(self, name, args, **kwargs):
                self.calls.append(name)
                return "fixture execution"
        registry = FixtureRegistry()
        with c.dispatch_guard(registry, {"paykit_public_bundle"}):
            for name in ("terminal", "mcp_talos_find", "fixture_private_after_refresh", "execute_code"):
                result = registry.dispatch(name, {})
                self.assertIn("denied", result)
            self.assertEqual(registry.calls, [])
            registry.dispatch("paykit_public_bundle", {})
        self.assertEqual(registry.calls, ["paykit_public_bundle"])

    def test_codec_rejects_duplicate_fields_and_trailing_payload(self):
        import zlib
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(c.CoordinatorError):
                c.decode_message('pkhex1:' + zlib.compress(raw).hex())
        encoded = 'pkhex1:' + (zlib.compress(b'{}')+b'extra').hex()
        with self.assertRaises(c.CoordinatorError): c.decode_message(encoded)

    def test_lock_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            with c.host_lock(Path(td)/"coordinator.lock"):
                with self.assertRaises(c.CoordinatorError):
                    with c.host_lock(Path(td)/"coordinator.lock"):
                        self.fail("overlap allowed")

    def test_worker_subprocess_does_not_inherit_private_additions(self):
        import os
        with unittest.mock.patch.dict(os.environ, {"PRIVATE_FIXTURE_CANARY": "secret"}):
            env = c.worker_environment()
        self.assertNotIn("PRIVATE_FIXTURE_CANARY", env)
        self.assertNotIn("PYTHONPATH", env)


if __name__ == "__main__":
    unittest.main()
