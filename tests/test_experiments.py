from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ExperimentDriverTest(unittest.TestCase):
    def test_h2_ours_sit_smoke_recovers_after_bad_regression(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "experiments/driver.py",
                    "--experiment",
                    "h2",
                    "--condition",
                    "ours-sit",
                    "--steps",
                    "5",
                    "--bad-step",
                    "3",
                    "--seed",
                    "0",
                    "--run-id",
                    "h2-test",
                    "--output-root",
                    tmp,
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=True,
            )

            summary_path = Path(tmp) / "h2-test" / "summary.json"
            trajectory_path = Path(tmp) / "h2-test" / "trajectory.jsonl"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

            self.assertIn("Experiment complete:", completed.stdout)
            self.assertEqual(summary["condition"], "ours-sit")
            self.assertEqual(summary["detection_step"], 3)
            self.assertEqual(summary["recovery_steps"], 1)
            self.assertEqual(summary["final_success_rate"], 1.0)
            self.assertTrue(trajectory_path.exists())


if __name__ == "__main__":
    unittest.main()
