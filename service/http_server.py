"""HTTP transport for the FinLLM production service."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from service.inference import InferenceError
from service.runtime import (
    BadRequestError,
    ServiceRuntime,
    ServiceUnavailableError,
)


LOGGER = logging.getLogger("finllm.http")


class FinLLMHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address: tuple[str, int], runtime: ServiceRuntime) -> None:
        self.runtime = runtime
        self.request_queue_size = runtime.config.listen_backlog
        super().__init__(address, FinLLMRequestHandler)


class FinLLMRequestHandler(BaseHTTPRequestHandler):
    server: FinLLMHTTPServer
    protocol_version = "HTTP/1.1"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._write_json(HTTPStatus.OK, self.server.runtime.health())
            return
        if path == "/ready":
            snapshot = self.server.runtime.readiness()
            self._write_json(
                HTTPStatus.OK if snapshot.ready else HTTPStatus.SERVICE_UNAVAILABLE,
                snapshot.as_dict(),
            )
            return
        if path == "/metrics":
            raw = self.server.runtime.metrics.render_prometheus().encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "text/plain; version=0.0.4; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.split("?", 1)[0] != "/v1/rag/chat/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.server.runtime.metrics.request_rejected()
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return
        if length <= 0 or length > self.server.runtime.config.max_request_bytes:
            self.server.runtime.metrics.request_rejected()
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request body is empty or too large"},
            )
            return
        lease = None
        response_status = HTTPStatus.OK
        response_payload: dict[str, Any]
        request_succeeded = False
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            lease = self.server.runtime.admit_rag_request(payload)
            response_payload = self.server.runtime.execute_rag_request(payload)
            request_succeeded = True
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.server.runtime.metrics.request_rejected()
            response_status = HTTPStatus.BAD_REQUEST
            response_payload = {"error": "invalid JSON"}
        except BadRequestError as exc:
            response_status = HTTPStatus.BAD_REQUEST
            response_payload = {"error": str(exc)}
        except ServiceUnavailableError as exc:
            response_status = HTTPStatus.SERVICE_UNAVAILABLE
            response_payload = {"error": str(exc)}
        except InferenceError as exc:
            response_status = HTTPStatus.BAD_GATEWAY
            response_payload = {"error": str(exc)}
        except Exception:  # noqa: BLE001 - avoid leaking internals to the client
            LOGGER.exception("unhandled request failure")
            response_status = HTTPStatus.INTERNAL_SERVER_ERROR
            response_payload = {"error": "internal server error"}

        delivered = False
        try:
            self._write_json(response_status, response_payload)
            delivered = True
        except OSError as exc:
            LOGGER.warning(
                "response delivery failed after status %s: %s",
                response_status,
                type(exc).__name__,
            )
        finally:
            if lease is not None:
                lease.finish(success=request_succeeded and delivered)

    def log_message(self, format_string: str, *args: object) -> None:
        LOGGER.info(
            "%s - %s",
            self.address_string(),
            format_string % args,
        )
