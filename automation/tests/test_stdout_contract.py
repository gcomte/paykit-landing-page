"""Synthetic protocol labs, not review/run/publication approval.

Compose (do not inherit/import its TestCase) the existing valid audit fixture so
unittest discovery does not duplicate the original module's test IDs.
"""
import ast
import contextlib
import hashlib
import copy
import importlib.util
import io
import json
import subprocess
import sys
from unittest.mock import patch
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "stdout_audit_fixture", Path(__file__).with_name("test_public_review_audit.py"))
audit_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_fixture)


class StdoutContractTests(unittest.TestCase):
    def setUp(self):
        self.lab = audit_fixture.PublicReviewAuditTests(methodName="runTest")
        self.addCleanup(self.lab.doCleanups)
        self.lab.setUp()

    def test_one_terminal_lf_is_accepted_losslessly_for_all_four_reports(self):
        # Valid no-LF control proves RED cannot be an unrelated schema/lease gate.
        self.assertEqual(self.lab.approve()["walkthroughs"], self.lab.walkthroughs)
        for row in self.lab.walkthroughs:
            row["stdout"] += "\n"
        raw = copy.deepcopy(self.lab.walkthroughs)
        self.assertEqual(self.lab.approve()["walkthroughs"], raw)
        self.assertEqual(self.lab.walkthroughs, raw)

    def test_optional_status_must_be_exact_pass_and_legacy_absence_is_supported(self):
        baseline = copy.deepcopy(self.lab.walkthroughs)
        self.assertEqual(self.lab.approve()["walkthroughs"], baseline)
        for index in range(4):
            for status in ("fail", "incomplete", "PASS", " pass", "pass\n", "", None,
                           False, True, 0, 0.0, [], {}, ["pass"]):
                with self.subTest(index=index, status=status):
                    self.lab.walkthroughs = copy.deepcopy(baseline)
                    self.lab.walkthroughs[index]["status"] = status
                    with self.assertRaisesRegex(ValueError, "Fresh walkthrough failed"):
                        self.lab.approve()
        self.lab.walkthroughs = copy.deepcopy(baseline)
        for row in self.lab.walkthroughs:
            row["status"] = "pass"
        self.assertEqual(self.lab.approve()["walkthroughs"], self.lab.walkthroughs)

    def test_invalid_whitespace_content_and_json_types_are_rejected_for_each_report(self):
        baseline = copy.deepcopy(self.lab.walkthroughs)
        for index in range(4):
            expected = self.lab.p.EXPECTED[baseline[index]["audience"]]
            invalid = [" " + expected, "\t" + expected, "\n" + expected,
                       expected + " ", expected + "\t", expected + " \n", expected + "\t\n",
                       expected + "\n\n", expected + "\n\n\n", expected + "\r", expected + "\r\n",
                       expected + "\nextra", expected + "extra\n", expected + "\x00",
                       expected.replace(" ", "  ", 1), expected.replace(" ", "\t", 1),
                       expected.replace(" ", "\n", 1), expected[:-1] + "!",
                       "", None, False, True, 0, 0.0, [], {}, [expected], {"stdout": expected}]
            if "\n" in expected:
                invalid.extend([expected.replace("\n", "\r\n"), expected.replace("\n", "\n\n")])
            for value in invalid:
                with self.subTest(index=index, value=value):
                    self.lab.walkthroughs = copy.deepcopy(baseline)
                    self.lab.walkthroughs[index]["stdout"] = value
                    self.assertFalse(self.lab.p.stdout_matches_expected(value, expected))
                    with self.assertRaisesRegex(ValueError, "Fresh walkthrough failed"):
                        self.lab.approve()
            self.lab.walkthroughs = copy.deepcopy(baseline)
            del self.lab.walkthroughs[index]["stdout"]
            with self.assertRaisesRegex(ValueError, "Fresh walkthrough failed"):
                self.lab.approve()
            self.assertFalse(self.lab.p.stdout_matches_expected(expected.encode(), expected))

    def test_one_lf_does_not_bypass_exit_identity_artifact_guide_archive_or_audit_gates(self):
        for row in self.lab.walkthroughs:
            row["stdout"] += "\n"
        baseline = copy.deepcopy(self.lab.walkthroughs)
        self.lab.approve()
        for index in range(4):
            for field, value, message in (
                    ("exitCode", False, "Fresh walkthrough failed"),
                    ("exitCode", 0.0, "Fresh walkthrough failed"),
                    ("exitCode", 1, "Fresh walkthrough failed"),
                    ("runId", None, "walkthrough run identity"),
                    ("runId", "closed-old-run", "walkthrough run identity"),
                    ("artifactHash", "old-artifact", "Fresh walkthrough failed"),
                    ("reporterRunId", self.lab.review["authorRunId"], "identities"),
                    ("guideUrl", "https://synthetic.invalid/wrong-guide", "guide URL"),
                    ("commands", ["cargo run"], "literal locked cargo run"),
                    ("archiveHash", "old-archive", "archive hash")):
                with self.subTest(index=index, field=field, value=value):
                    self.lab.walkthroughs = copy.deepcopy(baseline)
                    self.lab.walkthroughs[index][field] = value
                    with self.assertRaisesRegex(ValueError, message):
                        self.lab.approve()
        self.lab.walkthroughs = baseline
        self.lab.review["publicEvidenceAudit"]["unsupportedClaims"] = ["synthetic unsupported claim"]
        with self.assertRaisesRegex(ValueError, "Public evidence audit"):
            self.lab.approve()

    def test_consumer_never_rewrites_raw_command_files_or_report_stdout(self):
        original = {}
        for row in self.lab.walkthroughs:
            value = self.lab.p.EXPECTED[row["audience"]] + "\n"
            raw_path = self.lab.root / f"raw-{row['version']}-{row['audience']}.stdout"
            raw_path.write_bytes(value.encode())
            original[raw_path] = raw_path.read_bytes()
            row.update(stdout=value, stdoutPath=str(raw_path))
        approved = self.lab.approve()
        self.assertEqual(approved["walkthroughs"], self.lab.walkthroughs)
        for path, value in original.items():
            self.assertEqual(path.read_bytes(), value)
        self.assertEqual(json.loads((self.lab.cache / "walkthroughs.json").read_text()), self.lab.walkthroughs)

    def example_results(self, stdout_for):
        for row in self.lab.walkthroughs:
            directory = self.lab.root / f"public/docs-examples/{row['version']}/{row['audience']}"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "Cargo.lock").write_text("# Synthetic lock, no Cargo execution.\n")

        def captured(args, **kwargs):
            audience = kwargs["cwd"].name
            return subprocess.CompletedProcess(args, 0, stdout_for(audience), "synthetic stderr\n")

        with patch.object(self.lab.p, "bounded_process", side_effect=captured), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.lab.p.test_examples(self.lab.lock, write_artifacts=False)

    def test_example_producer_retains_raw_stdout_in_returned_and_saved_reports(self):
        for terminator in ("", "\n"):
            with self.subTest(terminator=repr(terminator)):
                rows = self.example_results(lambda audience: self.lab.p.EXPECTED[audience] + terminator)
                self.assertEqual(len(rows), 4)
                self.assertEqual(json.loads((self.lab.cache / "example-results.json").read_text()), rows)
                for row in rows:
                    self.assertEqual(row["stdout"], self.lab.p.EXPECTED[row["audience"]] + terminator)

    def test_real_capture_does_not_turn_crlf_or_cr_into_accepted_lf(self):
        # Real small Python child, not Cargo: expose universal-newline conversion
        # through the actual producer. All work is inside the owned synthetic lab.
        original = self.lab.p.bounded_process
        for row in self.lab.walkthroughs:
            directory = self.lab.root / f"public/docs-examples/{row['version']}/{row['audience']}"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "Cargo.lock").write_text("# Synthetic lock.\n")
        for terminator in ("\r\n", "\r"):
            def captured(args, **kwargs):
                raw = (self.lab.p.EXPECTED[kwargs["cwd"].name] + terminator).encode()
                kwargs["cwd"] = self.lab.root
                kwargs["timeout"] = 5
                kwargs.pop("env", None)
                return original([sys.executable, "-c", "import os; os.write(1, " + repr(raw) + ")"], **kwargs)
            with self.subTest(terminator=repr(terminator)), \
                    patch.object(self.lab.p, "bounded_process", side_effect=captured), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "Example failed"):
                    self.lab.p.test_examples(self.lab.lock, write_artifacts=False)
                saved = json.loads((self.lab.cache / "example-results.json").read_text())[0]
                self.assertEqual(saved["stdout"], self.lab.p.EXPECTED["wallet"] + terminator)

    def test_example_producer_saves_per_command_raw_output_without_rewriting(self):
        rows = self.example_results(lambda audience: self.lab.p.EXPECTED[audience] + "\n")
        for row in rows:
            stem = self.lab.cache / "example-output" / f"{row['version']}-{row['audience']}"
            self.assertEqual(stem.with_suffix(".stdout").read_bytes(), row["stdout"].encode())
            self.assertEqual(stem.with_suffix(".stderr").read_bytes(), b"synthetic stderr\n")

    def test_example_producer_rejects_whitespace_that_strip_used_to_accept(self):
        with self.assertRaisesRegex(ValueError, "Example failed"):
            self.example_results(lambda audience: " " + self.lab.p.EXPECTED[audience] + "\n\n")


