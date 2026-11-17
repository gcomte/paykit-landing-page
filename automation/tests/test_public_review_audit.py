"""Owned, offline synthetic fixtures; never Paykit approval or privacy proof."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODE = "instruction-based-public-evidence-v1"
VERSIONS = ("released", "coming-soon")


def load_pipeline():
    spec = importlib.util.spec_from_file_location("public_review_audit_pipeline", ROOT / "automation/pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicReviewAuditTests(unittest.TestCase):
    def setUp(self):
        self.p = load_pipeline()
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-public-review-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / ".automation"
        self.config = self.root / "automation/config.json"
        self.run_id = "synthetic-owned-run"
        self.artifact_hash = "synthetic-artifact-binding"
        self.lock = {v: {"commit": c * 40} for v, c in zip(VERSIONS, "ab")}
        self.write(self.config, {"privacy": {"boundaryMode": MODE, "requireNativeCoordinator": False},
                                 "sections": {"Understand Paykit": ["overview"]}})
        self.targets = []
        self.evidence_ids = []
        for version in VERSIONS:
            record = {"id": "synthetic-source", "version": version, "commit": self.lock[version]["commit"],
                      "path": "src/lib.rs", "startLine": 1, "endLine": 1,
                      "excerpt": "// Synthetic public fixture only.",
                      "sourceHash": self.p.digest(b"// Synthetic public fixture only.\n"),
                      "kind": "implemented", "claim": "Synthetic fixture only."}
            blueprint = {"sourceCommit": self.lock[version]["commit"], "evidence": [record],
                         "pages": [{"slug": "overview", "title": "Synthetic fixture", "description": "Not Paykit facts.",
                                    "group": "Understand Paykit", "sections": [
                                        {"id": "fixture", "title": "Synthetic", "paragraphs": ["Synthetic fixture only."],
                                         "list": ["Synthetic item"], "code": [{"code": "printf synthetic", "language": "sh"}],
                                         "expectedOutput": "synthetic", "evidence": [record["id"]],
                                         "diagram": {"id": "synthetic-flow", "caption": "Synthetic edge", "alt": "A to B"}}]}],
                         "diagrams": [{"id": "synthetic-flow", "mermaid": "flowchart TD\n A[Synthetic A] --> B[Synthetic B]",
                                       "alt": "Synthetic A to B", "relationships": [
                                           {"from": "A", "to": "B", "evidence": [record["id"]]}]}]}
            self.write(self.root / f"automation/blueprints/{version}.json", blueprint)
            self.targets.extend([f"{version}:page:overview", f"{version}:section:overview/fixture",
                                 f"{version}:diagram:synthetic-flow"])
            self.evidence_ids.append(f"{version}:synthetic-source")
        self.review = {"status": "pass", "artifactHash": self.artifact_hash, "findings": [],
                       "runId": self.run_id, "authorRunId": "synthetic-author-session",
                       "reviewerRunId": "synthetic-reviewer-session", "versions": list(VERSIONS),
                       "notes": "Synthetic review fixture; not an actual public evidence approval.",
                       "publicEvidenceAudit": {"checkedTargets": sorted(self.targets),
                                               "checkedEvidenceIds": sorted(self.evidence_ids),
                                               "unmappedClaims": [], "unsupportedClaims": [],
                                               "notes": "Synthetic attestation exercises coverage only, not semantic support or confidentiality."}}
        self.walkthroughs = []
        for version in VERSIONS:
            for audience in ("wallet", "point-of-sale"):
                archive = self.root / f"public/docs-examples/{version}/paykit-{audience}-{version}.tar.gz"
                archive.parent.mkdir(parents=True, exist_ok=True)
                with tarfile.open(archive, "w:gz") as output:
                    content = b"Synthetic archive only; no actual example execution.\n"
                    member = tarfile.TarInfo("synthetic/README.txt")
                    member.size = len(content)
                    output.addfile(member, io.BytesIO(content))
                slug = "wallets" if audience == "wallet" else audience
                self.walkthroughs.append({"version": version, "audience": audience, "exitCode": 0,
                    "artifactHash": self.artifact_hash, "stdout": self.p.EXPECTED[audience], "runId": self.run_id,
                    "reporterRunId": f"synthetic-walker-{version}-{audience}",
                    "guideUrl": f"http://synthetic.invalid/docs/{version}/{slug}",
                    "commands": ["tar xzf synthetic.tar.gz", "cargo run --locked"],
                    "archiveHash": self.p.digest(archive.read_bytes()),
                    "limitations": "Synthetic report data only; no Cargo or network ran."})
        self.write(self.cache / "runs" / self.run_id / "sources.json", self.lock)
        self.context = patch.multiple(self.p, ROOT=self.root, CACHE=self.cache, CONFIG=self.config)
        self.context.start()
        self.addCleanup(self.context.stop)
        self.owner = {"id": self.run_id, "deadline": time.time() + 600, "repairs": 0, "stages": [],
                      "children": [], "acceptanceHash": self.p.acceptance_digest(), "sourceHash": self.p.digest(self.lock)}
        self.write(self.cache / "run.lock/owner.json", self.owner)
        self.write(self.cache / "runs" / self.run_id / "run.json", self.owner)
        (self.cache / "lease-state.lock").touch()
        self.persist_reports()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n")

    def persist_reports(self):
        self.write(self.cache / "review.json", self.review)
        self.write(self.cache / "walkthroughs.json", self.walkthroughs)

    def approve(self):
        self.persist_reports()
        return self.p.require_approvals(self.artifact_hash, self.cache, run_id=self.run_id)

    def test_author_cannot_review_its_own_candidate(self):
        self.assertEqual(self.approve()["review"], self.review)
        self.review["reviewerRunId"] = self.review["authorRunId"]
        with self.assertRaisesRegex(ValueError, "author|independent"):
            self.approve()

    def test_walkers_are_four_distinct_sessions_separate_from_author_and_reviewer(self):
        self.approve()
        original = copy.deepcopy(self.walkthroughs)
        identities = [self.review["authorRunId"], self.review["reviewerRunId"],
                      original[1]["reporterRunId"], "", "   ", True, {}, ["alias"]]
        for identity in identities:
            with self.subTest(identity=identity):
                self.walkthroughs = copy.deepcopy(original)
                self.walkthroughs[0]["reporterRunId"] = identity
                with self.assertRaisesRegex(ValueError, "identity|identities|distinct"):
                    self.approve()

    def test_public_audit_requires_exact_complete_unique_typed_inventories(self):
        self.approve()
        baseline = copy.deepcopy(self.review)
        audit = baseline["publicEvidenceAudit"]
        mutations = [None, {}, True, "checked", {**audit, "extra": "not allowed"}]
        for field in audit:
            missing = copy.deepcopy(audit)
            del missing[field]
            mutations.append(missing)
        for field, values in {
            "checkedTargets": [[], audit["checkedTargets"][:-1], audit["checkedTargets"] + [audit["checkedTargets"][0]],
                               audit["checkedTargets"] + ["released:page:unknown"], True, "all", [True], [{}]],
            "checkedEvidenceIds": [[], audit["checkedEvidenceIds"][:-1], audit["checkedEvidenceIds"] * 2,
                                   ["released:unknown", "coming-soon:synthetic-source"], True, [False], [{}]],
            "unmappedClaims": [["released:overview missing mapping"], {}, False, ""],
            "unsupportedClaims": [[{"location": "released:overview", "message": "Synthetic unsupported claim"}], {}, False, ""],
            "notes": ["", "   ", True, {"explanation": "not a string"}, []],
        }.items():
            mutations.extend({**audit, field: value} for value in values)
        for mutated in mutations:
            with self.subTest(audit=mutated):
                self.review = copy.deepcopy(baseline)
                self.review["publicEvidenceAudit"] = mutated
                with self.assertRaisesRegex(ValueError, "[Pp]ublic evidence audit"):
                    self.approve()
        self.review = copy.deepcopy(baseline)
        del self.review["publicEvidenceAudit"]
        with self.assertRaisesRegex(ValueError, "[Pp]ublic evidence audit"):
            self.approve()

    def test_audit_reads_current_blueprints_instead_of_trusting_report_inventory(self):
        self.approve()
        for version in VERSIONS:
            path = self.root / f"automation/blueprints/{version}.json"
            original = json.loads(path.read_text())
            for kind in ("page", "section", "diagram", "evidence"):
                with self.subTest(version=version, kind=kind):
                    blueprint = copy.deepcopy(original)
                    if kind == "page":
                        blueprint["pages"].append({"slug": "new-page", "sections": []})
                    elif kind == "section":
                        blueprint["pages"][0]["sections"].append({"id": "new-claims"})
                    elif kind == "diagram":
                        blueprint["diagrams"].append({"id": "new-nodes-and-edges"})
                    else:
                        blueprint["evidence"].append({**blueprint["evidence"][0], "id": "new-source"})
                    self.write(path, blueprint)
                    with self.assertRaisesRegex(ValueError, "[Pp]ublic evidence audit"):
                        self.approve()
                    self.write(path, original)
        self.approve()

    def test_audit_is_bound_to_both_exact_versions_and_frozen_source_roots(self):
        self.approve()
        original_review = copy.deepcopy(self.review)
        for versions in (None, True, "released", [], ["released"], ["released", "released"],
                         ["released", "coming-soon", "unknown"], ["released", {}]):
            with self.subTest(versions=versions):
                self.review = {**original_review, "versions": versions}
                with self.assertRaisesRegex(ValueError, "[Pp]ublic evidence audit.*version"):
                    self.approve()
        self.review = original_review
        for version in VERSIONS:
            path = self.root / f"automation/blueprints/{version}.json"
            original = json.loads(path.read_text())
            for field in ("sourceCommit", "evidence.version", "evidence.commit"):
                with self.subTest(version=version, field=field):
                    blueprint = copy.deepcopy(original)
                    if field == "sourceCommit":
                        blueprint[field] = "c" * 40
                    else:
                        blueprint["evidence"][0][field.split(".")[1]] = "wrong-snapshot"
                    self.write(path, blueprint)
                    with self.assertRaisesRegex(ValueError, "[Pp]ublic evidence audit.*snapshot"):
                        self.approve()
                    self.write(path, original)
        changed_lock = copy.deepcopy(self.lock)
        changed_lock["released"]["commit"] = "c" * 40
        self.write(self.cache / "runs" / self.run_id / "sources.json", changed_lock)
        with self.assertRaisesRegex(ValueError, "Frozen source inputs changed"):
            self.approve()

    def test_legacy_config_without_mode_preserves_old_report_data_contract(self):
        self.write(self.config, {"privacy": {"requireNativeCoordinator": False}})
        del self.review["authorRunId"]
        del self.review["publicEvidenceAudit"]
        del self.review["versions"]
        for row in self.walkthroughs:
            row["reporterRunId"] = self.review["reviewerRunId"]
        self.assertEqual(self.approve()["review"], self.review)

    def prepare_cli(self):
        # Read the real Git index only, with an owned synthetic worktree. No Git
        # init/add/commit/config or live-run files are touched by these fixtures.
        (self.root / "automation/pipeline.py").write_bytes((ROOT / "automation/pipeline.py").read_bytes())
        (self.root / ".gitignore").write_text(".automation/\n__pycache__/\n")
        self.owner["acceptanceHash"] = self.p.acceptance_digest()
        self.write(self.cache / "run.lock/owner.json", self.owner)
        self.write(self.cache / "runs" / self.run_id / "run.json", self.owner)
        (self.cache / "review.json").unlink()
        (self.cache / "walkthroughs.json").unlink()
        environment = os.environ.copy()
        environment.update(GIT_DIR=str(ROOT / ".git"), GIT_WORK_TREE=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        return environment

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_runnable_review_targets_cli_is_exact_read_only_and_not_an_approval(self):
        environment = self.prepare_cli()
        before = self.snapshot()
        expected_hash = self.p.digest({name: self.p.digest(content) for name, content in before.items()
                                       if not name.startswith(".automation/")})
        result = subprocess.run([sys.executable, "automation/pipeline.py", "review-targets", "--run", self.run_id],
                                cwd=self.root, env=environment, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"runId": self.run_id, "artifactHash": expected_hash,
                         "checkedTargets": sorted(self.targets), "checkedEvidenceIds": sorted(self.evidence_ids)})
        self.assertEqual(self.snapshot(), before)
        missing_run = subprocess.run([sys.executable, "automation/pipeline.py", "review-targets"],
                                     cwd=self.root, env=environment, text=True, capture_output=True, timeout=20)
        self.assertEqual(missing_run.returncode, 2)
        self.assertIn("--run is required", missing_run.stderr)

    def test_existing_approval_gates_still_reject_individual_mutants(self):
        self.approve()
        baseline_review = copy.deepcopy(self.review)
        baseline_walkthroughs = copy.deepcopy(self.walkthroughs)
        cases = [("review", "status", "fail", "Independent review failed"),
                 ("review", "findings", ["Synthetic unsupported assertion"], "Independent review failed"),
                 ("review", "artifactHash", "changed", "artifacts changed"),
                 ("review", "runId", "another-run", "review run identity"),
                 ("walk", "version", "unknown", "All four"),
                 ("walk", "audience", "unknown", "All four"),
                 ("walk", "exitCode", 1, "Fresh walkthrough failed"),
                 ("walk", "stdout", "not promised", "Fresh walkthrough failed"),
                 ("walk", "artifactHash", "changed", "Fresh walkthrough failed"),
                 ("walk", "runId", "another-run", "walkthrough run identity"),
                 ("walk", "guideUrl", "http://synthetic.invalid/docs/coming-soon/wallets", "guide URL"),
                 ("walk", "commands", ["cargo run"], "literal locked cargo run"),
                 ("walk", "archiveHash", "changed", "archive hash")]
        for where, field, value, message in cases:
            with self.subTest(where=where, field=field):
                self.review = copy.deepcopy(baseline_review)
                self.walkthroughs = copy.deepcopy(baseline_walkthroughs)
                target = self.review if where == "review" else self.walkthroughs[0]
                target[field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.approve()

    def test_author_and_reviewer_session_identities_are_strict_nonempty_strings(self):
        self.approve()
        baseline = copy.deepcopy(self.review)
        for field in ("authorRunId", "reviewerRunId"):
            for value in (None, "", "   ", " padded ", True, {}, ["alias"]):
                with self.subTest(field=field, value=value):
                    self.review = {**baseline, field: value}
                    with self.assertRaisesRegex(ValueError, "identity|identities"):
                        self.approve()

    def test_review_targets_checks_actual_artifact_bytes_before_and_after_assembly(self):
        environment = self.prepare_cli()
        inventory = self.p.public_review_inventory
        path = self.root / "automation/blueprints/released.json"

        def edit_after_inventory(lock):
            result = inventory(lock)
            blueprint = json.loads(path.read_text())
            blueprint["pages"][0]["sections"][0]["paragraphs"].append("Synthetic late edit.")
            self.write(path, blueprint)
            return result

        with patch.dict(os.environ, environment, clear=True), \
             patch.object(self.p, "public_review_inventory", side_effect=edit_after_inventory), \
             patch.object(sys, "argv", ["automation/pipeline.py", "review-targets", "--run", self.run_id]):
            with self.assertRaisesRegex(ValueError, "Artifacts changed while assembling"):
                self.p.main()
        self.assertFalse((self.cache / "review.json").exists())
        self.assertEqual(json.loads((self.cache / "run.lock/owner.json").read_text()), self.owner)

    def test_review_targets_rejects_unfrozen_changed_or_wrong_source_context(self):
        environment = self.prepare_cli()
        source_path = self.cache / "runs" / self.run_id / "sources.json"
        changed_lock = copy.deepcopy(self.lock)
        changed_lock["released"]["commit"] = "c" * 40
        self.write(source_path, changed_lock)
        result = subprocess.run([sys.executable, "automation/pipeline.py", "review-targets", "--run", self.run_id],
                                cwd=self.root, env=environment, text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Frozen source inputs changed", result.stderr)
        self.assertEqual(result.stdout, "")
        self.write(source_path, self.lock)
        self.owner.pop("acceptanceHash")
        self.write(self.cache / "run.lock/owner.json", self.owner)
        result = subprocess.run([sys.executable, "automation/pipeline.py", "review-targets", "--run", self.run_id],
                                cwd=self.root, env=environment, text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Acceptance policy not frozen", result.stderr)
        self.assertFalse((self.cache / "review.json").exists())


    def test_instruction_mode_requires_present_list_valued_empty_findings(self):
        baseline = copy.deepcopy(self.review)
        self.assertIs(type(baseline["findings"]), list)
        self.assertEqual(baseline["findings"], [])
        cases = [("missing", None), ("null", None), ("boolean", False),
                 ("integer", 0), ("float", 0.0), ("object", {}), ("text", "")]
        for label, value in cases:
            with self.subTest(findings=label):
                self.review = copy.deepcopy(baseline)
                self.assertEqual(self.approve()["review"], baseline)
                if label == "missing":
                    del self.review["findings"]
                    self.assertNotIn("findings", self.review)
                else:
                    self.review["findings"] = value
                with self.assertRaisesRegex(ValueError, "Independent review failed"):
                    self.approve()
        self.review = baseline
        self.assertEqual(self.approve()["review"], baseline)


    def test_instruction_mode_requires_present_nonblank_outer_review_notes(self):
        baseline = copy.deepcopy(self.review)
        cases = [("missing", None), ("null", None), ("boolean", False),
                 ("integer", 0), ("float", 0.0), ("object", {}), ("list", []),
                 ("empty", ""), ("whitespace", " \t\r\n")]
        for label, value in cases:
            with self.subTest(notes=label):
                self.review = copy.deepcopy(baseline)
                self.assertEqual(self.approve()["review"], baseline)
                if label == "missing":
                    del self.review["notes"]
                    self.assertNotIn("notes", self.review)
                else:
                    self.review["notes"] = value
                with self.assertRaisesRegex(ValueError, "Independent review notes"):
                    self.approve()
        self.review = baseline
        self.assertEqual(self.approve()["review"], baseline)


    def test_instruction_mode_walkthrough_exit_code_is_exact_integer_zero(self):
        baseline = copy.deepcopy(self.walkthroughs)
        cases = [("missing", None), ("null", None), ("false", False), ("true", True),
                 ("float-zero", 0.0), ("negative-float-zero", -0.0),
                 ("nonzero", 1), ("text", "0"), ("object", {}), ("list", [])]
        for label, value in cases:
            with self.subTest(exitCode=label):
                self.walkthroughs = copy.deepcopy(baseline)
                self.assertEqual(self.approve()["walkthroughs"], baseline)
                if label == "missing":
                    del self.walkthroughs[0]["exitCode"]
                    self.assertNotIn("exitCode", self.walkthroughs[0])
                else:
                    self.walkthroughs[0]["exitCode"] = value
                with self.assertRaisesRegex(ValueError, "Fresh walkthrough failed"):
                    self.approve()
        self.walkthroughs = baseline
        self.assertEqual(self.approve()["walkthroughs"], baseline)


    def test_instruction_mode_accepts_empty_findings_text_notes_and_integer_zero(self):
        self.review["notes"] = " \tSynthetic complete review with explanatory text.\n"
        approved = self.approve()
        self.assertIs(type(approved["review"]["findings"]), list)
        self.assertEqual(approved["review"]["findings"], [])
        self.assertIs(type(approved["review"]["notes"]), str)
        self.assertTrue(approved["review"]["notes"].strip())
        self.assertEqual(approved["review"], self.review)
        self.assertEqual(len(approved["walkthroughs"]), 4)
        for result in approved["walkthroughs"]:
            self.assertIs(type(result["exitCode"]), int)
            self.assertEqual(result["exitCode"], 0)

    def test_legacy_no_mode_retains_r1_field_and_exit_code_behavior(self):
        self.approve()
        config = json.loads(self.config.read_text())
        del config["privacy"]["boundaryMode"]
        self.write(self.config, config)
        self.assertNotIn("boundaryMode", config["privacy"])
        baseline_review = copy.deepcopy(self.review)
        baseline_walkthroughs = copy.deepcopy(self.walkthroughs)
        for field, cases in {
            "findings": [("missing", None), ("null", None), ("false", False),
                         ("zero", 0), ("float-zero", 0.0), ("object", {}), ("empty", "")],
            "notes": [("missing", None), ("null", None), ("false", False),
                      ("zero", 0), ("object", {}), ("list", []), ("empty", ""), ("whitespace", " \t\n")],
        }.items():
            for label, value in cases:
                with self.subTest(field=field, legacy=label):
                    self.review = copy.deepcopy(baseline_review)
                    self.assertEqual(self.approve()["review"], baseline_review)
                    if label == "missing":
                        del self.review[field]
                        self.assertNotIn(field, self.review)
                    else:
                        self.review[field] = value
                    approved = self.approve()["review"]
                    self.assertEqual(approved, self.review)
                    if label != "missing":
                        self.assertIs(type(approved[field]), type(value))
        self.review = baseline_review
        for value in (False, 0.0):
            with self.subTest(legacyExitCode=repr(value)):
                self.walkthroughs = copy.deepcopy(baseline_walkthroughs)
                self.assertEqual(self.approve()["walkthroughs"], baseline_walkthroughs)
                self.walkthroughs[0]["exitCode"] = value
                approved = self.approve()["walkthroughs"][0]
                self.assertIs(type(approved["exitCode"]), type(value))
                self.assertEqual(approved["exitCode"], value)


if __name__ == "__main__":
    unittest.main()
