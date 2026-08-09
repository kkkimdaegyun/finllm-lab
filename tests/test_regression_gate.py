"""Regression gate tests.

The point of these tests is not that the gate passes on a good repository —
`--stage cpu` already demonstrates that. The point is that it **fails** on each
kind of bad change. A gate that has never been observed to block anything is
not evidence of anything.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import regression_gate  # noqa: E402


def load_baseline() -> dict:
    return json.loads(
        (ROOT / "ops" / "baselines" / "profile-a-baseline.json").read_text(encoding="utf-8")
    )


def load_config() -> dict:
    return json.loads((ROOT / "configs" / "profiles.json").read_text(encoding="utf-8"))


def evaluation(
    quality: float = 97.667,
    acl: int = 0,
    injection: int = 2,
) -> dict:
    return {
        "summary": {
            "case_count": 60,
            "quality_score": quality,
            "answer_correctness": 100.0,
            "groundedness": 93.333,
            "citation_accuracy": 100.0,
            "abstention_safety": 96.667,
            "acl_violations": acl,
            "acl_violation_cases": ["eval-031"] if acl else [],
            "injection_successes": injection,
            "injection_success_cases": [],
        }
    }


def context(**overrides) -> dict:
    ctx = {
        "config": load_config(),
        "baseline": load_baseline(),
        "index_path": ROOT / "work" / "v02" / "gate-index.json",
        "top_k": 8,
    }
    ctx.update(overrides)
    return ctx


class QualityRegressionTests(unittest.TestCase):
    def test_baseline_quality_passes(self) -> None:
        result = regression_gate.stage_quality_regression(
            context(evaluation=evaluation())
        )
        self.assertEqual(result.status, regression_gate.PASS)

    def test_below_absolute_policy_minimum_fails(self) -> None:
        """configs/profiles.json의 quality_score_min(90)을 gate가 실제로 쓴다."""
        result = regression_gate.stage_quality_regression(
            context(evaluation=evaluation(quality=88.0))
        )
        self.assertEqual(result.status, regression_gate.FAIL)
        self.assertIn("절대 기준 미달", result.detail)

    def test_regression_below_baseline_fails_even_when_above_90(self) -> None:
        """90점은 넘지만 baseline보다 나빠진 변경도 막는다.

        이것이 hard gate와 regression gate의 차이다. 95.0은 합격선 90을 넘지만
        측정된 baseline 97.667보다 나쁘므로 회귀다.
        """
        result = regression_gate.stage_quality_regression(
            context(evaluation=evaluation(quality=95.0))
        )
        self.assertEqual(result.status, regression_gate.FAIL)
        self.assertIn("baseline 대비 회귀", result.detail)

    def test_tolerance_comes_from_a_measurement(self) -> None:
        baseline = load_baseline()
        self.assertIn("MEASURED", baseline["quality_regression_tolerance_source"])
        self.assertIn(
            "work/v02/eval-variance", baseline["quality_regression_tolerance_source"]
        )

    def test_gate_does_not_redefine_the_project_threshold(self) -> None:
        """gate가 configs/profiles.json의 합격선을 자기 값으로 대체하지 않는다."""
        config = load_config()
        self.assertEqual(config["benchmark_policy"]["quality_score_min"], 90)
        ctx = context(evaluation=evaluation(quality=89.999))
        self.assertEqual(
            regression_gate.stage_quality_regression(ctx).status, regression_gate.FAIL
        )


class SecurityRegressionTests(unittest.TestCase):
    def test_any_acl_violation_fails(self) -> None:
        result = regression_gate.stage_acl_runtime(context(evaluation=evaluation(acl=1)))
        self.assertEqual(result.status, regression_gate.FAIL)

    def test_worse_injection_defence_fails(self) -> None:
        result = regression_gate.stage_injection_regression(
            context(evaluation=evaluation(injection=3))
        )
        self.assertEqual(result.status, regression_gate.FAIL)

    def test_equal_injection_defence_passes(self) -> None:
        """baseline 2건은 미해결 결함이다. gate는 악화만 막는다."""
        result = regression_gate.stage_injection_regression(
            context(evaluation=evaluation(injection=2))
        )
        self.assertEqual(result.status, regression_gate.PASS)

    def test_improved_injection_defence_passes(self) -> None:
        result = regression_gate.stage_injection_regression(
            context(evaluation=evaluation(injection=0))
        )
        self.assertEqual(result.status, regression_gate.PASS)

    def test_unauthorized_retrieval_is_checked_without_a_gpu(self) -> None:
        """권한 검사가 CPU 단계에 있다는 것이 '모델이 아니라 데이터 층에서
        강제한다'는 주장의 증거다."""
        stage_names = [name for name, _ in regression_gate.CPU_STAGES]
        self.assertIn("retrieval-acl", stage_names)
        result = regression_gate.stage_retrieval_acl(context())
        self.assertEqual(result.status, regression_gate.PASS)
        self.assertGreater(result.evidence["cases_checked"], 0)


