"""Opt-in checks for an already running native GPU deployment.

Set FINLLM_RUN_GPU_INTEGRATION=1 only after starting deploy/compose.yaml on the
target A6000 host. These tests never invent benchmark or readiness evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from urllib.request import urlopen


ENABLED = os.environ.get("FINLLM_RUN_GPU_INTEGRATION") == "1"


@unittest.skipUnless(ENABLED, "requires an explicitly started GPU deployment")
class NativeGpuDeploymentTests(unittest.TestCase):
    def test_host_exposes_an_a6000(self) -> None:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        self.assertIn("NVIDIA RTX A6000", names)

    def test_running_stack_is_ready_and_exports_metrics(self) -> None:
        api_base = os.environ.get(
            "FINLLM_GPU_INTEGRATION_API_BASE", "http://127.0.0.1:8080"
        )
        with urlopen(f"{api_base}/ready", timeout=5) as response:
            readiness = json.loads(response.read().decode("utf-8"))
        self.assertEqual(readiness["status"], "ready")
        with urlopen(f"{api_base}/metrics", timeout=5) as response:
            metrics = response.read().decode("utf-8")
        self.assertIn("finllm_requests_total", metrics)

    def test_vllm_lists_the_pinned_model(self) -> None:
        vllm_base = os.environ.get(
            "FINLLM_GPU_INTEGRATION_VLLM_BASE", "http://127.0.0.1:8000/v1"
        )
        with urlopen(f"{vllm_base}/models", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn(
            "Qwen/Qwen3-14B-AWQ",
            {item.get("id") for item in payload.get("data", [])},
        )


if __name__ == "__main__":
    unittest.main()
