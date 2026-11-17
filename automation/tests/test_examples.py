import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]

class ExampleRecipesTests(unittest.TestCase):
    def test_wallet_recipe_covers_shared_and_unsupported_rails_without_sending_money(self):
        path = ROOT / "automation/recipes/wallet.rs"
        self.assertTrue(path.exists(), "wallet onboarding recipe is missing")
        source = path.read_text()
        for required in ("PaymentList", "PaymentEndpointIdentifier", "assert!", "fixture", "no shared rail"):
            self.assertIn(required, source)
        self.assertNotIn("send_payment_proof(", source)

    def test_pos_recipe_validates_and_round_trips_a_request_without_payment(self):
        path = ROOT / "automation/recipes/point-of-sale.rs"
        self.assertTrue(path.exists(), "point-of-sale onboarding recipe is missing")
        source = path.read_text()
        for required in ("PaymentRequestTerms", "serialize_payment_request_event", "parse_payment_request_event_message", "is_valid()", "no payment was sent"):
            self.assertIn(required, source)
        self.assertNotIn("send_payment_request(", source)

    def test_versioned_examples_use_published_stable_and_exact_development_commit(self):
        import importlib.util
        import tempfile
        spec = importlib.util.spec_from_file_location("pipeline", ROOT / "automation/pipeline.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Frozen fixture is deliberately a later stable version, not today's release.
        lock = {"released": {"commit": "a" * 40, "packages": {"paykit-lib": {"version": "0.2.7"}}},
                "coming-soon": {"commit": "b" * 40}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            module.emit_examples(lock, output_root=output)
            stable = (output / "released/wallet/Cargo.toml").read_text()
            dev = (output / "coming-soon/wallet/Cargo.toml").read_text()
            self.assertIn('version = "=' + lock["released"]["packages"]["paykit-lib"]["version"] + '"', stable)
            self.assertNotIn("git =", stable)
            self.assertIn(lock["coming-soon"]["commit"], dev)
            self.assertNotIn('branch = "master"', dev)

if __name__ == "__main__":
    unittest.main()
