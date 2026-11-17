"""Policy-only regression checks; not live runner or isolation qualification."""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
POLICY = ROOT / "automation" / "local-execution-policy.json"


class LocalExecutionPolicyTests(unittest.TestCase):
    def test_execution_stays_on_existing_server_without_hermes_sudo(self):
        self.assertTrue(POLICY.is_file(), "server-local execution policy is missing")
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(policy["schemaVersion"], 1)
        self.assertEqual(policy["executionHost"], "existing-user-controlled-always-on-server")
        self.assertFalse(policy["githubHostedExecutionAllowed"])
        self.assertEqual(policy["modelProviderPolicy"], "preserve-existing-provider-and-authentication")
        # USERCHANGEDCONTRACT: ordinary existing Hermes, not protected setup.
        self.assertEqual(policy["setupActor"], "existing-hermes-runtime")
        self.assertFalse(policy["grantHermesSudo"])
        self.assertFalse(policy["runModelOrBuildJobsAsRoot"])
        self.assertFalse(policy["runtimeApprovalGrantedByThisPolicy"])

    def test_instruction_boundary_runs_without_protected_runner_prerequisites(self):
        """The accepted policy selects instructions, not enforced isolation."""
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        expected = {
            "setupActor": "existing-hermes-runtime",
            "sourceBoundaryMode": "instruction-based-public-evidence-v1",
            "enforcedIsolationRequired": False,
            "requiresProtectedRunnerSetup": False,
            "runtimeApprovalGrantedByThisPolicy": False,
        }
        self.assertEqual({key: policy.get(key) for key in expected}, expected)

    def test_public_role_contract_requires_independent_claim_enumeration(self):
        """Instructions require support review without asserting isolation."""
        paths = (
            "COORDINATOR.md", "README.md", "SOURCE-BOUNDARY.md", "LOCAL-EXECUTION.md",
            "skills/coordinator.md", "skills/sources.md", "skills/writing.md",
            "skills/diagrams.md", "skills/evaluation.md",
        )
        texts = {path: (ROOT / "automation" / path).read_text(encoding="utf-8") for path in paths}
        for path, text in texts.items():
            with self.subTest(path=path):
                self.assertIn("instruction-based-public-evidence-v1", text)
                for retired_gate in (
                    "Before enabling unattended publication, verify actual author tool/context restrictions",
                    "Fresh empty CWD, disabled memory/SOUL/context/prefill",
                    "A separate administrator account performs reviewed protected-service setup",
                    "Core `begin` consumes a fresh consultation receipt",
                ):
                    self.assertNotIn(retired_gate, text)
        boundary = texts["SOURCE-BOUNDARY.md"]
        for obligation in ("not access isolation", "section-level citation", "occurrence location",
                           "Do not query Talos or private storage", "pretrained"):
            self.assertIn(obligation, boundary)
        review = texts["skills/evaluation.md"]
        for obligation in ("different agent", "independently enumerate", "unmapped",
                           "surrounding context", "artifactHash", "public evidence supports checked claims",
                           "never prove absence of private influence"):
            self.assertIn(obligation, review)

    def test_review_instructions_use_actual_identity_and_complete_inventory_attestation(self):
        evaluation = (ROOT / "automation/skills/evaluation.md").read_text(encoding="utf-8")
        for required in (
            "authorRunId", "publicEvidenceAudit", "checkedTargets", "checkedEvidenceIds",
            "unmappedClaims", "unsupportedClaims", "review-targets --run RUN_ID",
            "four distinct", "actual task/session", "not technical proof",
            "<version>:page:<slug>", "<version>:section:<slug>/<sectionId>",
            "<version>:diagram:<diagramId>", "<version>:<evidenceId>",
        ):
            self.assertIn(required, evaluation)
        for path in ("COORDINATOR.md", "README.md", "skills/coordinator.md"):
            with self.subTest(path=path):
                text = (ROOT / "automation" / path).read_text(encoding="utf-8")
                self.assertIn("review-targets --run RUN_ID", text)
                self.assertIn("authorRunId", text)
                self.assertIn("publicEvidenceAudit", text)

    def test_register_supersedes_isolation_without_dropping_prior_requirements(self):
        register = json.loads((ROOT / "automation" / "corrections.json").read_text(encoding="utf-8"))
        boundary = register["privateContext"]
        self.assertEqual(boundary.get("sourceBoundaryMode"), "instruction-based-public-evidence-v1")
        self.assertIs(boundary.get("enforcedIsolationRequired"), False)
        self.assertIs(boundary.get("requiresProtectedRunnerSetup"), False)
        self.assertTrue(boundary["talosConsultationRequired"])
        self.assertIn("supersedes", boundary["securityLimit"])
        requirements = {item["id"]: item for item in register["requirements"]}
        prior_ids = {
            "server-local-unprivileged-documentation-jobs", "mandatory-talos-public-source-boundary",
            "landing-docs-section", "generated-teaser-copy", "persistent-user-corrections",
            "existing-stable-development-alias", "temporary-preview-is-not-acceptance",
        }
        self.assertTrue(prior_ids <= requirements.keys())
        server = requirements["server-local-unprivileged-documentation-jobs"]
        self.assertIn("Supersedes", server["requirement"])
        self.assertIn("existing Hermes runtime", server["requirement"])
        self.assertNotIn("separate administrator account installs the protected runner", server["requirement"])
        correction = requirements.get("instruction-boundary-public-evidence-review")
        self.assertIsNotNone(correction, "durable superseding user correction is missing")
        for obligation in ("different agent", "independently enumerates", "occurrence", "public", "no proof"):
            self.assertIn(obligation, correction["requirement"])
        for path in ("automation/SOURCE-BOUNDARY.md", "automation/skills/evaluation.md",
                     "automation/tests/test_local_execution_policy.py"):
            self.assertIn(path, correction["enforcedBy"])
        self.assertEqual(register["landingDocs"], {
            "sectionId": "docs-teaser", "beforeSectionAriaLabelledby": "foundation-title",
            "beforeSectionHeading": "Your data. Your choice.", "mainHref": "/docs",
            "version": "default", "featuredSlugs": ["flows", "wallets", "point-of-sale"],
            "copySource": "generated selected page titles and descriptions", "responsiveWidths": [375, 768, 1440],
        })

    def test_correction_register_preserves_execution_and_administration_choice(self):
        register = json.loads((ROOT / "automation" / "corrections.json").read_text(encoding="utf-8"))
        requirements = {item["id"]: item for item in register["requirements"]}
        self.assertIn("server-local-unprivileged-documentation-jobs", requirements)
        requirement = requirements["server-local-unprivileged-documentation-jobs"]
        for path in (
            "automation/local-execution-policy.json",
            "automation/LOCAL-EXECUTION.md",
            "automation/tests/test_local_execution_policy.py",
        ):
            self.assertIn(path, requirement["enforcedBy"])
            self.assertTrue((ROOT / path).is_file(), path)
        coordinator = (ROOT / "automation" / "COORDINATOR.md").read_text(encoding="utf-8")
        # USERCHANGEDCONTRACT: protected-runner preparation is superseded.
        self.assertIn("Existing-runtime server-local execution", coordinator)
        self.assertIn("automation/local-execution-policy.json", coordinator)


if __name__ == "__main__":
    unittest.main()
