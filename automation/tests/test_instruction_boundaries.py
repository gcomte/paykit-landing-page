"""Instruction-only privacy policy; content provenance/approval stays mandatory."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODE = "instruction-based-public-evidence-v1"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("instruction_boundary_pipeline", ROOT / "automation/pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstructionBoundaryTests(unittest.TestCase):
    def test_operator_explicitly_chooses_instruction_mode_without_native_coordinator(self):
        config = json.loads((ROOT / "automation/config.json").read_text(encoding="utf-8"))
        privacy = config.get("privacy", {})
        self.assertEqual(privacy.get("boundaryMode"), MODE)
        self.assertIs(privacy.get("requireNativeCoordinator"), False)
        self.assertIs(privacy.get("enforcedIsolationRequired"), False)
        self.assertIs(privacy.get("requirePublicSourceMapping"), True)
        self.assertIs(privacy.get("requireIndependentPublicEvidenceReview"), True)
        self.assertNotIn("isolationVersion", privacy)

    def test_instruction_mode_tool_versions_ignore_retired_native_receipts(self):
        pipeline = load_pipeline()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            automation = root / "automation"
            cache = root / ".automation"
            automation.mkdir()
            cache.mkdir()
            config = automation / "config.json"
            config.write_text(json.dumps({"privacy": {"boundaryMode": MODE, "requireNativeCoordinator": False}}))
            script = automation / "hermes_coordinator.py"
            script.write_text("# synthetic retired native coordinator\n")
            metadata = {"hermesRevision": "a" * 40, "coordinatorSha256": pipeline.digest(script.read_bytes()),
                        "isolationVersion": "native-public-tools-v1"}
            receipt = cache / "native-runtime.json"
            receipt.write_text(json.dumps(metadata))
            before = receipt.read_bytes()
            with patch.object(pipeline, "ROOT", root), patch.object(pipeline, "CACHE", cache), \
                 patch.object(pipeline, "CONFIG", config), patch.object(pipeline, "run", return_value="synthetic-tool-version"):
                versions = pipeline.tool_versions()
                self.assertNotIn("hermesRevision", versions)
                self.assertNotIn("coordinatorSha256", versions)
                self.assertNotIn("isolationVersion", versions)
                self.assertEqual(receipt.read_bytes(), before)

    def test_portable_policies_keep_operator_account_settings_out_of_tracked_instructions(self):
        paths = ["COORDINATOR.md", "README.md", "SOURCE-BOUNDARY.md", "LOCAL-EXECUTION.md"]
        paths.extend("skills/" + role + ".md" for role in ("coordinator", "sources", "writing", "diagrams", "evaluation"))
        for name in paths:
            with self.subTest(name=name):
                text = (ROOT / "automation" / name).read_text(encoding="utf-8")
                self.assertNotIn("gcomte/paykit-landing-page", text)
                self.assertNotIn("paykit-landing-page-lake.vercel.app", text)

    def test_instruction_mode_local_policy_changes_trigger_generation(self):
        pipeline = load_pipeline()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("automation/LOCAL-EXECUTION.md", "automation/local-execution-policy.json"):
                with self.subTest(name=name):
                    policy = root / name
                    policy.parent.mkdir(parents=True, exist_ok=True)
                    policy.write_text("synthetic-before")
                    before = pipeline.input_fingerprint({}, root, {})
                    policy.write_text("synthetic-after")
                    self.assertNotEqual(before, pipeline.input_fingerprint({}, root, {}))

    def test_instruction_mode_local_policy_changes_invalidate_frozen_acceptance(self):
        pipeline = load_pipeline()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("automation/LOCAL-EXECUTION.md", "automation/local-execution-policy.json"):
                with self.subTest(name=name):
                    policy = root / name
                    policy.parent.mkdir(parents=True, exist_ok=True)
                    policy.write_text("synthetic-before")
                    before = pipeline.acceptance_digest(root)
                    policy.write_text("synthetic-after")
                    self.assertNotEqual(before, pipeline.acceptance_digest(root))

    def test_instruction_mode_still_requires_the_stable_deployment_alias(self):
        pipeline = load_pipeline()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".automation"
            cache.mkdir()
            config = root / "config.json"
            config.write_text(json.dumps({"privacy": {"boundaryMode": MODE, "requireNativeCoordinator": False}}))
            with patch.object(pipeline, "CONFIG", config), patch.object(pipeline, "CACHE", cache):
                with self.assertRaisesRegex(ValueError, "[Ss]table.*alias"):
                    pipeline.configured_stable_alias()
                (cache / "deployment.json").write_text(json.dumps({"stableDevelopmentUrl": "https://synthetic-alias.vercel.app/"}))
                self.assertEqual(pipeline.configured_stable_alias(), "https://synthetic-alias.vercel.app")

    def test_instruction_mode_does_not_consume_or_require_native_context_receipts(self):
        pipeline = load_pipeline()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"privacy": {"boundaryMode": MODE, "requireNativeCoordinator": False}}))
            with patch.object(pipeline, "CONFIG", config), patch.object(pipeline, "CACHE", root / "cache"), \
                 patch.object(pipeline, "native_runtime_versions", side_effect=AssertionError("retired native preflight used")):
                pipeline.bind_native_context({"id": "b" * 32})
                pipeline.require_native_context("b" * 32)
                self.assertFalse((root / "cache").exists())

    def test_public_evidence_bytes_and_version_checks_remain_mandatory(self):
        pipeline = load_pipeline()
        source = "pub const EXAMPLE_IS_READY: bool = false;\n"
        record = {"id": "synthetic-readiness", "version": "released", "commit": "a" * 40,
                  "path": "src/lib.rs", "startLine": 1, "endLine": 1, "excerpt": source.strip(),
                  "sourceHash": pipeline.digest(source.encode()), "kind": "implemented",
                  "claim": "The synthetic example is not ready."}
        lock = {"released": {"commit": "a" * 40}}
        with patch.object(pipeline, "source_text", return_value=source):
            self.assertEqual(pipeline.validate_evidence([record], lock, "released"), {record["id"]})
            for changes, message in (({"sourceHash": "b" * 64}, "Source hash"),
                                     ({"commit": "b" * 40}, "version snapshot"),
                                     ({"excerpt": "unsupported replacement"}, "excerpt differs")):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                    pipeline.validate_evidence([{**record, **changes}], lock, "released")

    def test_unmapped_section_or_diagram_is_not_accepted_in_instruction_mode(self):
        import copy
        pipeline = load_pipeline()
        source = "// Synthetic public-only mapping fixture, not Paykit behavior.\n"
        lock = {"released": {"commit": "a" * 40}}
        record = {"id": "synthetic-source", "version": "released", "commit": "a" * 40,
                  "path": "src/lib.rs", "startLine": 1, "endLine": 1, "excerpt": source.strip(),
                  "sourceHash": pipeline.digest(source.encode()), "kind": "implemented", "claim": "Synthetic fixture."}
        blueprint = {"sourceCommit": "a" * 40, "evidence": [record],
                     "pages": [{"slug": "overview", "group": "Understand Paykit", "sections": [
                         {"id": "synthetic", "paragraphs": ["Synthetic fixture."], "evidence": [record["id"]]}]}],
                     "diagrams": [{"alt": "Synthetic relation", "relationships": [{"evidence": [record["id"]]}]}]}
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"sections": {"Understand Paykit": ["overview"]},
                                          "privacy": {"boundaryMode": MODE, "requireNativeCoordinator": False}}))
            with patch.object(pipeline, "CONFIG", config), patch.object(pipeline, "source_text", return_value=source):
                self.assertTrue(pipeline.validate_blueprint(blueprint, lock, "released"))
                missing_section = copy.deepcopy(blueprint)
                missing_section["pages"][0]["sections"][0]["evidence"] = []
                with self.assertRaisesRegex(ValueError, "Unsupported section"):
                    pipeline.validate_blueprint(missing_section, lock, "released")
                missing_edge = copy.deepcopy(blueprint)
                missing_edge["diagrams"][0]["relationships"][0]["evidence"] = ["unmapped-id"]
                with self.assertRaisesRegex(ValueError, "Unsupported diagram"):
                    pipeline.validate_blueprint(missing_edge, lock, "released")
                private_link = copy.deepcopy(blueprint)
                private_link["pages"][0]["sourceLinks"] = [{"href": "https://drive.google.com/file/d/synthetic-private"}]
                with self.assertRaisesRegex(ValueError, "Unapproved public source"):
                    pipeline.validate_blueprint(private_link, lock, "released")

    def test_soft_boundary_never_waives_independent_review_and_four_walkthroughs(self):
        pipeline = load_pipeline()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with self.assertRaisesRegex(ValueError, "Independent review"):
                pipeline.require_approvals("a" * 64, cache, run_id="b" * 32)
            (cache / "review.json").write_text(json.dumps({"status": "pass", "artifactHash": "a" * 64,
                "findings": [], "runId": "b" * 32, "reviewerRunId": "synthetic-independent-reviewer"}))
            (cache / "walkthroughs.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "All four"):
                pipeline.require_approvals("a" * 64, cache, run_id="b" * 32)
            (cache / "review.json").write_text(json.dumps({"status": "fail", "artifactHash": "a" * 64,
                "findings": ["Synthetic unsupported assertion"], "runId": "b" * 32,
                "reviewerRunId": "synthetic-independent-reviewer"}))
            with self.assertRaisesRegex(ValueError, "Independent review failed"):
                pipeline.require_approvals("a" * 64, cache, run_id="b" * 32)


if __name__ == "__main__":
    unittest.main()
