import contextlib
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("corrections_pipeline", Path(__file__).parents[1] / "pipeline.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class UserCorrectionsTests(unittest.TestCase):
    def test_change_detection_tracks_user_register_and_root_landing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("automation/corrections.json", "automation/SOURCE-BOUNDARY.md", "components/Landing.tsx", "app/page.tsx"):
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("before")
                before = p.input_fingerprint({}, root, {})
                file.write_text("after")
                self.assertNotEqual(before, p.input_fingerprint({}, root, {}), name)

    def test_generation_rejects_missing_featured_pages_and_wrong_card_count(self):
        self.assertTrue(hasattr(p, "validate_corrections"), "persistent correction enforcement is missing")
        original = p.read_json(p.ROOT / "automation/corrections.json")
        selector = p.generation_config()
        pages = [{"slug": slug, "title": slug, "description": "Test fixture description"}
                 for slug in original["landingDocs"]["featuredSlugs"]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "automation/corrections.json"
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps(original))
            p.save_json(root / "docs-generation.json", selector)
            with patch.object(p, "ROOT", root):
                p.validate_corrections({"coming-soon": {"pages": pages}})
                with self.assertRaisesRegex(ValueError, "featured|missing"):
                    p.validate_corrections({"coming-soon": {"pages": pages[:-1]}})
                original["landingDocs"]["featuredSlugs"] = ["wallets"]
                file.write_text(json.dumps(original))
                with self.assertRaisesRegex(ValueError, "three"):
                    p.validate_corrections({"coming-soon": {"pages": pages}})

    @contextlib.contextmanager
    def native_publication_fixture(self, alias=None):
        # Real checkpoint/lease writes are isolated fixtures, never live state.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, cfg = root / ".automation", root / "config.json"
            p.save_json(cfg, {"privacy": {"requireNativeCoordinator": True},
                              "publishing": {"branch": "docs-automation", "deploymentTimeoutSeconds": 2}})
            if alias is not None:
                p.save_json(cache / "deployment.json", {"stableDevelopmentUrl": alias})
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), patch.object(p, "CONFIG", cfg):
                record = p.acquire_lease(cache)
                lock, manifest, sha = {"fixture": "public sources"}, {"fingerprint": "fixture-new-inputs"}, "b" * 40
                record.update(acceptanceHash=p.acceptance_digest(root), sourceHash=p.digest(lock))
                p.save_json(cache / "run.lock/owner.json", record)
                p.save_json(cache / "runs" / record["id"] / "sources.json", lock)
                p.save_json(root / "public/docs-manifest.json", manifest)
                p.save_json(cache / "pending-publication.json", {"runId": record["id"], "sha": sha, "fingerprint": manifest["fingerprint"]})
                p.save_json(cache / "last-success.json", {"fingerprint": "fixture-last-good", "deploymentUrl": "https://previous-fixture.vercel.app"})
                checkpoint = cache / "last-success.json"
                before = checkpoint.read_bytes()
                def api(endpoint):
                    if endpoint.startswith("deployments?"):
                        return [{"id": 1, "environment": "Preview", "sha": sha}]
                    if endpoint.startswith("deployments/"):
                        return [{"state": "success", "environment_url": "https://preview-fixture.vercel.app"}]
                    return {"commit": {"sha": sha}}
                with patch.object(p, "fork_api", side_effect=api), \
                     patch.object(p, "get_json", return_value=manifest) as fetch, \
                     contextlib.redirect_stdout(io.StringIO()):
                    yield record["id"], checkpoint, before, manifest, fetch

    def test_native_verification_rejects_missing_or_invalid_alias_preserving_last_good(self):
        for alias in (None, "", "http://fixture.vercel.app", "https://fixture.vercel.app/path",
                      "https://fixture.vercel.app?query=1", "https://fixture.vercel.app#fragment", 123):
            with self.subTest(alias=alias), self.native_publication_fixture(alias) as fixture:
                run_id, checkpoint, before, _, _ = fixture
                with self.assertRaisesRegex(ValueError, "[Ss]table.*alias"):
                    p._verify_publication(run_id)
                self.assertEqual(checkpoint.read_bytes(), before)
                self.assertTrue((p.CACHE / "run.lock/owner.json").exists())
                self.assertNotEqual(p.read_json(p.CACHE / "runs" / run_id / "run.json").get("status"), "published")

    def test_native_configured_alias_finalizes_only_after_manifest_match(self):
        with self.native_publication_fixture("https://stable-fixture.vercel.app/") as fixture:
            run_id, checkpoint, before, manifest, fetch = fixture
            fetch.side_effect = [manifest, {"fingerprint": "fixture-old-deployment"}]
            with self.assertRaisesRegex(ValueError, "[Ss]table.*alias"):
                p._verify_publication(run_id)
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertTrue((p.CACHE / "run.lock/owner.json").exists())
            fetch.side_effect = None
            result = p._verify_publication(run_id)
            self.assertEqual(result["stableDevelopmentUrl"], "https://stable-fixture.vercel.app")
            self.assertEqual(p.read_json(checkpoint), result)
            self.assertFalse((p.CACHE / "run.lock").exists())

    def test_native_publication_preflights_alias_before_git_effects(self):
        with self.native_publication_fixture() as fixture:
            run_id, checkpoint, before, manifest, fetch = fixture
            record = p.guard_run(run_id)
            p.save_json(p.CACHE / "validation.json", {"runId": run_id, "repairRound": record["repairs"],
                        "acceptanceHash": record["acceptanceHash"], "artifactHash": "fixture", "fingerprint": "fixture"})
            calls = []
            def git(args, **kwargs):
                calls.append(args)
                if args[1:3] == ["branch", "--show-current"]:
                    return "docs-automation"
                if args[1] == "ls-remote":
                    return "b" * 40
                if args[1] == "rev-parse":
                    return "fixture-tree" if args[-1] == "HEAD^{tree}" else "b" * 40
                return ""
            with patch.object(p, "require_native_context"), patch.object(p, "validate_generated"), \
                 patch.object(p, "artifact_digest", return_value="fixture"), \
                 patch.object(p, "input_fingerprint", return_value="fixture"), \
                 patch.object(p, "require_approvals"), patch.object(p, "approved_push_url", return_value="https://github.com/fixture-owner/paykit-landing-page.git"), \
                 patch.object(p, "stage_reviewed_candidate", return_value="fixture-tree"), patch.object(p, "enforce_publication_scope"), \
                 patch.object(p, "fork_api", return_value={"fork": True, "parent": {"full_name": "pubky/paykit-landing-page"}, "permissions": {"push": True}}), \
                 patch.object(p, "run", side_effect=git):
                with self.assertRaisesRegex(ValueError, "[Ss]table.*alias"):
                    p._publish(run_id)
                self.assertEqual(calls, [], "Alias configuration must fail before any Git publication operation")
                self.assertEqual(checkpoint.read_bytes(), before)
                self.assertTrue((p.CACHE / "run.lock/owner.json").exists())
                # A configured alias still serves OLD inputs before deployment.
                # Publication preflight must not fetch/compare its new manifest.
                p.save_json(p.CACHE / "deployment.json", {"stableDevelopmentUrl": "https://stable-fixture.vercel.app"})
                fetch.side_effect = AssertionError("Pre-push alias manifest read is premature")
                p._publish(run_id)
                fetch.assert_not_called()
                self.assertTrue(any(args[1] == "push" for args in calls), "Configured fixture never reached synthetic push")
                self.assertEqual(checkpoint.read_bytes(), before)
                self.assertTrue((p.CACHE / "run.lock/owner.json").exists())

    def test_native_no_change_rejects_missing_required_alias(self):
        with self.native_publication_fixture() as fixture:
            run_id, checkpoint, before, _, fetch = fixture
            p.release_lease(run_id, "failed")
            cfg = p.read_json(p.CONFIG)
            cfg["limits"] = {"runtimeSeconds": 10}
            p.save_json(p.CONFIG, cfg)
            selector = {"schemaVersion": 1, "source": {"repository": "pubky/paykit-rs", "kind": "tag", "ref": "synthetic-tag"}, "defaultVersion": "coming-soon"}
            p.save_json(p.ROOT / "docs-generation.json", selector)
            lock = {"repository": "pubky/paykit-rs", "defaultVersion": "coming-soon", "coming-soon": {"commit": "a" * 40},
                    "selectedSource": {**selector["source"], "qualifiedRef": "refs/tags/synthetic-tag", "object": "b" * 40, "commit": "a" * 40}}
            lock["coming-soon"].update({k: v for k, v in lock["selectedSource"].items() if k != "repository"})
            lock["coming-soon"].update(tag="synthetic-tag", label="synthetic-tag", sourceUrl="https://github.com/pubky/paykit-rs/tree/" + "a" * 40)
            p.save_json(p.CACHE / "candidate-sources.json", lock)
            confirmed = p.read_json(checkpoint)
            confirmed.update(verifiedDeployment=True, regenerationFingerprint=p.regeneration_fingerprint(lock, tools={}))
            p.save_json(checkpoint, confirmed)
            before = checkpoint.read_bytes()
            fetch.return_value = {"fingerprint": "fixture-last-good"}
            with patch.object(p, "bind_native_context"), patch.object(p, "tool_versions", return_value={}), \
                 patch.object(p, "credential_health", return_value={"warnings": []}), \
                 patch.object(p, "input_fingerprint", return_value="fixture-last-good"):
                with self.assertRaisesRegex(ValueError, "[Ss]table.*alias"):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=True))
            self.assertEqual(checkpoint.read_bytes(), before)
            records = [p.read_json(path) for path in (p.CACHE / "runs").glob("*/run.json")]
            self.assertTrue(all(record.get("status") != "no-change" for record in records))

    def test_stable_alias_is_verified_not_only_the_branch_preview(self):
        self.assertTrue(hasattr(p, "verify_stable_alias"), "stable development URL verification is missing")
        expected = {"fingerprint": "accepted-inputs", "sources": {"released": {"commit": "a" * 40}}}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "deployment.json").write_text(json.dumps({"stableDevelopmentUrl": "https://fixture.vercel.app"}))
            with patch.object(p, "CACHE", cache), patch.object(p, "get_json", return_value={"fingerprint": "old-inputs"}) as fetch:
                with self.assertRaisesRegex(ValueError, "stable|alias"):
                    p.verify_stable_alias(expected)
                fetch.assert_called_once_with("https://fixture.vercel.app/docs-manifest.json", same_origin=True)
            with patch.object(p, "CACHE", cache), patch.object(p, "get_json", return_value=expected):
                self.assertEqual(p.verify_stable_alias(expected), "https://fixture.vercel.app")

if __name__ == "__main__":
    unittest.main()
