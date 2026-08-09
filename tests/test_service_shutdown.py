"""Deterministic admission and in-flight drain policy tests."""

from __future__ import annotations

import tempfile
import threading
import unittest
import json
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from service.http_server import FinLLMHTTPServer, FinLLMRequestHandler
from service.runtime import ServiceRuntime, ServiceUnavailableError
from tests.service_test_support import BlockingInference, make_config


class ServiceShutdownTests(unittest.TestCase):
    def test_shutdown_rejects_new_work_and_drains_an_accepted_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inference = BlockingInference()
            runtime = ServiceRuntime(
                make_config(Path(directory), shutdown_timeout_seconds=2),
                inference=inference,
            )
            runtime.initialize()
            result: list[dict] = []
            errors: list[BaseException] = []

            def execute() -> None:
                try:
                    result.append(
                        runtime.handle_rag_request(
                            {
                                "question": "고액현금거래 보고 기한은?",
                                "role": "branch-staff",
                            }
                        )
                    )
                except BaseException as exc:  # captured for the test thread
                    errors.append(exc)

            request_thread = threading.Thread(target=execute)
            request_thread.start()
            self.assertTrue(inference.started.wait(1), "request never reached generation")
            self.assertEqual(runtime.metrics.snapshot()["requests_in_flight"], 1)

            runtime.begin_shutdown()
            self.assertFalse(runtime.readiness().ready)
            with self.assertRaises(ServiceUnavailableError):
                runtime.handle_rag_request(
                    {"question": "새 요청", "role": "branch-staff"}
                )

            inference.release.set()
            request_thread.join(timeout=2)
            self.assertFalse(request_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(result), 1)
            self.assertTrue(runtime.wait_for_drain())
            self.assertEqual(runtime.metrics.snapshot()["requests_in_flight"], 0)
            self.assertEqual(runtime.metrics.snapshot()["request_errors_total"], 1)
            runtime.close()

    def test_drain_includes_http_response_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = ServiceRuntime(
                make_config(Path(directory), shutdown_timeout_seconds=0.1),
                inference=BlockingInference(),
            )
            runtime.initialize()
            # This test isolates response delivery; generation itself is released first.
            runtime.inference.release.set()
            server = FinLLMHTTPServer(("127.0.0.1", 0), runtime)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            write_started = threading.Event()
            release_write = threading.Event()
            original_write = FinLLMRequestHandler._write_json

            def delayed_write(handler, status, payload):
                if handler.command == "POST" and status == 200:
                    write_started.set()
                    release_write.wait(2)
                return original_write(handler, status, payload)

            result: list[bytes] = []

            def request() -> None:
                body = json.dumps(
                    {"question": "보고 기한은?", "role": "branch-staff"}
                ).encode("utf-8")
                req = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/v1/rag/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=3) as response:
                    result.append(response.read())

            with mock.patch.object(FinLLMRequestHandler, "_write_json", delayed_write):
                client = threading.Thread(target=request)
                client.start()
                self.assertTrue(write_started.wait(1))
                runtime.begin_shutdown()
                self.assertFalse(runtime.wait_for_drain())
                self.assertEqual(runtime.metrics.snapshot()["requests_in_flight"], 1)
                release_write.set()
                client.join(timeout=2)
                self.assertFalse(client.is_alive())
                self.assertTrue(result)
                self.assertTrue(runtime.wait_for_drain())
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