class ProspectivePolicyTests(unittest.TestCase):
    def test_stdout_and_deadline_guidance_is_explicit_in_all_three_policies(self):
        for name in ("automation/COORDINATOR.md", "automation/skills/evaluation.md", "automation/README.md"):
            source = (ROOT / name).read_text()
            with self.subTest(policy=name):
                for clause in ("raw stdout including its terminator", "raw-byte equality",
                               "optional single U+000A LF", "All other characters are exact",
                               "old FAIL reports remain FAIL", "new actual run/artifact/role IDs",
                               "measured end-to-end costs", "startup, source reads, final hash checks and report finalization",
                               "shared absolute run deadline", "planning targets only",
                               "120/300-second phase approval gates", "Never extend or reopen",
                               "No borrowing old reviews"):
                    self.assertIn(clause, source)

    def test_correction_register_is_append_only_with_executable_regressions(self):
        register = json.loads((ROOT / "automation/corrections.json").read_text())
        additions = {"lossless-stdout-optional-single-lf", "measured-review-shared-deadline"}
        existing = copy.deepcopy(register)
        # Explicit new user source/default requirements supersede only the old
        # Released-only selection. Normalize that authorized migration to retain
        # the original byte-contract assertion for every unrelated correction.
        selection_additions = {"configurable-documentation-source", "precise-regeneration-triggers", "truthful-readiness-metadata", "development-preview-hosting"}
        # User-approved review/output/budget additions are checked independently;
        # retain the original canonical assertion for every unrelated correction.
        review_budget_additions = {"expanded-independent-review-coverage", "generated-glossary-and-resources",
                                  "every-page-markdown-and-reviewed-llm-exports", "sandboxed-doc-execution-playgrounds",
                                  "six-hour-budgets-and-every-run-accounting"}
        existing["requirements"] = [r for r in existing["requirements"] if r["id"] not in additions | selection_additions | review_budget_additions]
        existing["landingDocs"]["version"] = "released"
        existing["landingDocs"]["copySource"] = "generated released page titles and descriptions"
        next(r for r in existing["requirements"] if r["id"] == "generated-teaser-copy")["requirement"] = "Featured card titles, descriptions and default released links come from the generated documentation and this selection policy; no parallel hand-maintained documentation copy."
        canonical = json.dumps(existing, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                         "cc83871ba33953be15c719ab47110e939e8f14f5d8b9fcf28cdd95430437b117")
        rows = [r for r in register["requirements"] if r["id"] in additions]
        self.assertEqual({r["id"] for r in rows}, additions)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn("automation/tests/test_stdout_contract.py", row["enforcedBy"])
            for name in row["enforcedBy"]:
                self.assertTrue((ROOT / name).is_file(), name)

    def test_original_limits_protected_instructions_and_guard_order_remain(self):
        limits = json.loads((ROOT / "automation/config.json").read_text())["limits"]
        self.assertEqual({key: limits[key] for key in ("runtimeSeconds", "concurrentWorkers", "repairRounds")},
                         {"runtimeSeconds": 21600, "concurrentWorkers": 2, "repairRounds": 2})
        self.assertEqual(hashlib.sha256((ROOT / "AGENTS.md").read_bytes()).hexdigest(),
                         "4ecd73b84ae564248bfc6227326817fa8b37cff802407faadbffab64619a8441")
        tree = ast.parse((ROOT / "automation/pipeline.py").read_text())
        publish = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_publish")
        calls = [node.func.id for node in sorted(ast.walk(publish), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        guards = ["require_native_context", "configured_stable_alias", "frozen_sources", "validate_generated",
                  "require_approvals", "fork_api", "approved_push_url", "stage_reviewed_candidate"]
        self.assertEqual([name for name in calls if name in guards][:len(guards)], guards)


if __name__ == "__main__":
    unittest.main()
