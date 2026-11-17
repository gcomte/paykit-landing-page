import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("pipeline", ROOT / "automation/pipeline.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)

class CoordinatorTests(unittest.TestCase):
    def test_exclusive_lease_prevents_overlapping_runs_and_expires(self):
        self.assertTrue(hasattr(pipeline, "acquire_lease"), "run lease is missing")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            first = pipeline.acquire_lease(cache, now=100, duration=20)
            with self.assertRaisesRegex(ValueError, "already active"):
                pipeline.acquire_lease(cache, now=110, duration=20)
            second = pipeline.acquire_lease(cache, now=121, duration=20)
            self.assertNotEqual(first["id"], second["id"])
            with self.assertRaisesRegex(ValueError, "expired|owner"):
                pipeline.guard_run(first["id"], cache, now=122)
            self.assertEqual(pipeline.guard_run(second["id"], cache, now=122)["id"], second["id"])

    def test_publication_gate_requires_independent_review_and_all_walkthroughs(self):
        self.assertTrue(hasattr(pipeline, "require_approvals"), "publication approval gate is missing")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with self.assertRaisesRegex(ValueError, "review|walkthrough"):
                pipeline.require_approvals("artifacts", cache)
            pipeline.save_json(cache / "review.json", {"status": "pass", "artifactHash": "artifacts", "findings": []})
            pipeline.save_json(cache / "walkthroughs.json", [])
            with self.assertRaisesRegex(ValueError, "four|walkthrough"):
                pipeline.require_approvals("artifacts", cache)

    def test_repair_rounds_cannot_exceed_two(self):
        self.assertTrue(hasattr(pipeline, "request_repair"), "bounded repair accounting is missing")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            record = pipeline.acquire_lease(cache, now=100, duration=50)
            pipeline.request_repair(record["id"], "first", cache, now=101)
            pipeline.request_repair(record["id"], "second", cache, now=102)
            with self.assertRaisesRegex(ValueError, "repair limit"):
                pipeline.request_repair(record["id"], "third", cache, now=103)

if __name__ == "__main__":
    unittest.main()
