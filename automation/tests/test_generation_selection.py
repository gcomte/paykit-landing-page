"""Fixed synthetic public-repository fixtures; never follow a moving live ref."""
import contextlib
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("selection_pipeline", Path(__file__).parents[1] / "pipeline.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

SELECTOR = {"schemaVersion": 1, "source": {"repository": "pubky/paykit-rs", "ref": "v0.1.1-rc9", "kind": "tag"}, "defaultVersion": "coming-soon"}

class SelectedSourceTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()

    @contextlib.contextmanager
    def repository(self, selector=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream, repo = root / "public-upstream", root / ".automation/sources/paykit-rs"
            upstream.mkdir()
            self.git(upstream, "init", "-b", "master")
            self.git(upstream, "config", "user.email", "synthetic@example.invalid")
            self.git(upstream, "config", "user.name", "Synthetic fixture")
            package = upstream / "paykit-lib"
            package.mkdir()
            (package / "Cargo.toml").write_text('[package]\nname="paykit-lib"\nversion="0.1.0"\n')
            self.git(upstream, "add", ".")
            self.git(upstream, "commit", "-m", "synthetic stable")
            stable = self.git(upstream, "rev-parse", "HEAD")
            self.git(upstream, "tag", "v0.1.0")
            (upstream / "fixture").write_text("selected rc snapshot")
            self.git(upstream, "add", ".")
            self.git(upstream, "commit", "-m", "synthetic selected")
            selected = self.git(upstream, "rev-parse", "HEAD")
            self.git(upstream, "tag", "-a", "v0.1.1-rc9", "-m", "synthetic annotated tag")
            self.git(upstream, "tag", "lightweight")
            self.git(upstream, "branch", "follow-me")
            (upstream / "fixture").write_text("unrelated default branch snapshot")
            self.git(upstream, "commit", "-am", "synthetic default movement")
            repo.parent.mkdir(parents=True)
            self.git(root, "clone", str(upstream), str(repo))
            self.git(repo, "remote", "set-url", "origin", "https://github.com/pubky/paykit-rs.git")
            self.git(repo, "remote", "set-url", "--push", "origin", "DISABLED")
            cfg = copy.deepcopy(p.read_json(p.CONFIG))
            cfg["packages"] = ["paykit-lib"]
            p.save_json(root / "automation/config.json", cfg)
            p.save_json(root / "docs-generation.json", SELECTOR if selector is None else selector)
            calls = []
            real_run = p.run
            def git(args, **kwargs):
                calls.append(args[:])
                if args[:2] in (["git", "fetch"], ["git", "ls-remote"]):
                    args = [str(upstream) if item == "origin" else item for item in args]
                return real_run(args, **kwargs)
            def public(url, **kwargs):
                if url == "https://api.github.com/repos/pubky/paykit-rs":
                    return {"private": False, "full_name": "pubky/paykit-rs", "default_branch": "master"}
                if "/releases?" in url:
                    return [{"tag_name": "v0.1.0", "draft": False, "prerelease": False, "html_url": "https://github.com/pubky/paykit-rs/releases/tag/v0.1.0"}]
                if url.startswith("https://crates.io/"):
                    return {"version": {"num": "0.1.0", "crate": "paykit-lib", "yanked": False, "checksum": "c" * 64}}
                raise AssertionError("Unexpected public lookup " + url)
            with patch.object(p, "ROOT", root), patch.object(p, "CONFIG", root / "automation/config.json"), patch.object(p, "CACHE", root / ".automation"), patch.object(p, "REPO", repo), patch.object(p, "run", side_effect=git), patch.object(p, "get_json", side_effect=public), patch.object(p, "verify_published_package", return_value={"sourceCommit": stable, "verifiedRustFiles": 1}):
                yield root, upstream, repo, stable, selected, calls

    def test_selected_annotated_tag_not_default_branch_is_frozen(self):
        with self.repository() as (_, upstream, _, stable, selected, calls):
            lock = p.resolve_sources()
            self.assertEqual(lock["coming-soon"]["commit"], selected, "configured tag must win over unrelated master HEAD")
            self.assertEqual(lock["released"]["commit"], stable)
            self.assertEqual(lock["released"]["tag"], "v0.1.0")
            self.assertEqual(lock["selectedSource"], {**SELECTOR["source"], "qualifiedRef": "refs/tags/v0.1.1-rc9", "object": self.git(upstream, "rev-parse", "refs/tags/v0.1.1-rc9"), "commit": selected})
            self.assertEqual(lock["coming-soon"]["ref"], "v0.1.1-rc9")
            self.assertEqual(lock["coming-soon"]["kind"], "tag")
            self.assertEqual(lock["defaultVersion"], "coming-soon")
            self.assertFalse(any(args[-1:] == ["master"] and "fetch" in args for args in calls))

    def test_branch_and_lightweight_tags_resolve_exact_namespaces(self):
        for kind, ref in (("branch", "follow-me"), ("tag", "lightweight")):
            selector = copy.deepcopy(SELECTOR)
            selector["source"].update(kind=kind, ref=ref)
            with self.subTest(kind=kind), self.repository(selector) as (_, upstream, _, _, selected, _):
                # Same literal exists in both namespaces, at different commits.
                if kind == "branch":
                    self.git(upstream, "tag", ref, "master")
                lock = p.resolve_sources()
                self.assertEqual(lock["coming-soon"]["commit"], selected)
                self.assertEqual(lock["selectedSource"]["object"], selected)
                if kind == "branch":
                    self.git(upstream, "branch", "-f", ref, "master")
                    moved = p.resolve_sources()
                    self.assertEqual(moved["coming-soon"]["commit"], self.git(upstream, "rev-parse", "master"))
                    self.assertNotEqual(moved["coming-soon"]["commit"], selected)

    def test_missing_and_wrong_kind_refs_never_fall_back_to_master(self):
        for kind, ref in (("branch", "v0.1.1-rc9"), ("tag", "follow-me"), ("tag", "missing")):
            selector = copy.deepcopy(SELECTOR)
            selector["source"].update(kind=kind, ref=ref)
            with self.subTest(kind=kind, ref=ref), self.repository(selector):
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    p.resolve_sources()

    def test_malformed_selectors_fail_before_public_lookup(self):
        invalid = [None, {}, {**SELECTOR, "schemaVersion": True}, {**SELECTOR, "defaultVersion": "released"}]
        for field, values in {"repository": ["other/paykit-rs", "pubky/paykit-rs.git", None], "kind": ["commit", "Tag", None], "ref": ["", None, " refs/tags/v0.1.1-rc9", "refs/tags/v0.1.1-rc9", "v0.1.1-rc9\n", "-option", "bad..ref", "path/.hidden", "a.lock", "a//b", "tag^{commit}", "a:b", "a@{1}"]}.items():
            for value in values:
                candidate = copy.deepcopy(SELECTOR)
                candidate["source"][field] = value
                invalid.append(candidate)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(p, "ROOT", root), patch.object(p, "get_json", side_effect=AssertionError("invalid selector must not reach network")):
                for candidate in invalid:
                    with self.subTest(candidate=candidate):
                        p.save_json(root / "docs-generation.json", candidate)
                        with self.assertRaises((ValueError, FileNotFoundError)):
                            p.resolve_sources()
                (root / "docs-generation.json").unlink()
                with self.assertRaises(FileNotFoundError):
                    p.resolve_sources()

    def test_ref_movement_during_fetch_rejects_atomic_snapshot(self):
        with self.repository() as (_, upstream, _, _, _, _):
            real = p.run
            def mutate(args, **kwargs):
                result = real(args, **kwargs)
                if args[:2] == ["git", "fetch"] and "refs/tags/v0.1.1-rc9:" in args[-1]:
                    self.git(upstream, "tag", "-f", "v0.1.1-rc9", "master")
                return result
            with patch.object(p, "run", side_effect=mutate):
                with self.assertRaisesRegex(ValueError, "moved"):
                    p.resolve_sources()

class SelectionPolicyTests(unittest.TestCase):
    def test_durable_selector_trigger_and_rendered_metadata_review_policy(self):
        register = p.read_json(p.ROOT / "automation/corrections.json")
        rows = {row["id"]: row for row in register["requirements"]}
        for required in ("configurable-documentation-source", "precise-regeneration-triggers", "truthful-readiness-metadata", "development-preview-hosting"):
            self.assertIn(required, rows)
            self.assertIn("automation/tests/test_generation_selection.py", rows[required]["enforcedBy"])
        for name in ("automation/COORDINATOR.md", "automation/README.md", "automation/skills/coordinator.md", "automation/skills/sources.md", "automation/skills/writing.md", "automation/skills/evaluation.md"):
            text = (p.ROOT / name).read_text()
            self.assertIn("docs-generation.json", text)
            self.assertIn("selected", text.lower())
            if "coordinator" in name.lower() or "evaluation" in name.lower() or "README" in name:
                self.assertIn("21600", text)
        review = (p.ROOT / "automation/skills/evaluation.md").read_text()
        self.assertIn("deterministic metadata", review)
        self.assertIn("README heading", review)


class ReadinessProducerTests(unittest.TestCase):
    def test_explicit_warning_is_quoted_with_its_component_scope_at_matching_snapshot(self):
        text = "# Synthetic project\nWallet adapter is not for production; other components are unassessed.\n"
        with patch.object(p, "source_text", return_value=text):
            result = p.readiness_metadata("b" * 40, "coming-soon")
            self.assertIn(text.splitlines()[1], result["notice"], "do not broaden component wording into global software status")
            self.assertEqual(result["evidence"]["commit"], "b" * 40)
            self.assertEqual(result["evidence"]["startLine"], 2)
            self.assertEqual(result["evidence"]["excerpt"], text.splitlines()[1])
            self.assertEqual(result["attribution"], "upstream")

    def test_readme_heading_is_not_evidence_for_operator_readiness_caution(self):
        with patch.object(p, "source_text", return_value="# Synthetic project README\nNo readiness assessment here.\n"):
            metadata = p.readiness_metadata("a" * 40, "coming-soon")
            self.assertEqual(metadata["status"], "unassessed")
            self.assertIn("does not establish production readiness", metadata["notice"])
            self.assertIsNone(metadata.get("evidence"), "a README heading cannot support a readiness judgment")
            self.assertEqual(metadata["attribution"], "documentation-policy")


class RegenerationTests(unittest.TestCase):
    @contextlib.contextmanager
    def published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".automation"
            cfg = {"limits": {"runtimeSeconds": 3500}, "sections": {"Understand Paykit": ["overview", "components", "flows", "boundaries"], "Integrate Paykit": ["wallets", "point-of-sale"]}, "publishing": {"branch": "docs-automation", "deploymentTimeoutSeconds": 10}}
            p.save_json(root / "automation/config.json", cfg)
            p.save_json(root / "docs-generation.json", SELECTOR)
            (root / "automation/skills").mkdir()
            (root / "automation/skills/writing.md").write_text("synthetic author instructions")
            lock = {"schemaVersion": 1, "repository": "pubky/paykit-rs", "defaultVersion": "coming-soon", "selectedSource": {**SELECTOR["source"], "qualifiedRef": "refs/tags/v0.1.1-rc9", "object": "c" * 40, "commit": "b" * 40}, "released": {"commit": "a" * 40, "tag": "v0.1.0", "packages": {}}, "coming-soon": {"commit": "b" * 40, "packages": {}}}
            lock["coming-soon"].update({key: value for key, value in lock["selectedSource"].items() if key != "repository"})
            lock["coming-soon"].update(label=SELECTOR["source"]["ref"], tag=SELECTOR["source"]["ref"],
                                       sourceUrl="https://github.com/pubky/paykit-rs/tree/" + lock["coming-soon"]["commit"])
            p.save_json(root / "automation/sources.lock.json", lock)
            for version in ("released", "coming-soon"):
                p.save_json(root / f"automation/blueprints/{version}.json", {"evidence": [], "pages": [{"slug": slug, "sections": []} for slugs in cfg["sections"].values() for slug in slugs]})
            p.save_json(cache / "deployment.json", {"stableDevelopmentUrl": "https://synthetic-stable.vercel.app"})
            tools = {"node": "synthetic-1"}
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), patch.object(p, "CONFIG", root / "automation/config.json"), patch.object(p, "tool_versions", return_value=tools), patch.object(p, "credential_health", return_value={"warnings": []}), patch.object(p, "resolve_sources", return_value=lock) as resolver, patch.object(p, "get_json") as fetch, patch.object(p, "fork_api") as api:
                record = p.acquire_lease()
                record.update(acceptanceHash=p.acceptance_digest(), sourceHash=p.digest(lock), tools=tools)
                # Begin owns trigger calculation; this temporary first run is not a publication shortcut.
                if hasattr(p, "regeneration_fingerprint"):
                    record["regenerationFingerprint"] = p.regeneration_fingerprint(lock, tools=tools)
                    record["processFingerprint"] = p.process_fingerprint(tools=tools)
                p.save_json(cache / "run.lock/owner.json", record)
                p.save_json(cache / "runs" / record["id"] / "sources.json", lock)
                manifest = {"fingerprint": p.input_fingerprint(lock, tools=tools)}
                p.save_json(root / "public/docs-manifest.json", manifest)
                p.save_json(cache / "pending-publication.json", {"runId": record["id"], "sha": "d" * 40, "fingerprint": manifest["fingerprint"]})
                api.side_effect = lambda endpoint: ([{"id": 1, "environment": "Preview", "sha": "d" * 40}] if endpoint.startswith("deployments?") else [{"state": "success", "environment_url": "https://synthetic-preview.vercel.app"}] if endpoint.startswith("deployments/") else {"commit": {"sha": "d" * 40}})
                fetch.return_value = manifest
                with contextlib.redirect_stdout(__import__("io").StringIO()):
                    p._verify_publication(record["id"])
                self.assertTrue(p.read_json(cache / "last-success.json")["verifiedDeployment"])
                yield root, cache, lock, resolver, fetch

    def begin(self, force=False):
        out = __import__("io").StringIO()
        with contextlib.redirect_stdout(out):
            p.begin_run(p.argparse.Namespace(reuse_candidate=False, force=force))
        return json.loads(out.getvalue())

    def test_retry_plan_uses_confirmed_sources_not_failed_generated_lock(self):
        with self.published() as (root, _, lock, resolver, _):
            changed = copy.deepcopy(lock)
            changed["coming-soon"]["commit"] = changed["selectedSource"]["commit"] = "e" * 40
            changed["coming-soon"]["sourceUrl"] = "https://github.com/pubky/paykit-rs/tree/" + "e" * 40
            resolver.return_value = changed
            p.save_json(root / "automation/sources.lock.json", changed)  # a failed attempt's generated output
            real = p.run
            with patch.object(p, "run", side_effect=lambda args, **kwargs: "synthetic.rs" if args[:3] == ["git", "diff", "--name-only"] else real(args, **kwargs)):
                result = self.begin()
            self.assertEqual(result["changes"]["coming-soon"]["from"], "b" * 40)
            self.assertEqual(result["changes"]["coming-soon"]["changedSourceFiles"], ["synthetic.rs"])
            p.release_lease(result["runId"], "failed")

    def test_process_inputs_cannot_drift_inside_a_frozen_attempt(self):
        with self.published() as (root, _, _, _, _):
            result = self.begin(force=True)
            recipe = root / "automation/recipes/wallet.rs"
            recipe.parent.mkdir(parents=True)
            recipe.write_text("// changed after source/acceptance freeze")
            with self.assertRaisesRegex(ValueError, "[Gg]eneration process.*changed"):
                p.frozen_sources(result["runId"])
            p.release_lease(result["runId"], "failed")

    def test_each_process_input_byte_change_retries_until_verified_success(self):
        paths = ["docs-generation.json", "automation/skills/writing.md", "automation/pipeline.py", "automation/recipes/wallet.rs", "automation/tests/test_fixture.py", "components/docs/DocsPage.tsx", "components/docs/docs.module.css", "tests/docs-runner.test.mjs", "next.config.ts", "package-lock.json"]
        for name in paths:
            with self.subTest(path=name), self.published() as (root, cache, _, _, _):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((target.read_text() if target.exists() else "") + "\n")
                last_good = (cache / "last-success.json").read_bytes()
                first = self.begin()
                self.assertEqual(first["reasons"], ["generation-process"])
                self.assertTrue(all(item["fullAuthoringRequired"] for item in first["changes"].values()))
                p.release_lease(first["runId"], "failed", {"error": "synthetic rejected attempt"})
                retry = self.begin()
                self.assertEqual(retry["action"], "update")
                self.assertNotEqual(first["runId"], retry["runId"])
                self.assertEqual(last_good, (cache / "last-success.json").read_bytes())
                p.release_lease(retry["runId"], "failed")

    def test_tool_change_triggers_but_timestamps_and_ignored_state_do_not(self):
        with self.published() as (root, cache, _, _, _):
            __import__("os").utime(root / "automation/skills/writing.md", (1, 1))
            (cache / "unrelated-runtime-state.json").write_text("synthetic ignored runtime state")
            self.assertEqual(self.begin()["action"], "no-change")
            with patch.object(p, "tool_versions", return_value={"node": "synthetic-2"}):
                result = self.begin()
                self.assertEqual(result["reasons"], ["generation-process"])
                p.release_lease(result["runId"], "failed")

    def test_source_commit_trigger_preserves_full_frozen_binding_and_failed_retry(self):
        with self.published() as (_, cache, lock, resolver, _):
            changed = copy.deepcopy(lock)
            changed["coming-soon"]["commit"] = changed["selectedSource"]["commit"] = "e" * 40
            changed["coming-soon"]["sourceUrl"] = "https://github.com/pubky/paykit-rs/tree/" + "e" * 40
            resolver.return_value = changed
            real = p.run
            def git(args, **kwargs):
                if args[:3] == ["git", "diff", "--name-only"]:
                    self.assertEqual(args[-2:], ["b" * 40, "e" * 40])
                    return "synthetic-implementation.rs"
                return real(args, **kwargs)
            with patch.object(p, "run", side_effect=git):
                result = self.begin()
                self.assertEqual(result["reasons"], ["selected-source-commit"])
                self.assertTrue(result["changes"]["coming-soon"]["provenanceRefresh"])
                self.assertEqual(p.frozen_sources(result["runId"]), changed)
                path = cache / "runs" / result["runId"] / "sources.json"
                tampered = copy.deepcopy(changed)
                tampered["released"]["packages"] = {"other": {"published": True}}
                p.save_json(path, tampered)
                with self.assertRaisesRegex(ValueError, "Frozen source inputs changed"):
                    p.frozen_sources(result["runId"])
                p.release_lease(result["runId"], "failed")
                retry = self.begin()
                self.assertEqual(retry["reasons"], ["selected-source-commit"])
                p.release_lease(retry["runId"], "failed")

    def test_force_cannot_bypass_unavailable_source_or_review_gates(self):
        with self.published() as (_, cache, _, resolver, _):
            before = (cache / "last-success.json").read_bytes()
            resolver.side_effect = ValueError("synthetic selected source unavailable")
            with self.assertRaisesRegex(ValueError, "source unavailable"):
                self.begin(force=True)
            self.assertEqual(before, (cache / "last-success.json").read_bytes())
            self.assertFalse((cache / "run.lock").exists())
        with self.published() as (_, cache, _, _, _):
            result = self.begin(force=True)
            self.assertTrue(all(delta["fullAuthoringRequired"] for delta in result["changes"].values()))
            with self.assertRaises((FileNotFoundError, ValueError)):
                p.require_approvals("synthetic-unapproved", run_id=result["runId"])
            p.release_lease(result["runId"], "failed")

    def test_force_cli_enters_real_begin_path(self):
        with self.published() as (_, _, _, _, _):
            out = __import__("io").StringIO()
            with patch.object(p.sys, "argv", ["pipeline.py", "begin", "--force"]), contextlib.redirect_stdout(out):
                p.main()
            result = json.loads(out.getvalue())
            self.assertIn("explicit-force", result["reasons"])
            p.release_lease(result["runId"], "failed")

    def test_unverified_or_legacy_checkpoint_cannot_suppress_authoring(self):
        for mutation in ("unverified", "legacy", "missing"):
            with self.subTest(mutation=mutation), self.published() as (_, cache, _, _, _):
                checkpoint = p.read_json(cache / "last-success.json")
                if mutation == "missing":
                    (cache / "last-success.json").unlink()
                else:
                    if mutation == "unverified":
                        checkpoint["verifiedDeployment"] = False
                    else:
                        checkpoint.pop("regenerationFingerprint", None)
                    p.save_json(cache / "last-success.json", checkpoint)
                result = self.begin()
                self.assertEqual(result["action"], "update")
                self.assertIn("initial-or-legacy-checkpoint", result["reasons"])
                self.assertTrue(all(delta["fullAuthoringRequired"] for delta in result["changes"].values()))
                p.release_lease(result["runId"], "failed")

    def test_reused_candidate_must_match_literal_selector(self):
        with self.published() as (_, cache, lock, _, _):
            wrong = copy.deepcopy(lock)
            wrong["selectedSource"]["ref"] = "some-other-tag"
            p.save_json(cache / "candidate-sources.json", wrong)
            before = (cache / "last-success.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "[Ss]elect.*(match|differ)"):
                with contextlib.redirect_stdout(__import__("io").StringIO()):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=True, force=False))
            self.assertEqual(before, (cache / "last-success.json").read_bytes())
            self.assertFalse((cache / "run.lock").exists())

    def test_selector_is_frozen_in_acceptance_and_allowed_publication_scope(self):
        with self.published() as (root, cache, _, _, _):
            result = self.begin(force=True)
            config = root / "docs-generation.json"
            before = p.input_fingerprint(p.read_json(cache / "runs" / result["runId"] / "sources.json"), tools={})
            config.write_text(config.read_text() + "\n")
            self.assertNotEqual(before, p.input_fingerprint(p.read_json(cache / "runs" / result["runId"] / "sources.json"), tools={}))
            with self.assertRaisesRegex(ValueError, "Acceptance policy changed"):
                p.guard_run(result["runId"])
            p.release_lease(result["runId"], "failed")
            with patch.object(p, "run", return_value="docs-generation.json\0"):
                self.assertIsNone(p.enforce_publication_scope())
            with patch.object(p, "run", return_value="unrelated.txt\0"):
                with self.assertRaisesRegex(ValueError, "outside scope"):
                    p.enforce_publication_scope()

    def test_process_change_marks_all_pages_for_authoring_even_without_source_delta(self):
        with self.published() as (root, cache, _, _, _):
            (root / "automation/skills/writing.md").write_text("changed synthetic author instructions")
            result = self.begin()
            self.assertEqual(result["action"], "update")
            self.assertEqual(result["reasons"], ["generation-process"])
            for delta in result["changes"].values():
                self.assertEqual(set(delta["affectedPages"]), {"overview", "components", "flows", "boundaries", "wallets", "point-of-sale"})
                self.assertEqual(delta["changedSourceFiles"], [])
                self.assertTrue(delta["fullAuthoringRequired"])
            out = __import__("io").StringIO()
            with contextlib.redirect_stdout(out):
                p.dispatch_stage(p.argparse.Namespace(command="plan", run=result["runId"]))
            self.assertEqual(json.loads(out.getvalue()), result["changes"], "plan must retain attempt-local force/process authoring scope")
            p.release_lease(result["runId"], "failed")

    def test_explicit_force_starts_fresh_attempt_without_advancing_checkpoint(self):
        with self.published() as (_, cache, _, resolver, _):
            before = (cache / "last-success.json").read_bytes()
            result = self.begin(force=True)
            self.assertEqual(result["action"], "update", "force is attempt-local, not a change to input bytes")
            self.assertIn("explicit-force", result["reasons"])
            resolver.assert_called_once_with()
            owner = p.guard_run(result["runId"])
            self.assertEqual(owner["repairs"], 0)
            self.assertEqual(owner["deadline"] - owner["started"], 3500)
            self.assertEqual(before, (cache / "last-success.json").read_bytes())
            p.release_lease(result["runId"], "failed", {"error": "synthetic unapproved attempt"})
            self.assertEqual(self.begin()["action"], "no-change", "force must not persist into the next attempt")

    def test_unchanged_selected_commit_ignores_other_observations_after_verified_publication(self):
        with self.published() as (_, cache, lock, resolver, fetch):
            before = (cache / "last-success.json").read_bytes()
            changed = copy.deepcopy(lock)
            changed["released"].update(commit="e" * 40, tag="v0.2.0", packages={"synthetic": {"published": True}})
            changed["coming-soon"]["packages"] = {"synthetic": {"published": True}}
            changed["selectedSource"]["object"] = "f" * 40  # retag, same peeled commit
            changed["coming-soon"]["object"] = "f" * 40
            resolver.return_value = changed
            result = self.begin()
            self.assertEqual(result["action"], "no-change", "only selected commit/process may trigger generation")
            self.assertEqual(before, (cache / "last-success.json").read_bytes())
            self.assertFalse((cache / "run.lock").exists())


if __name__ == "__main__":
    unittest.main()
