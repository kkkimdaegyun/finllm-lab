import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("autopsy", PROJECT / "src" / "autopsy.py")
autopsy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(autopsy)


class QuantizationAutopsyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runs = autopsy.load_runs(autopsy.DEFAULT_RESULTS)
        cls.summary = autopsy.build_summary(cls.runs)

    def test_protocol_and_repeats(self) -> None:
        self.assertEqual(len(self.runs), 18)
        self.assertEqual(self.summary["protocol"]["configurations"], 6)
        self.assertTrue(all(row["repeats"] == 3 for row in self.summary["configurations"]))

    def test_key_finding_is_reproducible(self) -> None:
        findings = self.summary["findings"]
        self.assertTrue(math.isclose(findings["awq_throughput_recovery_x"], 5.47, abs_tol=.05))
        self.assertLess(abs(findings["bf16_throughput_change_pct"]), 5)
        self.assertGreater(findings["weight_memory_saved_gib"], 5.8)

    def test_evidence_boundary_is_explicit(self) -> None:
        boundary = self.summary["evidence_boundary"]
        self.assertEqual(boundary["type"], "memory-budget-emulation")
        self.assertEqual(boundary["target_card_claim"], "NOT_EXECUTED")
        self.assertTrue(any("모델 크기" in item for item in self.summary["limitations"]))

    def test_build_and_check_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = root / "artifacts"
            portfolio = root / "index.html"
            autopsy.build(autopsy.DEFAULT_RESULTS, artifacts, portfolio)
            checked = autopsy.check(artifacts, portfolio)
            stored = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(checked, stored)
            self.assertTrue((artifacts / "benchmark.csv").is_file())
            self.assertTrue((artifacts / "REPORT.md").is_file())


if __name__ == "__main__":
    unittest.main()