class ProvenanceDriftTests(unittest.TestCase):
    def test_changed_eval_set_fails(self) -> None:
        ctx = context()
        ctx["baseline"] = copy.deepcopy(ctx["baseline"])
        ctx["baseline"]["eval_set_sha256"] = "0" * 64
        result = regression_gate.stage_eval_set_integrity(ctx)
        self.assertEqual(result.status, regression_gate.FAIL)

    def test_changed_retriever_config_fails(self) -> None:
        ctx = context()
        ctx["baseline"] = copy.deepcopy(ctx["baseline"])
        ctx["baseline"]["retriever_config_hash"] = "deadbeef1234"
        result = regression_gate.stage_retriever_config_hash(ctx)
        self.assertEqual(result.status, regression_gate.FAIL)

    def test_changed_prompt_revision_fails(self) -> None:
        ctx = context()
        ctx["baseline"] = copy.deepcopy(ctx["baseline"])
        ctx["baseline"]["prompt_revision"] = "prompt-v9.9"
        result = regression_gate.stage_prompt_revision(ctx)
        self.assertEqual(result.status, regression_gate.FAIL)


class AlertThresholdConsistencyTests(unittest.TestCase):
    def test_rules_match_configs(self) -> None:
        result = regression_gate.stage_alert_threshold_consistency(context())
        self.assertEqual(result.status, regression_gate.PASS, result.detail)

    def test_drifted_slo_is_detected(self) -> None:
        """configs의 SLO를 바꾸면 rule 파일과 어긋나 gate가 막는다."""
        ctx = context()
        ctx["config"] = copy.deepcopy(ctx["config"])
        ctx["config"]["benchmark_policy"]["p95_ttft_ms_max"] = 3000
        result = regression_gate.stage_alert_threshold_consistency(ctx)
        self.assertEqual(result.status, regression_gate.FAIL)
        self.assertTrue(
            any("FinLLMHighP95TTFT" in m for m in result.evidence["mismatches"]),
            result.evidence,
        )


class StageSeparationTests(unittest.TestCase):
    def test_cpu_and_gpu_stages_are_disjoint(self) -> None:
        cpu = {name for name, _ in regression_gate.CPU_STAGES}
        gpu = {name for name, _ in regression_gate.GPU_STAGES}
        self.assertEqual(cpu & gpu, set())

    @unittest.skipIf(
        os.environ.get("FINLLM_GATE_NESTED") == "1",
        "regression gate가 이 스위트를 실행 중이다 — 재귀 방지",
    )
    def test_gpu_stages_are_reported_skipped_not_passed(self) -> None:
        """GPU 없는 CI가 GPU 단계를 통과했다고 말하지 않는지 실제로 실행해 본다.

        --skip unit-tests: gate의 unit-tests 단계는 이 스위트를 다시 돌린다.
        여기서 그것까지 실행하면 재귀한다.
        """
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "regression_gate.py"),
                "--stage",
                "cpu",
                "--skip",
                "unit-tests",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        for name, _ in regression_gate.GPU_STAGES:
            self.assertRegex(completed.stdout, rf"\[SKIP\]\s+{name}\b")
        # GPU 4개 + 명시적으로 건너뛴 unit-tests 1개
        self.assertIn("skipped=5", completed.stdout)

    def test_skipped_stages_never_count_as_passed(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "regression_gate.py"),
                "--stage",
                "cpu",
                "--skip",
                "unit-tests",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("통과로 세지 않는다", completed.stdout)
        self.assertNotRegex(completed.stdout, r"\[OK  \]\s+unit-tests")


if __name__ == "__main__":
    unittest.main()
