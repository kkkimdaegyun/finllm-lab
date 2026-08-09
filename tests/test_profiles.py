from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile_cli = import_script(
    "finllm_profile", ROOT / "scripts" / "finllm_profile.py"
)
load_test = import_script("load_test", ROOT / "scripts" / "load_test.py")


class ProfileConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (ROOT / "configs" / "profiles.json").read_text(encoding="utf-8")
        )

    def test_exactly_three_deployment_profiles(self) -> None:
        self.assertEqual(
            set(self.config["deployment_profiles"]),
            {"profile-a", "profile-b", "reference"},
        )

    def test_a6000_budgets_match_utilization(self) -> None:
        physical = self.config["emulation_host"]["physical_vram_gib"]
        for profile in self.config["deployment_profiles"].values():
            expected = physical * profile["a6000_gpu_memory_utilization"]
            self.assertAlmostEqual(
                profile["a6000_executor_budget_gib"], expected, places=6
            )
            matched_expected = (
                physical * profile["deployment_matched_a6000_utilization"]
            )
            self.assertAlmostEqual(
                profile["deployment_matched_executor_budget_gib"],
                matched_expected,
                places=6,
            )

    def test_quality_reference_is_not_a_deployment_profile(self) -> None:
        reference = self.config["quality_reference"]
        self.assertFalse(reference["deployment_profile"])
        self.assertEqual(reference["gpu_count"], 2)
        self.assertNotIn(
            reference["id"], self.config["deployment_profiles"]
        )

    def test_fp8_is_blocked_on_ampere(self) -> None:
        with self.assertRaises(SystemExit):
            profile_cli.validate_quantization_for_host("fp8", "ampere")

    def test_starting_candidates_require_revision_pinning(self) -> None:
        candidates = json.loads(
            (ROOT / "configs" / "model-candidates.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [candidate["parameter_billions"] for candidate in candidates["candidates"]],
            [8.2, 14.8, 32.8, 14.8],
        )
        for candidate in candidates["candidates"]:
            revision = candidate["revision"]
            self.assertTrue(
                revision is None
                or re.fullmatch(r"[0-9a-f]{40}", revision) is not None,
                "revision must be null before the first run or a 40-char commit SHA",
            )


class PercentileTests(unittest.TestCase):
    def test_linear_percentile(self) -> None:
        self.assertEqual(load_test.percentile([100, 200, 300], 0.5), 200)
        self.assertAlmostEqual(load_test.percentile([100, 200], 0.95), 195)


if __name__ == "__main__":
    unittest.main()
