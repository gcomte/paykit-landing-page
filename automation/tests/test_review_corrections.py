"""CR-1/CR-2 fixed synthetic regressions; no generation/publication approval."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


selection = load("correction_selection", "test_generation_selection.py")
audit = load("correction_audit", "test_public_review_audit.py")
p = selection.p


def identity(kind="tag", ref="v0.1.1-rc9", annotated=True):
    selected = {"repository": "pubky/paykit-rs", "kind": kind, "ref": ref,
                "qualifiedRef": "refs/" + ("tags/" if kind == "tag" else "heads/") + ref,
                "object": "c" * 40 if annotated else "b" * 40, "commit": "b" * 40}
    displayed = {key: value for key, value in selected.items() if key != "repository"}
    displayed.update(label=ref, sourceUrl="https://github.com/pubky/paykit-rs/tree/" + selected["commit"], packages={})
    displayed["tag" if kind == "tag" else "branch"] = ref
    return {"repository": selected["repository"], "defaultVersion": "coming-soon",
            "selectedSource": selected, "coming-soon": displayed}


class SelectionCorrections(unittest.TestCase):
    def validate(self, lock):
        cfg = copy.deepcopy(selection.SELECTOR)
        cfg["source"] = {key: lock["selectedSource"][key] for key in ("repository", "ref", "kind")}
        with patch.object(p, "generation_config", return_value=cfg):
            return p.validate_source_selection(lock)

    def test_each_displayed_identity_field_mismatch_is_rejected(self):
        baseline = identity()
        self.assertIs(self.validate(baseline), baseline)
        for key in ("commit", "sourceUrl", "ref", "kind", "qualifiedRef", "object", "label", "tag"):
            for value in (None, "master", "", [], {}):
                with self.subTest(key=key, value=value):
                    candidate = copy.deepcopy(baseline)
                    candidate["coming-soon"][key] = value
                    with self.assertRaises(ValueError):
                        self.validate(candidate)
            with self.subTest(missing=key):
                candidate = copy.deepcopy(baseline)
                del candidate["coming-soon"][key]
                with self.assertRaises(ValueError):
                    self.validate(candidate)

    def test_branch_annotated_and_lightweight_tag_controls_preserve_identity(self):
        for kind, ref, annotated in (("branch", "feature/exact", False), ("tag", "v0.1.1-rc9", True), ("tag", "lightweight", False)):
            lock = identity(kind, ref, annotated)
            original = copy.deepcopy(lock)
            self.assertIs(self.validate(lock), lock)
            with patch.object(p, "readiness_metadata", return_value={"status": "unassessed"}):
                document = p.complete_document({"pages": []}, lock, "coming-soon")
            for key, value in lock["coming-soon"].items():
                self.assertEqual(document["version"][key], value)
            self.assertEqual(lock, original)
            opposite = "branch" if kind == "tag" else "tag"
            for value in (ref, "master", None):
                bad = copy.deepcopy(lock)
                bad["coming-soon"][opposite] = value
                with self.assertRaises(ValueError):
                    self.validate(bad)

    def test_absent_or_malformed_snapshots_and_extra_repository_reject(self):
        for key in ("selectedSource", "coming-soon"):
            for value in (None, [], "bad", {}):
                bad = identity()
                bad[key] = value
                with patch.object(p, "generation_config", return_value=selection.SELECTOR):
                    with self.assertRaises(ValueError):
                        p.validate_source_selection(bad)
        bad = identity()
        bad["coming-soon"]["repository"] = "other/paykit-rs"
        with self.assertRaises(ValueError):
            self.validate(bad)

    def test_force_reuse_cannot_admit_conflicting_display_or_change_checkpoint(self):
        lab = selection.RegenerationTests(methodName="runTest")
        self.addCleanup(lab.doCleanups)
        with lab.published() as (_, cache, lock, _, _):
            wrong = copy.deepcopy(lock)
            wrong["coming-soon"].update(identity()["coming-soon"])
            wrong["coming-soon"].update(ref="master", kind="branch", branch="master", label="master")
            p.save_json(cache / "candidate-sources.json", wrong)
            before = (cache / "last-success.json").read_bytes()
            with self.assertRaises(ValueError), contextlib.redirect_stdout(io.StringIO()):
                p.begin_run(p.argparse.Namespace(reuse_candidate=True, force=True))
            self.assertFalse((cache / "run.lock").exists())
            self.assertEqual(before, (cache / "last-success.json").read_bytes())


class RawEvidenceCorrections(unittest.TestCase):
    def setUp(self):
        self.lab = audit.PublicReviewAuditTests(methodName="runTest")
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)
        self.p = self.lab.p
        for version in ("released", "coming-soon"):
            for audience in ("wallet", "point-of-sale"):
                directory = self.lab.root / f"public/docs-examples/{version}/{audience}"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "Cargo.lock").write_text("# Synthetic lock; no Cargo.\n")

    def exercise(self, stdout, stderr, timeout=False, code=0):
        real = self.p.bounded_process
        procs = []
        real_popen = subprocess.Popen
        def observe(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            procs.append(proc)
            return proc
        def capture(args, **kwargs):
            kwargs.update(cwd=self.lab.root, timeout=.25 if timeout else 5)
            kwargs.pop("env", None)
            program = f"import os,time;os.write(1,{stdout!r});os.write(2,{stderr!r});" + ("time.sleep(3)" if timeout else f"raise SystemExit({code})")
            return real([sys.executable, "-c", program], **kwargs)
        caught = None
        with patch.object(self.p, "bounded_process", side_effect=capture), patch.object(self.p.subprocess, "Popen", side_effect=observe), contextlib.redirect_stdout(io.StringIO()):
            try:
                self.p.test_examples(self.lab.lock, write_artifacts=False)
            except Exception as error:
                caught = error
        stem = self.lab.cache / "example-output/released-wallet"
        self.assertTrue(stem.with_suffix(".stdout").exists(), "captured raw stdout erased")
        self.assertTrue(stem.with_suffix(".stderr").exists(), "captured raw stderr erased")
        self.assertEqual(stem.with_suffix(".stdout").read_bytes(), stdout)
        self.assertEqual(stem.with_suffix(".stderr").read_bytes(), stderr)
        rows = self.p.read_json(self.lab.cache / "example-results.json")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "fail")
        for proc in procs:
            self.assertIsNotNone(proc.poll())
            self.assertFalse(self.p.group_alive(proc.pid))
        return caught, rows[0]

    def test_invalid_utf8_stderr_retains_both_raw_streams(self):
        caught, row = self.exercise(self.p.EXPECTED["wallet"].encode() + b"\n", b"bad\xff\r\n")
        self.assertIsInstance(caught, UnicodeDecodeError)
        self.assertEqual(row["errorType"], "UnicodeDecodeError")
        self.assertTrue(row["decodeError"])

    def test_invalid_utf8_stdout_retains_both_raw_streams(self):
        caught, row = self.exercise(b"bad\xff\r\n", b"diagnostic\r")
        self.assertIsInstance(caught, UnicodeDecodeError)
        self.assertTrue(row["decodeError"])

    def test_timeout_retains_actual_partial_output_and_cleanup(self):
        caught, row = self.exercise(b"partial\r\n", b"failure\r", timeout=True)
        self.assertIsInstance(caught, subprocess.TimeoutExpired)
        self.assertEqual(caught.stdout, b"partial\r\n")
        self.assertEqual(caught.stderr, b"failure\r")
        self.assertTrue(row["timedOut"])
        self.assertEqual(row["errorType"], "TimeoutExpired")

    def test_nonzero_and_crlf_remain_failures_with_raw_evidence(self):
        for raw, code in ((self.p.EXPECTED["wallet"].encode() + b"\n", 7), (self.p.EXPECTED["wallet"].encode() + b"\r\n", 0)):
            caught, row = self.exercise(raw, b"stderr\r", code=code)
            self.assertIsInstance(caught, ValueError)
            self.assertEqual(row["exitCode"], code)

    def test_real_success_optional_lf_is_unchanged(self):
        real = self.p.bounded_process
        for terminator in (b"", b"\n"):
            def capture(args, **kwargs):
                raw = self.p.EXPECTED[kwargs["cwd"].name].encode() + terminator
                kwargs.update(cwd=self.lab.root, timeout=5)
                kwargs.pop("env", None)
                return real([sys.executable, "-c", f"import os;os.write(1,{raw!r});os.write(2,b'diagnostic\\r')"], **kwargs)
            with patch.object(self.p, "bounded_process", side_effect=capture), contextlib.redirect_stdout(io.StringIO()):
                rows = self.p.test_examples(self.lab.lock, write_artifacts=False)
            self.assertEqual(len(rows), 4)
            for row in rows:
                self.assertEqual(row["stdout"], self.p.EXPECTED[row["audience"]] + terminator.decode())
                stem = self.lab.cache / "example-output" / f"{row['version']}-{row['audience']}"
                self.assertEqual(stem.with_suffix(".stdout").read_bytes(), row["stdout"].encode())

    def test_global_expiry_forbids_late_evidence_and_cleans_owned_child(self):
        record = self.p.read_json(self.lab.cache / "run.lock/owner.json")
        record["deadline"] = self.p.time.time() + .2
        self.p.save_json(self.lab.cache / "run.lock/owner.json", record)
        real = self.p.bounded_process
        def capture(args, **kwargs):
            kwargs.update(cwd=self.lab.root)
            kwargs.pop("env", None)
            return real([sys.executable, "-c", "import os,time;os.write(1,b'partial\\r\\n');os.write(2,b'failure\\r');time.sleep(3)"], **kwargs)
        with patch.object(self.p, "bounded_process", side_effect=capture), self.p.run_scope(record["id"]):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.p.test_examples(self.lab.lock, write_artifacts=False)
        self.assertFalse((self.lab.cache / "example-output").exists())
        self.assertFalse((self.lab.cache / "example-results.json").exists())
        self.assertEqual(self.p.read_json(self.lab.cache / "run.lock/owner.json").get("children"), [])


if __name__ == "__main__":
    unittest.main()
