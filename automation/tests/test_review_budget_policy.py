"""Prospective user-approved review budgeting policy, not runtime certification."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReviewBudgetPolicyTests(unittest.TestCase):
    def test_doubled_budget_requirement_is_persistent_and_prospective(self):
        register = json.loads((ROOT / "automation/corrections.json").read_text())
        requirements = {row["id"]: row for row in register["requirements"]}
        self.assertIn("six-hour-budgets-and-every-run-accounting", requirements)
        text = requirements["six-hour-budgets-and-every-run-accounting"]["requirement"]
        for fragment in ("21600", "14400", "3600", "10800", "5400", "1800", "prospective", "historical"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)


    def test_every_run_reporting_replaces_silent_no_change_in_active_instructions(self):
        paths = ("automation/COORDINATOR.md", "automation/README.md",
                 "automation/skills/coordinator.md", "automation/skills/evaluation.md")
        for name in paths:
            text = (ROOT / name).read_text()
            with self.subTest(path=name):
                self.assertNotIn("[SILENT]", text)
                self.assertIn("Budget accounting for every run", text)
                for word in ("no-change", "unknown", "wall-clock", "tooling", "access", "quality"):
                    self.assertIn(word, text)


    def test_approved_review_reference_export_and_playground_scope_is_recorded(self):
        register = json.loads((ROOT / "automation/corrections.json").read_text())
        requirements = {row["id"]: row for row in register["requirements"]}
        for identifier in ("expanded-independent-review-coverage", "generated-glossary-and-resources",
                           "every-page-markdown-and-reviewed-llm-exports", "sandboxed-doc-execution-playgrounds"):
            self.assertIn(identifier, set(requirements))
        exports = requirements["every-page-markdown-and-reviewed-llm-exports"]["requirement"]
        for text in ("homepage", "footer", "llms.txt", "Markdown", "reviewed"):
            self.assertIn(text, exports)
        sandbox = requirements["sandboxed-doc-execution-playgrounds"]["requirement"]
        self.assertIn("not", sandbox)
        self.assertIn("credentials", sandbox)
        self.assertIn("blocked", sandbox)


if __name__ == "__main__":
    unittest.main()
