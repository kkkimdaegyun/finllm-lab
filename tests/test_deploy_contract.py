"""Static and Compose-level deployment contract checks; no GPU required."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
REVISION = "31c69efc29464b6bb0aee1398b5a7b50a99340c3"


class DeployContractTests(unittest.TestCase):
    def test_service_config_pins_model_and_tokenizer_revision(self) -> None:
        payload = json.loads(
            (DEPLOY / "config" / "service.json").read_text(encoding="utf-8")
        )
        inference = payload["inference"]
        self.assertEqual(inference["model_revision"], REVISION)
        self.assertEqual(inference["tokenizer_revision"], REVISION)
        self.assertRegex(inference["model_revision"], r"^[0-9a-f]{40}$")

    def test_cuda_line_is_pinned_and_not_replaced(self) -> None:
        dockerfile = (DEPLOY / "Dockerfile.vllm").read_text(encoding="utf-8")
        self.assertIn("nvidia/cuda:12.2.2-devel-ubuntu22.04@sha256:", dockerfile)
        self.assertNotIn("nvidia/cuda:latest", dockerfile)
        self.assertNotIn("12.8", dockerfile)
        self.assertIn("vllm.__version__ == '0.9.2'", dockerfile)
        self.assertIn("sys.version.split()[0] == '3.10.12'", dockerfile)

    def test_compose_has_api_inference_gpu_and_loopback_contracts(self) -> None:
        compose = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("  vllm:", compose)
        self.assertIn("  finllm-api:", compose)
        self.assertIn("device_ids:", compose)
        self.assertIn("127.0.0.1:${FINLLM_API_PORT:-8080}:8080", compose)
        self.assertIn("127.0.0.1:${FINLLM_VLLM_PORT:-8000}:8000", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertIn(REVISION, compose)

    def test_prometheus_metric_names_are_stable(self) -> None:
        metrics_source = (ROOT / "service" / "metrics.py").read_text(encoding="utf-8")
        for metric in (
            "finllm_requests_total",
            "finllm_request_errors_total",
            "finllm_requests_in_flight",
            "finllm_request_duration_seconds",
            "finllm_retrieval_duration_seconds",
            "finllm_generation_duration_seconds",
        ):
            self.assertIn(metric, metrics_source)

    def test_start_scripts_are_executable(self) -> None:
        for relative in ("start_service.sh", "up.sh", "validate.sh", "smoke.sh"):
            self.assertTrue((ROOT / "scripts" / "deploy" / relative).stat().st_mode & 0o111)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is not installed")
    def test_docker_compose_config_is_valid(self) -> None:
        completed = subprocess.run(
            ["docker", "compose", "--file", "deploy/compose.yaml", "config", "--quiet"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
