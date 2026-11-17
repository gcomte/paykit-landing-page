"""Offline regressions: isolated repositories, no publication or deployment."""
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("safety_pipeline", Path(__file__).parents[1] / "pipeline.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class SafetyTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()

    def repository(self, root):
        self.git(root, "init", "-b", "docs-automation")
        self.git(root, "config", "user.email", "fixture@example.invalid")
        self.git(root, "config", "user.name", "Fixture")
        (root / "README.md").write_text("base")
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "fixture base")
        return self.git(root, "rev-parse", "HEAD")

    def test_publish_rejects_prestaged_outside_scope_and_hidden_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base = self.repository(root)
            (root / "unrelated.txt").write_text("must not publish")
            self.git(root, "add", "unrelated.txt")
            with patch.object(p, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "scope"):
                    p.enforce_publication_scope(base)
                self.git(root, "commit", "-m", "unrelated fixture")
                (root / "unrelated.txt").unlink()
                self.git(root, "add", "-u")
                self.git(root, "commit", "-m", "conceal unrelated net diff")
                with self.assertRaisesRegex(ValueError, "scope"):
                    p.enforce_publication_scope(base)

    def test_reviewed_staging_includes_runner_config_and_rejects_omitted_inputs(self):
        self.assertIn("playwright.config.ts", p.PUBLISH_PATHS)
        self.assertTrue(hasattr(p, "stage_reviewed_candidate"), "reviewed-tree staging gate is missing")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base = self.repository(root)
            configuration = root / "playwright.config.ts"
            configuration.write_text("reuse old server")
            self.git(root, "add", "playwright.config.ts")
            self.git(root, "commit", "-m", "fixture tracked config")
            base = self.git(root, "rev-parse", "HEAD")
            configuration.write_text("always start tested build")
            (root / "tests").mkdir()
            (root / "tests/regression.mjs").write_text("require fresh server")
            with patch.object(p, "ROOT", root), patch.object(p, "PUBLISH_PATHS", ("playwright.config.ts", "tests")):
                tree = p.stage_reviewed_candidate(p.artifact_digest(), base)
                self.assertEqual(tree, self.git(root, "write-tree"))
                self.assertIn("playwright.config.ts", self.git(root, "diff", "--cached", "--name-only"))
                self.assertEqual(self.git(root, "diff", "--name-only"), "")
                (root / "omitted-input.py").write_text("reviewed but not publishable")
                with self.assertRaisesRegex(ValueError, "omitted|unstaged|untracked"):
                    p.stage_reviewed_candidate(p.artifact_digest(), base)

    def test_effective_push_urls_reject_extra_or_rewritten_upstream(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.repository(root)
            cache = root / ".automation"
            p.save_json(cache / "deployment.json", {"repository": "fixture-owner/paykit-landing-page"})
            p.save_json(root / "config.json", {"publishing": {"remote": "origin", "branch": "docs-automation"}})
            approved = "https://github.com/fixture-owner/paykit-landing-page.git"
            self.git(root, "remote", "add", "origin", approved)
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), patch.object(p, "CONFIG", root / "config.json"):
                self.assertEqual(p.approved_push_url(), approved)
                self.git(root, "config", "--add", "remote.origin.pushurl", approved)
                self.git(root, "config", "--add", "remote.origin.pushurl", "https://github.com/pubky/paykit-landing-page.git")
                with self.assertRaisesRegex(ValueError, "push|fork"):
                    p.approved_push_url()
                self.git(root, "config", "--unset-all", "remote.origin.pushurl")
                self.git(root, "config", "url.https://github.com/pubky/.pushInsteadOf", "https://github.com/fixture-owner/")
                with self.assertRaisesRegex(ValueError, "push|fork"):
                    p.approved_push_url()

    def test_fingerprint_tracks_blueprints_and_every_acceptance_test(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ("automation/blueprints/released.json", "tests/docs-diagrams.test.mjs", "tests/new-policy.test.mjs", "automation/capture-layout.mjs", "automation/new-evaluator.mjs", "automation/skills/evaluation.md", "automation/COORDINATOR.md"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("before")
                before = p.input_fingerprint({}, root, {"tool": "1"})
                path.write_text("after")
                self.assertNotEqual(before, p.input_fingerprint({}, root, {"tool": "1"}), name)
            self.assertNotEqual(p.input_fingerprint({}, root, {"tool": "1"}), p.input_fingerprint({}, root, {"tool": "2"}))

    def test_acceptance_policy_is_frozen_but_blueprint_repairs_remain_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            policy = root / "tests/docs-diagrams.test.mjs"
            policy.parent.mkdir(parents=True)
            policy.write_text("strict original")
            blueprint = root / "automation/blueprints/released.json"
            blueprint.parent.mkdir(parents=True)
            blueprint.write_text("author draft")
            with patch.object(p, "ROOT", root):
                record = p.acquire_lease(cache)
                record["acceptanceHash"] = p.acceptance_digest(root)
                p.save_json(cache / "run.lock/owner.json", record)
                blueprint.write_text("corrected evidence")
                p.guard_run(record["id"], cache)
                policy.write_text("changed mid-run")
                with self.assertRaisesRegex(ValueError, "Acceptance policy.*new run"):
                    p.guard_run(record["id"], cache)
                policy.write_text("strict original")
                with self.assertRaisesRegex(ValueError, "Acceptance policy.*new run"):
                    p.guard_run(record["id"], cache)

    def test_timeout_kills_descendants_not_only_direct_child(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            marker = root / "escaped"
            child = "import time; from pathlib import Path; time.sleep(.6); Path(" + repr(str(marker)) + ").write_text('escaped')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(5)"
            with self.assertRaises(subprocess.TimeoutExpired):
                p.run([sys.executable, "-c", parent], cwd=root, timeout=.15)
            time.sleep(.7)
            self.assertFalse(marker.exists(), "Timed-out descendant remained alive")

    def test_registration_write_failure_stops_real_owned_descendants(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            ready, marker = root / "child-ready", root / "effect-must-not-happen"
            child = ("import os,time; from pathlib import Path; Path(" + repr(str(ready)) +
                     ").write_text(str(os.getpid())); time.sleep(1); Path(" + repr(str(marker)) + ").write_text('escaped')")
            command = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," +
                       repr(child) + "],start_new_session=True); time.sleep(20)")
            record = p.acquire_lease(cache)
            processes = []
            real_popen, real_save = subprocess.Popen, p.save_json
            def observe(*args, **kwargs):
                proc = real_popen(*args, **kwargs)
                processes.append(proc)
                return proc
            def fail_registration(path, value):
                if Path(path) == cache / "run.lock/owner.json":
                    until = time.monotonic() + 3
                    while not ready.exists() and time.monotonic() < until:
                        time.sleep(.01)
                    if not ready.exists():
                        raise AssertionError("Real detached fixture child did not start")
                    raise OSError("fixture owner registration-write failure")
                return real_save(path, value)
            try:
                with patch.object(p.subprocess, "Popen", side_effect=observe), \
                     patch.object(p, "save_json", side_effect=fail_registration):
                    with self.assertRaisesRegex(OSError, "fixture owner registration-write failure"):
                        p.bounded_process([sys.executable, "-c", command], cwd=root,
                                          run_id=record["id"], cache=cache, timeout=10)
                child_pid = int(ready.read_text())
                alive_on_return = processes[0].poll() is None or p.group_alive(child_pid)
                registered = p.read_json(cache / "run.lock/owner.json").get("children", [])
                p.release_lease(record["id"], "failed", cache=cache)
                alive_after_release = processes[0].poll() is None or p.group_alive(child_pid)
                time.sleep(1.1)
                self.assertFalse(alive_on_return, "Registration failure returned with a real owned child alive")
                self.assertFalse(alive_after_release, "Failed lease release left an owned child alive")
                self.assertEqual(registered, [])
                self.assertFalse(marker.exists(), "Unregistered child performed a later effect")
            finally:
                # RED runs must clean up their real fixtures too.
                for proc in processes:
                    if p.group_alive(proc.pid):
                        p.stop_group(proc.pid)
                    proc.communicate(timeout=3)
                if ready.exists() and p.group_alive(int(ready.read_text())):
                    p.stop_group(int(ready.read_text()))
                if (cache / "run.lock/owner.json").exists():
                    p.release_lease(record["id"], "failed", cache=cache)

    def test_shared_lease_budget_bounds_process_and_reclaim_cleans_owned_group(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            record = p.acquire_lease(cache, duration=.2)
            started = time.monotonic()
            with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                p.bounded_process([sys.executable, "-c", "import time; time.sleep(5)"], timeout=10, run_id=record["id"], cache=cache)
            self.assertLess(time.monotonic() - started, 2)
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True)
            try:
                record = p.read_json(cache / "run.lock/owner.json")
                record["children"] = [{"pid": proc.pid, "start": p.process_identity(proc.pid)}]
                p.save_json(cache / "run.lock/owner.json", record)
                second = p.acquire_lease(cache)
                proc.wait(timeout=2)
                self.assertNotEqual(record["id"], second["id"])
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()

    def test_expired_finalization_preserves_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            record = p.acquire_lease(cache, now=100, duration=1)
            checkpoint = cache / "last-success.json"
            p.save_json(checkpoint, {"fingerprint": "previous-success"})
            before = checkpoint.read_bytes()
            with self.assertRaisesRegex(ValueError, "expired"):
                p.finalize_success(record["id"], {"fingerprint": "must-not-advance"}, cache)
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertTrue((cache / "run.lock/owner.json").exists())

    def test_failed_stages_require_accounted_repair_and_stop_after_two(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            record = p.acquire_lease(cache)
            calls = []
            def fail():
                calls.append("attempt")
                raise ValueError("fixture validation failed")
            for attempt in range(3):
                with self.assertRaisesRegex(ValueError, "fixture validation failed"):
                    p.execute_stage(record["id"], "validate", fail, cache)
                self.assertTrue(p.read_json(cache / "run.lock/owner.json")["needsRepair"])
                with self.assertRaisesRegex(ValueError, "repair"):
                    p.execute_stage(record["id"], "generate", fail, cache)
                self.assertEqual(len(calls), attempt + 1)
                if attempt < 2:
                    p.request_repair(record["id"], "accounted fix", cache)
            with self.assertRaisesRegex(ValueError, "repair limit"):
                p.request_repair(record["id"], "forbidden third", cache)
            self.assertEqual(len(p.read_json(cache / "runs" / record["id"] / "failures.json")), 3)
            self.assertFalse((cache / "last-success.json").exists())

    def test_validation_rejects_artifact_mutation_by_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            run_dir = cache / "runs/fixture"
            run_dir.mkdir(parents=True)
            artifact = root / "artifact.txt"
            artifact.write_text("before checks")
            p.save_json(cache / "browser-results.json", {"stats": {"fixture": True}})
            def command(args, **kwargs):
                artifact.write_text("mutated by a check")
                return subprocess.CompletedProcess(args, 0, "fixture stdout", "")
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), \
                 patch.object(p, "frozen_sources", return_value={}), patch.object(p, "validate_generated", return_value=True), \
                 patch.object(p, "test_examples", return_value=[]), patch.object(p, "guard_run", return_value={"deadline": time.time() + 60}), \
                 patch.object(p, "record_stage"), patch.object(p, "input_fingerprint", return_value="fixture"), \
                 patch.object(p, "artifact_digest", side_effect=lambda: p.digest(artifact.read_bytes())), patch.object(p, "bounded_process", side_effect=command):
                with self.assertRaisesRegex(ValueError, "[Aa]rtifact.*changed|mutat"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        p.validate_all("fixture")
            self.assertFalse((cache / "validation.json").exists())

    def test_timeout_stops_detached_children_as_used_by_browser_tools(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            marker = root / "escaped-session"
            child = "import time; from pathlib import Path; time.sleep(.6); Path(" + repr(str(marker)) + ").write_text('escaped')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "],start_new_session=True); time.sleep(5)"
            with self.assertRaises(subprocess.TimeoutExpired):
                p.run([sys.executable, "-c", parent], cwd=root, timeout=.15)
            time.sleep(.7)
            self.assertFalse(marker.exists(), "Detached command descendant escaped deadline")

    def test_network_read_respects_shared_lease_deadline(self):
        import http.server
        import threading
        class Slow(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(.6)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{}')
                except BrokenPipeError:
                    pass
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as d, patch.object(p, "CACHE", Path(d)):
                record = p.acquire_lease(Path(d), duration=.18)
                start = time.monotonic()
                with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                    with p.run_scope(record["id"]):
                        p.get_json("http://127.0.0.1:" + str(server.server_port))
                self.assertLess(time.monotonic() - start, .5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_walkthrough_requires_identity_guide_commands_and_real_archive_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            review = {"status": "pass", "artifactHash": "fixture", "findings": [], "runId": "run", "reviewerRunId": "independent-review"}
            p.save_json(cache / "review.json", review)
            rows = []
            for v in ("released", "coming-soon"):
                for a in ("wallet", "point-of-sale"):
                    archive = root / f"public/docs-examples/{v}/paykit-{a}-{v}.tar.gz"
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    archive.write_bytes((v + a).encode())
                    rows.append({"version": v, "audience": a, "exitCode": 0, "artifactHash": "fixture", "stdout": p.EXPECTED[a]})
            p.save_json(cache / "walkthroughs.json", rows)
            # This retained fixture exercises the legacy report data contract.
            # Active-mode audit/identity AND all old content gates are exercised
            # together in test_public_review_audit.py with valid new reports.
            legacy_config = root / "legacy-config.json"
            p.save_json(legacy_config, {"privacy": {"requireNativeCoordinator": False}})
            with patch.object(p, "ROOT", root), patch.object(p, "CONFIG", legacy_config):
                with self.assertRaisesRegex(ValueError, "identity|guide|commands|archive"):
                    p.require_approvals("fixture", cache, run_id="run")
                for row in rows:
                    v, a = row["version"], row["audience"]
                    row.update(runId="run", reporterRunId="fresh-" + v + a,
                               guideUrl=f"http://127.0.0.1:3000/docs/{v}/" + ("wallets" if a == "wallet" else a),
                               commands=["curl -fLO archive.tar.gz", "tar xzf archive.tar.gz", "cargo run --locked"],
                               archiveHash=p.digest((root / f"public/docs-examples/{v}/paykit-{a}-{v}.tar.gz").read_bytes()))
                p.save_json(cache / "walkthroughs.json", rows)
                p.require_approvals("fixture", cache, run_id="run")
                rows[0]["archiveHash"] = "0" * 64
                p.save_json(cache / "walkthroughs.json", rows)
                with self.assertRaisesRegex(ValueError, "archive"):
                    p.require_approvals("fixture", cache, run_id="run")

    def test_validation_examples_do_not_emit_or_rewrite_archives(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for v in ("released", "coming-soon"):
                for a in ("wallet", "point-of-sale"):
                    directory = root / f"public/docs-examples/{v}/{a}"
                    directory.mkdir(parents=True)
                    (directory / "Cargo.lock").write_text("frozen lock fixture")
                    (directory.parent / f"paykit-{a}-{v}.tar.gz").write_bytes(b"frozen archive fixture")
            before = {str(path): path.read_bytes() for path in (root / "public").rglob("*") if path.is_file()}
            def cargo(args, cwd, **kwargs):
                environment=kwargs['env']
                self.assertFalse(any(name in environment for name in ('GITHUB_TOKEN','TALOS_TOKEN','OPENAI_API_KEY')), 'Production credentials reached an example subprocess')
                self.assertTrue(environment.get('HOME','').startswith(str(root/'.automation')), 'Examples must use an isolated public HOME')
                return subprocess.CompletedProcess(args, 0, p.EXPECTED[cwd.name], "")
            with patch.dict(p.os.environ, {'GITHUB_TOKEN':'SYNTHETIC_GH_FIXTURE','TALOS_TOKEN':'SYNTHETIC_TALOS_FIXTURE','OPENAI_API_KEY':'SYNTHETIC_PROVIDER_FIXTURE'}), \
                 patch.object(p, "ROOT", root), patch.object(p, "CACHE", root / ".automation"), \
                 patch.object(p, "emit_examples", side_effect=AssertionError("Validation must not emit")), \
                 patch.object(p, "bounded_process", side_effect=cargo):
                with contextlib.redirect_stdout(io.StringIO()):
                    p.test_examples({v: {"commit": v} for v in ("released", "coming-soon")}, write_artifacts=False)
            after = {str(path): path.read_bytes() for path in (root / "public").rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_vercel_fallback_requires_exact_sha_success_and_checkrun_preview_host(self):
        sha = "a" * 40
        def api(endpoint):
            if endpoint.endswith("/status"):
                return {"sha": sha, "statuses": [{"context": "Vercel", "state": "success", "target_url": "https://vercel.com/fixture/project/build-id"}]}
            return {"check_runs": [{"name": "Vercel Preview Comments", "head_sha": sha, "app": {"slug": "vercel"},
                "output": {"summary": "[Feedback](https://vercel.live/open-feedback/fixture-preview.vercel.app?via=pr-comment-feedback-link)"}}]}
        with patch.object(p, "fork_api", side_effect=api):
            self.assertEqual(p.vercel_fallback_url(sha), "https://fixture-preview.vercel.app")
            self.assertIsNone(p.vercel_fallback_url("b" * 40))

    def test_preview_login_html_is_account_blocker_not_provenance(self):
        import http.server
        import threading
        class Login(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b'<html>Log in to Vercel</html>')
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Login)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(ValueError, "[Aa]ccount action|protected"):
                p.get_json(f"http://127.0.0.1:{server.server_port}/docs-manifest.json", same_origin=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_publication_rejects_validation_from_different_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.repository(root)
            cache = root / ".automation"
            p.save_json(cache / "deployment.json", {"stableDevelopmentUrl": "https://fixture.vercel.app"})
            p.save_json(cache / "validation.json", {"runId": "old", "artifactHash": "same", "fingerprint": "same"})
            p.save_json(root / "config.json", {"publishing": {"branch": "docs-automation"}})
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), patch.object(p, "CONFIG", root / "config.json"), \
                 patch.object(p, "frozen_sources", return_value={}), patch.object(p, "validate_generated"), \
                 patch.object(p, "guard_run", return_value={"id": "new", "deadline": time.time() + 10, "repairs": 0, "acceptanceHash": "policy"}), \
                 patch.object(p, "artifact_digest", return_value="same"), patch.object(p, "input_fingerprint", return_value="same"), \
                 patch.object(p, "require_approvals", side_effect=AssertionError("Stale validation reached approvals")):
                with self.assertRaisesRegex(ValueError, "Validation"):
                    p.publish("new")

    def test_tool_version_change_requires_new_run_before_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache):
                record = p.acquire_lease(cache)
                lock = {"frozen": "sources"}
                record.update(acceptanceHash=p.acceptance_digest(root), sourceHash=p.digest(lock), tools={"node": "old"})
                p.save_json(cache / "run.lock/owner.json", record)
                p.save_json(cache / "runs" / record["id"] / "sources.json", lock)
                with patch.object(p, "tool_versions", return_value={"node": "changed"}):
                    with self.assertRaisesRegex(ValueError, "[Aa]cceptance.*new run"):
                        p.frozen_sources(record["id"])

    def test_expired_public_write_cannot_overlap_reclaimed_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            with patch.object(p, "CACHE", cache):
                old = p.acquire_lease(cache, now=100, duration=1)
                new = p.acquire_lease(cache)
                with patch.object(p, "ACTIVE_RUN", old["id"]):
                    with self.assertRaisesRegex(ValueError, "owner|expired"):
                        p.write_artifact(root / "public/must-not-write", b"old worker")
                self.assertFalse((root / "public/must-not-write").exists())
                self.assertEqual(p.guard_run(new["id"], cache)["id"], new["id"])

    def test_no_change_run_preserves_wording_and_skips_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            p.save_json(cache / "deployment.json", {"stableDevelopmentUrl": "https://fixture.vercel.app"})
            cfg = root / "automation/config.json"
            p.save_json(cfg, {"limits": {"runtimeSeconds": 10}})
            blueprint = root / "automation/blueprints/released.json"
            blueprint.parent.mkdir(parents=True)
            blueprint.write_text('"unchanged wording"')
            lock = {"fixture": "same source pair"}
            # User-authorized selector/verified-checkpoint schema migration only.
            selector = {"schemaVersion": 1, "source": {"repository": "pubky/paykit-rs", "kind": "tag", "ref": "synthetic-tag"}, "defaultVersion": "coming-soon"}
            p.save_json(root / "docs-generation.json", selector)
            lock.update(repository="pubky/paykit-rs", defaultVersion="coming-soon", **{"coming-soon": {"commit": "a" * 40},
                        "selectedSource": {**selector["source"], "qualifiedRef": "refs/tags/synthetic-tag", "object": "b" * 40, "commit": "a" * 40}})
            lock["coming-soon"].update({k: v for k, v in lock["selectedSource"].items() if k != "repository"})
            lock["coming-soon"].update(tag="synthetic-tag", label="synthetic-tag", sourceUrl="https://github.com/pubky/paykit-rs/tree/" + "a" * 40)
            p.save_json(cache / "candidate-sources.json", lock)
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), patch.object(p, "CONFIG", cfg), \
                 patch.object(p, "tool_versions", return_value={"fixture": "1"}), \
                 patch.object(p, "credential_health", return_value={"warnings": []}), \
                 patch.object(p, "get_json", return_value={"fingerprint": p.input_fingerprint(lock, tools={"fixture": "1"})}), \
                 patch.object(p, "generate_docs", side_effect=AssertionError("No-change must not generate")):
                fingerprint = p.input_fingerprint(lock, tools={"fixture": "1"})
                p.save_json(cache / "last-success.json", {"fingerprint": fingerprint, "deploymentUrl": "https://fixture.vercel.app",
                            "verifiedDeployment": True, "regenerationFingerprint": p.regeneration_fingerprint(lock, tools={"fixture": "1"})})
                before = (blueprint.read_bytes(), blueprint.stat().st_mtime_ns)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    p.begin_run(p.argparse.Namespace(reuse_candidate=True))
                self.assertEqual(json.loads(output.getvalue())["action"], "no-change")
                self.assertEqual(before, (blueprint.read_bytes(), blueprint.stat().st_mtime_ns))
                self.assertFalse((cache / "run.lock").exists())

    def test_expiry_during_final_branch_readback_keeps_success_unadvanced(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / ".automation"
            p.save_json(cache / "deployment.json", {"stableDevelopmentUrl": "https://fixture.vercel.app"})
            cfg = root / "automation/config.json"
            p.save_json(cfg, {"publishing": {"branch": "docs-automation", "deploymentTimeoutSeconds": 10}})
            lock = {"fixture": "frozen sources"}
            manifest = {"fingerprint": "fixture provenance"}
            p.save_json(root / "public/docs-manifest.json", manifest)
            with patch.object(p, "ROOT", root), patch.object(p, "CACHE", cache), patch.object(p, "CONFIG", cfg):
                record = p.acquire_lease(cache)
                record.update(acceptanceHash=p.acceptance_digest(root), sourceHash=p.digest(lock))
                p.save_json(cache / "run.lock/owner.json", record)
                p.save_json(cache / "runs" / record["id"] / "sources.json", lock)
                p.save_json(cache / "pending-publication.json", {"runId": record["id"], "sha": "a" * 40, "fingerprint": manifest["fingerprint"]})
                p.save_json(cache / "last-success.json", {"fingerprint": "old confirmed deployment"})
                before = (cache / "last-success.json").read_bytes()
                def api(endpoint):
                    if endpoint.startswith("deployments?"):
                        return [{"id": 1, "sha": "a" * 40, "environment": "Preview"}]
                    if endpoint.startswith("deployments/"):
                        return [{"state": "success", "environment_url": "https://fixture.vercel.app"}]
                    current = p.read_json(cache / "run.lock/owner.json")
                    current["deadline"] = time.time() - 1
                    p.save_json(cache / "run.lock/owner.json", current)
                    return {"commit": {"sha": "a" * 40}}
                with patch.object(p, "fork_api", side_effect=api), patch.object(p, "get_json", return_value=manifest):
                    with self.assertRaisesRegex(ValueError, "expired"):
                        p.verify_publication(record["id"])
                self.assertEqual(before, (cache / "last-success.json").read_bytes())

    def test_validate_failure_is_automatically_accounted_at_public_entrypoint(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            with patch.object(p, "CACHE", cache):
                record = p.acquire_lease(cache)
                with patch.object(p, "frozen_sources", return_value={}), \
                     patch.object(p, "validate_generated", side_effect=ValueError("fixture invalid generated output")):
                    with self.assertRaisesRegex(ValueError, "fixture invalid"):
                        p.validate_all(record["id"])
                self.assertTrue(p.read_json(cache / "run.lock/owner.json").get("needsRepair"))

    def test_publication_entrypoints_activate_shared_lease_budget(self):
        def frozen(run_id):
            self.assertEqual(p.ACTIVE_RUN, run_id, "Public entrypoint omitted bounded lease context")
            raise ValueError("fixture stop before any publication")
        with patch.object(p, "guard_run", return_value={"deadline": time.time() + 10}), \
             patch.object(p, "require_native_context", side_effect=frozen), \
             patch.object(p, "frozen_sources", side_effect=frozen):
            for entry in (p.publish, p.verify_publication):
                with self.subTest(entry=entry.__name__):
                    with self.assertRaisesRegex(ValueError, "fixture stop"):
                        entry("fixture-run")

    def test_expiry_while_preparing_finalization_never_records_published(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            record = p.acquire_lease(cache)
            p.save_json(cache / "last-success.json", {"fingerprint": "previous"})
            before = (cache / "last-success.json").read_bytes()
            save = p.save_json
            def expire_during_save(path, value):
                save(path, value)
                if Path(path) == cache / "runs" / record["id"] / "run.json":
                    owner = p.read_json(cache / "run.lock/owner.json")
                    owner["deadline"] = time.time() - 1
                    save(cache / "run.lock/owner.json", owner)
            with patch.object(p, "save_json", side_effect=expire_during_save):
                with self.assertRaisesRegex(ValueError, "expired"):
                    p.finalize_success(record["id"], {"fingerprint": "attempted"}, cache)
            self.assertEqual(before, (cache / "last-success.json").read_bytes())
            self.assertNotEqual(p.read_json(cache / "runs" / record["id"] / "run.json").get("status"), "published")

if __name__ == "__main__":
    unittest.main()
