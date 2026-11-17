import importlib.util
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("pipeline", ROOT / "automation/pipeline.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)

class EvidenceTests(unittest.TestCase):
    def test_evidence_rejects_wrong_version_before_reading_files(self):
        self.assertTrue(hasattr(pipeline, "validate_evidence"), "evidence validation is missing")
        lock = {"released": {"commit": "a" * 40}}
        record = {"id": "bad", "version": "released", "commit": "b" * 40, "path": "paykit-lib/src/lib.rs"}
        with self.assertRaisesRegex(ValueError, "version snapshot"):
            pipeline.validate_evidence([record], lock, "released")

    def test_assembled_pages_get_only_their_matching_version_marker(self):
        self.assertTrue(hasattr(pipeline, "assemble_pages"), "deterministic page generation is missing")
        lock = {"released": {"commit": "a" * 40, "label": "v1.0.0", "tag": "v1.0.0", "packages": {}}}
        blueprint = {"pages": [{"slug": "overview", "sections": []}]}
        output = pipeline.assemble_pages(blueprint, lock, "released")
        self.assertEqual(output["version"]["commit"], "a" * 40)
        self.assertNotIn("version", blueprint)
        self.assertEqual(output["pages"][0]["slug"], "overview")

    def test_readiness_notice_is_source_driven_not_inferred_from_stable_tag(self):
        self.assertTrue(hasattr(pipeline, "readiness_metadata"), "source readiness preservation is missing")
        lock = pipeline.read_json(os.environ.get("PAYKIT_TEST_SOURCES", pipeline.ROOT / "automation/sources.lock.json"))
        notice = pipeline.readiness_metadata(lock["released"]["commit"])
        upstream = pipeline.source_text(lock["released"]["commit"], "README.md").lower()
        if "not for production" in upstream:
            self.assertEqual(notice["status"], "experimental")
            self.assertIn("not for production", notice["notice"].lower())
        else:
            self.assertEqual(notice["status"], "unassessed")
            self.assertIn("does not establish production readiness", notice["notice"])
        self.assertIn(lock["released"]["commit"], notice["sourceUrl"])
        if "evidence" in notice:
            pipeline.validate_evidence([notice["evidence"]], lock, "released")
        else:
            self.assertEqual(notice["attribution"], "documentation-policy")
            self.assertEqual(notice["status"], "unassessed")

    def test_evidence_contract_anchors_resolve_per_snapshot_and_reject_wrong_ranges(self):
        self.assertTrue(hasattr(pipeline, "anchor_evidence"), "semantic evidence anchoring is missing")
        lock = pipeline.read_json(os.environ.get("PAYKIT_TEST_SOURCES", pipeline.ROOT / "automation/sources.lock.json"))
        blueprint = pipeline.read_json(pipeline.ROOT / "automation/blueprints/coming-soon.json")
        original = next(item for item in blueprint["evidence"] if item["id"] == "coming-soon-storage-contract")
        bad = dict(original, startLine=70, endLine=78, anchor={
            "start": "/// Authoritative logical state boundary for Paykit SDK.",
            "end": "pub trait StorageAdapter: Send + Sync {"})
        source = pipeline.source_text(bad["commit"], bad["path"])
        bad["excerpt"] = "\n".join(source.splitlines()[69:78])
        resolved = pipeline.anchor_evidence(bad)
        self.assertIn("atomic transactions", resolved["excerpt"])
        self.assertIn("pub trait StorageAdapter", resolved["excerpt"])
        pipeline.validate_evidence([resolved], lock, "coming-soon")
        with self.assertRaisesRegex(ValueError, "anchor"):
            pipeline.validate_evidence([bad], lock, "coming-soon")

if __name__ == "__main__":
    unittest.main()
