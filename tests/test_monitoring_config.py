"""Monitoring configuration tests.

A dashboard that queries a metric nobody exports renders an empty panel, and an
empty panel looks exactly like a healthy service. These tests exist so that
failure mode cannot survive a commit.

Every metric referenced by the dashboard must be either
  (a) declared in docs/cross-review/interface-contract-v0.2.md (A파트가 노출할 것), or
  (b) recorded in ops/evidence/ as actually observed on a running endpoint.

Nothing here needs a GPU, Docker, or a network. It is all static analysis of
version-controlled files.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import regression_gate  # noqa: E402

MONITORING = ROOT / "monitoring"
RULES = MONITORING / "prometheus" / "rules" / "finllm-alerts.yml"
PROMETHEUS_YML = MONITORING / "prometheus" / "prometheus.yml"
DASHBOARD = MONITORING / "grafana" / "dashboards" / "finllm-service.json"
DATASOURCE = MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
CONTRACT = ROOT / "docs" / "cross-review" / "interface-contract-v0.2.md"
EVIDENCE = ROOT / "ops" / "evidence"

METRIC_REFERENCE = re.compile(r"(finllm_[a-z0-9_]+|vllm:[a-z0-9_]+|DCGM_FI_[A-Z0-9_]+)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def dashboard() -> dict:
    return json.loads(read(DASHBOARD))


def dashboard_expressions() -> list[str]:
    return [
        target["expr"]
        for panel in dashboard()["panels"]
        for target in panel.get("targets", [])
        if "expr" in target
    ]


def alert_blocks() -> list[tuple[str, str]]:
    text = read(RULES)
    blocks = re.split(r"^\s*-\s+alert:\s*", text, flags=re.MULTILINE)[1:]
    return [(block.splitlines()[0].strip(), block) for block in blocks]


def known_metric_names() -> set[str]:
    names: set[str] = set()
    # (a) A파트가 노출하기로 계약한 것
    names.update(re.findall(r"finllm_[a-z0-9_]+", read(CONTRACT)))
    # (b) 실제 endpoint에서 관측된 것
    for evidence_file in ("vllm-0.9.2-metric-names.txt", "dcgm-metric-names.txt"):
        path = EVIDENCE / evidence_file
        names.update(
            line.strip() for line in read(path).splitlines() if line.strip()
        )
    return names


def base_metric(name: str) -> str:
    for suffix in ("_bucket", "_count", "_sum"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


class DashboardTests(unittest.TestCase):
    def test_dashboard_is_valid_json_with_stable_uid(self) -> None:
        data = dashboard()
        self.assertEqual(data["uid"], "finllm-service")
        self.assertTrue(data["panels"])

    def test_every_referenced_metric_is_declared_or_observed(self) -> None:
        known = known_metric_names()
        unknown: list[str] = []
        for expr in dashboard_expressions():
            for reference in METRIC_REFERENCE.findall(expr):
                if base_metric(reference) not in known:
                    unknown.append(reference)
        self.assertEqual(
            sorted(set(unknown)),
            [],
            "dashboard가 계약에도 없고 관측된 적도 없는 metric을 조회한다. "
            "빈 패널은 건강한 서비스와 구분되지 않는다.",
        )

    def test_dashboard_answers_every_required_operational_question(self) -> None:
        """요구된 8개 질문이 실제로 패널 제목에 있는가."""
        titles = " ".join(panel["title"] for panel in dashboard()["panels"])
        for question in (
            "요청이 들어오는가",
            "오류가 증가하는가",
            "P95 latency가 악화되는가",
            "queue가 쌓이는가",
            "GPU가 포화되는가",
            "VRAM이 위험 수준인가",
            "병목",
        ):
            self.assertIn(question, titles, f"'{question}' 질문에 답하는 패널이 없다")

    def test_retrieval_and_generation_are_comparable_on_one_panel(self) -> None:
        """retrieval이 병목인지 generation이 병목인지는 둘을 같이 봐야 답이 된다."""
        for panel in dashboard()["panels"]:
            if "병목" in panel["title"]:
                exprs = " ".join(t["expr"] for t in panel["targets"])
                self.assertIn("finllm_retrieval_duration_seconds", exprs)
                self.assertIn("finllm_generation_duration_seconds", exprs)
                return
        self.fail("병목 분해 패널이 없다")

    def test_datasource_uid_matches_provisioning(self) -> None:
        declared = re.search(r"^\s*uid:\s*(\S+)", read(DATASOURCE), re.MULTILINE)
        self.assertIsNotNone(declared)
        uid = declared.group(1)
        for panel in dashboard()["panels"]:
            source = panel.get("datasource") or {}
            if source:
                self.assertEqual(source.get("uid"), uid, panel["title"])

    def test_slo_threshold_lines_match_configs(self) -> None:
        """SLO 점선이 configs/profiles.json과 다른 값을 그리면 dashboard가 거짓말을 한다."""
        config = json.loads(read(ROOT / "configs" / "profiles.json"))
        policy = config["benchmark_policy"]
        vram_class_mib = config["deployment_profiles"]["profile-a"]["vram_class_gib"] * 1024
        wanted = {
            "오류가 증가하는가": policy["error_rate_max"],
            "P95 latency가 악화되는가": policy["p95_ttft_ms_max"] / 1000.0,
            "VRAM이 위험 수준인가": vram_class_mib,
        }
        for panel in dashboard()["panels"]:
            for marker, expected in wanted.items():
                if marker in panel["title"]:
                    steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
                    values = [s["value"] for s in steps if s["value"] is not None]
                    self.assertIn(
                        expected,
                        values,
                        f"{panel['title']}: threshold 선 {values} 에 {expected} 이 없다",
                    )


class AlertRuleTests(unittest.TestCase):
    def test_alert_names_are_unique(self) -> None:
        names = [name for name, _ in alert_blocks()]
        self.assertEqual(len(names), len(set(names)))

    def test_at_least_one_alert_exists(self) -> None:
        self.assertGreaterEqual(len(alert_blocks()), 1)

    def test_every_alert_references_a_runbook_that_exists(self) -> None:
        for name, block in alert_blocks():
            match = re.search(r"runbook:\s*\"?([^\"\n]+)\"?", block)
            self.assertIsNotNone(match, f"{name}: runbook annotation이 없다")
            runbook = ROOT / match.group(1).strip()
            self.assertTrue(runbook.exists(), f"{name}: runbook 파일 없음 {runbook}")

    def test_every_alert_declares_where_its_threshold_came_from(self) -> None:
        """근거 없는 threshold를 조용히 넣지 못하게 한다."""
        for name, block in alert_blocks():
            self.assertTrue(
                "threshold_source:" in block,
                f"{name}: threshold_source annotation이 없다",
            )

    def test_ungrounded_thresholds_are_explicitly_marked(self) -> None:
        """근거가 없으면 PENDING_THRESHOLD_VALIDATION으로 표시되어야 한다.

        KV cache 0.9는 이 프로젝트에서 측정으로 뒷받침되지 않은 관례값이다.
        그렇게 적혀 있는지 확인한다.
        """
        for name, block in alert_blocks():
            if name == "FinLLMKVCacheHighUtilization":
                self.assertIn("PENDING_THRESHOLD_VALIDATION", block)
                return
        self.fail("FinLLMKVCacheHighUtilization alert를 찾지 못했다")

    def test_thresholds_match_configs_profiles(self) -> None:
        config = json.loads(read(ROOT / "configs" / "profiles.json"))
        result = regression_gate.stage_alert_threshold_consistency({"config": config})
        self.assertEqual(result.status, regression_gate.PASS, result.detail)

    def test_alerts_reference_only_known_metrics(self) -> None:
        known = known_metric_names() | {"up"}
        unknown: list[str] = []
        for _name, block in alert_blocks():
            for reference in METRIC_REFERENCE.findall(block):
                if base_metric(reference) not in known:
                    unknown.append(reference)
        self.assertEqual(sorted(set(unknown)), [])


class ScrapeConfigTests(unittest.TestCase):
    def test_all_required_jobs_are_scraped(self) -> None:
        text = read(PROMETHEUS_YML)
        for job in ("finllm-gateway", "vllm", "dcgm"):
            self.assertIn(f"job_name: {job}", text)

    def test_gateway_target_list_is_file_based(self) -> None:
        """A파트가 아직 없을 때 존재하지 않는 서비스에 대해 alert가 울리면 안 된다."""
        text = read(PROMETHEUS_YML)
        self.assertIn("targets/gateway.json", text)
        targets = json.loads(read(MONITORING / "prometheus" / "targets" / "gateway.json"))
        self.assertIsInstance(targets, list)


class GpuEvidenceTests(unittest.TestCase):
    def test_fb_total_is_collected(self) -> None:
        """완료 조건 10의 'GPU memory total'. DCGM 기본 field set에는 없어서
        monitoring/dcgm/finllm-counters.csv로 명시적으로 추가했다."""
        counters = read(MONITORING / "dcgm" / "finllm-counters.csv")
        self.assertIn("DCGM_FI_DEV_FB_TOTAL", counters)
        observed = read(EVIDENCE / "dcgm-metric-names.txt")
        self.assertIn("DCGM_FI_DEV_FB_TOTAL", observed)

    def test_vllm_ttft_buckets_lack_the_slo_boundary(self) -> None:
        """이 프로젝트의 SLO는 2,000ms인데 vLLM 0.9.2 bucket에는 2.0이 없다.

        이 사실이 계약에서 gateway histogram에 le=2 경계를 요구하는 근거다.
        vLLM 버전이 올라가 bucket이 바뀌면 이 테스트가 깨지고, 그때 계약의
        근거를 다시 검토하게 된다.
        """
        buckets = read(EVIDENCE / "vllm-ttft-buckets.txt").split()
        self.assertIn("1.0", buckets)
        self.assertIn("2.5", buckets)
        self.assertNotIn("2.0", buckets)
        self.assertIn("le=2", read(CONTRACT).replace(" ", "").replace("`", ""))


if __name__ == "__main__":
    unittest.main()
