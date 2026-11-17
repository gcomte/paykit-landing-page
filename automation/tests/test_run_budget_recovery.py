"""Recovery/compatibility regressions for real budget observations."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('budget_recovery_pipeline', Path(__file__).parents[1] / 'pipeline.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
CONFIG = Path(__file__).parents[1] / 'config.json'


class RunBudgetRecoveryTests(unittest.TestCase):
    def test_legacy_copied_cli_without_helper_explains_unavailable_human_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            automation = root / 'automation'
            automation.mkdir()
            shutil.copy2(Path(__file__).parents[1] / 'pipeline.py', automation / 'pipeline.py')
            result = subprocess.run([sys.executable, str(automation / 'pipeline.py'), 'budget-report', '--run', 'historical-fixture'],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('unavailable', result.stdout)
            self.assertFalse((root / '.automation/runs/historical-fixture').exists())

    def test_real_command_timeout_persists_measured_partial_stage_and_original_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            record = p.acquire_lease(cache, duration=21600, budget_policy=p.read_json(CONFIG)['budget'])
            command = [sys.executable, '-c', "import time; print('fixture before timeout', flush=True); time.sleep(30)"]
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                p.execute_stage(record['id'], 'validate', lambda: p.bounded_process(command,
                    cwd=directory, run_id=record['id'], cache=cache, timeout=.2, check=True), cache)
            self.assertIn('fixture before timeout', caught.exception.stdout)
            p.release_lease(record['id'], 'failed', cache=cache)
            report = p.budget_report(record['id'], cache)
            row = report['observations'][0]
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(row['details']['errorType'], 'TimeoutExpired')
            self.assertGreater(row['elapsedSeconds'], .15)
            self.assertEqual(report['deadline'], record['deadline'])
            self.assertEqual(p.read_json(cache / 'runs' / record['id'] / 'run.json')['repairs'], 0)
            self.assertFalse((cache / 'last-success.json').exists())

    def test_closed_reports_rebuild_from_state_without_policy_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / '.automation'
            config = p.read_json(CONFIG)
            snapshot = json.loads(json.dumps(config['budget']))
            record = p.acquire_lease(cache, duration=21600, budget_policy=config['budget'])
            p.execute_stage(record['id'], 'generate', lambda: 'fixture', cache)
            p.release_lease(record['id'], 'failed', cache=cache)
            run = cache / 'runs' / record['id']
            before = {suffix: (run / ('budget-report.' + suffix)).read_bytes() for suffix in ('json', 'txt')}
            for suffix in before:
                (run / ('budget-report.' + suffix)).unlink()
            config['budget']['phasePlanningSeconds']['generation_build'] = 1
            report = p.budget_report(record['id'], cache)
            self.assertEqual(report['policySnapshot'], snapshot)
            self.assertEqual(before, {suffix: (run / ('budget-report.' + suffix)).read_bytes() for suffix in before})
            self.assertFalse((cache / 'run.lock').exists())

    def test_helper_is_a_generation_acceptance_and_allowed_publication_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / 'automation/run_budget.py'
            helper.parent.mkdir()
            helper.write_text('# synthetic before\n')
            self.assertIn('automation/run_budget.py', p.generation_inputs(root))
            before = p.acceptance_digest(root)
            helper.write_text('# synthetic after\n')
            self.assertNotEqual(p.acceptance_digest(root), before)
            self.assertTrue(any('automation/run_budget.py' == path or 'automation/run_budget.py'.startswith(path + '/') for path in p.PUBLISH_PATHS))

    def test_real_cli_publication_refusals_reach_the_instrumented_entrypoints(self):
        for command in ('publish', 'verify'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                automation = root / 'automation'
                automation.mkdir()
                for name in ('pipeline.py', 'run_budget.py'):
                    shutil.copy2(Path(__file__).parents[1] / name, automation / name)
                cache = root / '.automation'
                record = p.acquire_lease(cache, now=100, duration=1)
                result = subprocess.run([sys.executable, str(automation / 'pipeline.py'), command, '--run', record['id']],
                                        cwd=root, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('expired', result.stderr)
                report = p.budget_report(record['id'], cache)
                self.assertEqual(len(report['observations']), 1, 'CLI preflight must not bypass instrumented publication helpers')
                self.assertEqual(report['observations'][0]['status'], 'failed')
                self.assertFalse((cache / 'last-success.json').exists())


if __name__ == '__main__':
    unittest.main()
