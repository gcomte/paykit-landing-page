"""Acceptance tests are independent of generated prose and model verdicts."""
import importlib.util
import os
from pathlib import Path
import unittest

MODULE = Path(__file__).parents[1] / "pipeline.py"

class SourcePolicyTests(unittest.TestCase):
    def test_highest_stable_ignores_rc_even_if_metadata_says_stable(self):
        self.assertTrue(MODULE.exists(), "source resolver has not been implemented")
        spec = importlib.util.spec_from_file_location("pipeline", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        releases = [
            {"tag_name": "v0.1.0", "draft": False, "prerelease": False},
            {"tag_name": "v0.1.1-rc6", "draft": False, "prerelease": False},
            {"tag_name": "v9.0.0", "draft": True, "prerelease": False},
            {"tag_name": "v8.0.0", "draft": False, "prerelease": True},
            {"tag_name": "v0.10.0", "draft": False, "prerelease": False},
            {"tag_name": "v0.2.0", "draft": False, "prerelease": False},
        ]
        self.assertEqual(module.select_release(releases)["tag_name"], "v0.10.0")

    def test_no_release_does_not_fall_back_to_master(self):
        spec = importlib.util.spec_from_file_location("pipeline", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(ValueError, "retain last valid"):
            module.select_release([{"tag_name": "v1.0.0-beta.2", "draft": False, "prerelease": False}])

    def test_public_source_policy_rejects_private_or_unapproved_input(self):
        spec = importlib.util.spec_from_file_location("pipeline", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "approved_source"), "public-only source policy is missing")
        self.assertTrue(module.approved_source("https://github.com/pubky/paykit-rs/blob/" + "a" * 40 + "/paykit-lib/src/lib.rs"))
        self.assertFalse(module.approved_source("https://drive.google.com/file/d/private"))
        self.assertFalse(module.approved_source("https://github.com/pubky/paykit-rs.evil/blob/a"))
        self.assertFalse(module.approved_source("https://github.com/other/private"))

    def test_snapshots_resolve_from_real_canonical_repository(self):
        spec = importlib.util.spec_from_file_location("pipeline", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "resolve_sources"), "immutable snapshot resolver is missing")
        frozen = os.environ.get("PAYKIT_TEST_SOURCES")
        lock = module.read_json(frozen) if frozen else module.resolve_sources()
        self.assertEqual(lock["repository"], "pubky/paykit-rs")
        for version in ("released", "coming-soon"):
            import tomllib
            manifest = tomllib.loads(module.source_text(lock[version]["commit"], "paykit-lib/Cargo.toml"))
            self.assertEqual(manifest["package"]["version"], lock[version]["packages"]["paykit-lib"]["version"])
        self.assertRegex(lock["released"]["commit"], r"^[a-f0-9]{40}$")
        self.assertRegex(lock["coming-soon"]["commit"], r"^[a-f0-9]{40}$")
        self.assertTrue(module.STABLE.fullmatch(lock["released"]["tag"]))
        self.assertTrue(lock["released"]["packages"]["paykit-lib"]["published"])
        self.assertIn("pubky", lock["released"]["packages"]["paykit-lib"]["dependencies"])

    def test_generation_fingerprint_ignores_timestamps_but_tracks_instructions(self):
        spec = importlib.util.spec_from_file_location("pipeline", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "input_fingerprint"), "change detection is missing")
        import tempfile, os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "automation/skills").mkdir(parents=True)
            rules = root / "automation/skills/writing.md"
            rules.write_text("reader-first v1")
            before = module.input_fingerprint({"released": {"commit": "a" * 40}}, root, {"node": "26"})
            os.utime(rules, (1, 1))
            self.assertEqual(before, module.input_fingerprint({"released": {"commit": "a" * 40}}, root, {"node": "26"}))
            rules.write_text("reader-first v2")
            self.assertNotEqual(before, module.input_fingerprint({"released": {"commit": "a" * 40}}, root, {"node": "26"}))

    def test_published_example_crate_matches_the_selected_source_commit(self):
        spec = importlib.util.spec_from_file_location("pipeline", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "verify_published_package"), "published source verification is missing")
        lock = module.read_json(os.environ.get("PAYKIT_TEST_SOURCES", module.ROOT / "automation/sources.lock.json"))
        result = module.verify_published_package("paykit-lib", lock["released"]["packages"]["paykit-lib"], lock["released"]["commit"])
        self.assertEqual(result["sourceCommit"], lock["released"]["commit"])
        self.assertGreater(result["verifiedRustFiles"], 0)

if __name__ == "__main__":
    unittest.main()
