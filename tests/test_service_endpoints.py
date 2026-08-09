"""GPU-free endpoint and application-metric contract tests."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service.http_server import FinLLMHTTPServer
from service.inference import InferenceError
from service.runtime import ServiceRuntime
from tests.service_test_support import FakeInference, make_config


@contextmanager
def running_service(runtime: ServiceRuntime):
    server = FinLLMHTTPServer(("127.0.0.1", 0), runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.begin_shutdown()
        runtime.wait_for_drain()
        runtime.close()


def get(url: str) -> tuple[int, str, str]:
    try:
        with urlopen(url, timeout=2) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        return (
            exc.code,
            exc.headers.get("Content-Type", ""),
            exc.read().decode("utf-8"),
        )


class ServiceEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.fake = FakeInference()
        self.runtime = ServiceRuntime(
            make_config(Path(self.directory.name)), inference=self.fake
        )
        self.runtime.initialize()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_health_only_reports_process_liveness(self) -> None:
        with running_service(self.runtime) as base_url:
            status, content_type, body = get(f"{base_url}/health")
            self.assertEqual(status, 200)
            self.assertIn("application/json", content_type)
            self.assertEqual(json.loads(body), {"status": "alive"})

            self.fake.reachable = False
            self.runtime.refresh_inference_readiness()
            status, _, _ = get(f"{base_url}/health")
            self.assertEqual(status, 200)
            status, _, ready_body = get(f"{base_url}/ready")
            self.assertEqual(status, 503)
            self.assertFalse(
                json.loads(ready_body)["checks"]["inference_endpoint_reachable"]
            )

    def test_readiness_success_lists_each_required_check(self) -> None:
        with running_service(self.runtime) as base_url:
            status, _, body = get(f"{base_url}/ready")
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(
                payload["checks"],
                {
                    "application_initialized": True,
                    "retriever_initialized": True,
                    "inference_endpoint_reachable": True,
                    "model_ready": True,
                    "accepting_requests": True,
                },
            )

    def test_model_not_ready_returns_non_success(self) -> None:
        with running_service(self.runtime) as base_url:
            self.fake.model_ready = False
            self.runtime.refresh_inference_readiness()
            status, _, body = get(f"{base_url}/ready")
            self.assertEqual(status, 503)
            checks = json.loads(body)["checks"]
            self.assertTrue(checks["inference_endpoint_reachable"])
            self.assertFalse(checks["model_ready"])

    def test_metrics_exposition_and_successful_rag_request(self) -> None:
        with running_service(self.runtime) as base_url:
            request_body = json.dumps(
                {"question": "고액현금거래 보고 기한은?", "role": "branch-staff"}
            ).encode("utf-8")
            request = Request(
                f"{base_url}/v1/rag/chat/completions",
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(result["answer"], "테스트 응답")
            self.assertEqual(
                result["provenance"]["model_revision"],
                result["provenance"]["tokenizer_revision"],
            )
            self.assertTrue(result["retrieval"]["chunk_ids"])

            status, content_type, metrics = get(f"{base_url}/metrics")
            self.assertEqual(status, 200)
            self.assertIn("text/plain", content_type)
            for metric in (
                "finllm_requests_total",
                "finllm_request_errors_total",
                "finllm_requests_in_flight",
                "finllm_request_duration_seconds",
                "finllm_retrieval_duration_seconds",
                "finllm_generation_duration_seconds",
            ):
                self.assertIn(metric, metrics)
            self.assertIn("finllm_requests_total 1", metrics)
            self.assertIn("finllm_request_duration_seconds_count 1", metrics)
            self.assertIn("finllm_retrieval_duration_seconds_count 1", metrics)
            self.assertIn("finllm_generation_duration_seconds_count 1", metrics)

    def test_invalid_json_counts_as_a_request_error(self) -> None:
        with running_service(self.runtime) as base_url:
            request = Request(
                f"{base_url}/v1/rag/chat/completions",
                data=b"{invalid",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=2)
            self.assertEqual(raised.exception.code, 400)
            _, _, metrics = get(f"{base_url}/metrics")
            self.assertIn("finllm_requests_total 1", metrics)
            self.assertIn("finllm_request_errors_total 1", metrics)

    def test_generation_failure_latches_readiness_until_engine_probe_recovers(self) -> None:
        class RecoverableFailure(FakeInference):
            failing = True

            def generate(self, model_id, messages, generation):
                if self.failing:
                    raise InferenceError("engine failed")
                return super().generate(model_id, messages, generation)

            def probe_generation(self, model_id):
                return (not self.failing, "recovered" if not self.failing else "failed")

        inference = RecoverableFailure()
        runtime = ServiceRuntime(
            make_config(Path(self.directory.name)), inference=inference
        )
        runtime.initialize()
        with running_service(runtime) as base_url:
            request = Request(
                f"{base_url}/v1/rag/chat/completions",
                data=json.dumps(
                    {"question": "보고 기한은?", "role": "branch-staff"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=2)
            self.assertEqual(raised.exception.code, 502)
            status, _, _ = get(f"{base_url}/ready")
            self.assertEqual(status, 503)
            inference.failing = False
            runtime.refresh_inference_readiness()
            status, _, _ = get(f"{base_url}/ready")
            self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
