"""OpenAI-compatible dependency probe and response validation tests."""

from __future__ import annotations

import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from service.inference import InferenceError, OpenAIInferenceClient


MODEL_ID = "Qwen/Qwen3-14B-AWQ"


class InferenceStubHandler(BaseHTTPRequestHandler):
    models_status = 200
    models_body = json.dumps({"data": [{"id": MODEL_ID}]}).encode("utf-8")
    generation_body = json.dumps(
        {"choices": [{"message": {"content": "stub answer"}}]}
    ).encode("utf-8")

    def do_GET(self) -> None:  # noqa: N802
        body = type(self).models_body
        self.send_response(type(self).models_status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = type(self).generation_body
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@contextmanager
def inference_stub():
    InferenceStubHandler.models_status = 200
    InferenceStubHandler.models_body = json.dumps(
        {"data": [{"id": MODEL_ID}]}
    ).encode("utf-8")
    InferenceStubHandler.generation_body = json.dumps(
        {"choices": [{"message": {"content": "stub answer"}}]}
    ).encode("utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), InferenceStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield OpenAIInferenceClient(
            f"http://127.0.0.1:{server.server_address[1]}/v1", 2
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class InferenceAdapterTests(unittest.TestCase):
    def test_model_probe_success(self) -> None:
        with inference_stub() as client:
            self.assertEqual(
                client.probe_model(MODEL_ID),
                (True, True, "model listed by inference endpoint"),
            )

    def test_http_error_means_reachable_but_not_model_ready(self) -> None:
        with inference_stub() as client:
            InferenceStubHandler.models_status = 503
            reachable, ready, detail = client.probe_model(MODEL_ID)
            self.assertTrue(reachable)
            self.assertFalse(ready)
            self.assertIn("HTTP 503", detail)

    def test_invalid_models_json_means_reachable_but_not_ready(self) -> None:
        with inference_stub() as client:
            InferenceStubHandler.models_body = b"not-json"
            reachable, ready, detail = client.probe_model(MODEL_ID)
            self.assertTrue(reachable)
            self.assertFalse(ready)
            self.assertIn("invalid inference /models JSON", detail)

    def test_generation_schema_is_validated(self) -> None:
        with inference_stub() as client:
            answer = client.generate(
                MODEL_ID,
                [{"role": "user", "content": "test"}],
                {"max_tokens": 1},
            )
            self.assertEqual(answer, "stub answer")
            InferenceStubHandler.generation_body = b"{}"
            with self.assertRaisesRegex(InferenceError, "chat contract"):
                client.generate(
                    MODEL_ID,
                    [{"role": "user", "content": "test"}],
                    {"max_tokens": 1},
                )

    def test_generation_probe_observes_the_engine_path(self) -> None:
        with inference_stub() as client:
            ready, detail = client.probe_generation(MODEL_ID)
            self.assertTrue(ready)
            self.assertIn("succeeded", detail)
            InferenceStubHandler.generation_body = b"{}"
            ready, detail = client.probe_generation(MODEL_ID)
            self.assertFalse(ready)
            self.assertIn("failed", detail)


if __name__ == "__main__":
    unittest.main()
