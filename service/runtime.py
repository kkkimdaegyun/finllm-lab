"""Service lifecycle, readiness, RAG orchestration, and request admission."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from scripts.rag_eval import build_messages
from scripts.rag_index import Retriever

from service.config import ServiceConfig, StartupValidationError
from service.inference import InferenceBackend, OpenAIInferenceClient
from service.metrics import MetricRegistry


class BadRequestError(ValueError):
    """Client request is missing a required field or has an invalid type."""


class ServiceUnavailableError(RuntimeError):
    """Service is not ready or is draining."""


class RequestGate:
    """Atomically stop admission while allowing accepted requests to drain."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._accepting = True
        self._in_flight = 0

    def try_enter(self) -> bool:
        with self._condition:
            if not self._accepting:
                return False
            self._in_flight += 1
            return True

    def leave(self) -> None:
        with self._condition:
            if self._in_flight <= 0:
                raise RuntimeError("request gate underflow")
            self._in_flight -= 1
            if self._in_flight == 0:
                self._condition.notify_all()

    def begin_drain(self) -> None:
        with self._condition:
            self._accepting = False
            if self._in_flight == 0:
                self._condition.notify_all()

    def wait_for_zero(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def snapshot(self) -> tuple[bool, int]:
        with self._condition:
            return self._accepting, self._in_flight


@dataclass(frozen=True)
class ReadinessSnapshot:
    application_initialized: bool
    retriever_initialized: bool
    inference_endpoint_reachable: bool
    model_ready: bool
    accepting_requests: bool
    checked_at_utc: str
    detail: str

    @property
    def ready(self) -> bool:
        return all(
            (
                self.application_initialized,
                self.retriever_initialized,
                self.inference_endpoint_reachable,
                self.model_ready,
                self.accepting_requests,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "checks": {
                "application_initialized": self.application_initialized,
                "retriever_initialized": self.retriever_initialized,
                "inference_endpoint_reachable": self.inference_endpoint_reachable,
                "model_ready": self.model_ready,
                "accepting_requests": self.accepting_requests,
            },
            "checked_at_utc": self.checked_at_utc,
            "detail": self.detail,
        }


class RequestLease:
    """Keeps admission and metrics open until the HTTP response is delivered."""

    def __init__(self, runtime: "ServiceRuntime") -> None:
        self._runtime = runtime
        self._started = time.perf_counter()
        self._finished = False

    def finish(self, *, success: bool) -> None:
        if self._finished:
            return
        self._finished = True
        self._runtime.metrics.request_finished(
            time.perf_counter() - self._started,
            success=success,
        )
        self._runtime._gate.leave()  # noqa: SLF001 - lease is runtime-owned


class ServiceRuntime:
    def __init__(
        self,
        config: ServiceConfig,
        inference: InferenceBackend | None = None,
        metrics: MetricRegistry | None = None,
    ) -> None:
        self.config = config
        try:
            self.inference = inference or OpenAIInferenceClient(
                config.inference_base_url,
                config.inference_timeout_seconds,
                config.inference_api_key_file,
            )
        except OSError as exc:
            raise StartupValidationError(
                f"cannot read inference API key file: {exc}"
            ) from exc
        self.metrics = metrics or MetricRegistry()
        self.retriever: Retriever | None = None
        self._gate = RequestGate()
        self._state_lock = threading.Lock()
        self._application_initialized = False
        self._retriever_initialized = False
        self._inference_reachable = False
        self._model_ready = False
        self._generation_failure_latched = False
        self._readiness_detail = "not initialized"
        self._readiness_checked_at = datetime.now(timezone.utc).isoformat()
        self._stop_event = threading.Event()
        self._readiness_thread: threading.Thread | None = None

    def initialize(self) -> None:
        self.config.validate_static()
        try:
            # Reuse the evaluation prompt builder as the service adapter and
            # reject an unknown prompt revision before accepting traffic.
            build_messages("startup validation", [], self.config.prompt_revision)
        except ValueError as exc:
            raise StartupValidationError(f"invalid prompt configuration: {exc}") from exc
        try:
            index_metadata = json.loads(
                self.config.index_path.read_text(encoding="utf-8")
            )
            if not isinstance(index_metadata, dict):
                raise ValueError("index root must be an object")
            index_corpus_version = index_metadata.get("corpus_version")
            if index_corpus_version != self.config.corpus_dir.name:
                raise ValueError(
                    "index corpus_version does not match configured corpus directory: "
                    f"{index_corpus_version!r} != {self.config.corpus_dir.name!r}"
                )
            retriever = Retriever.from_index_file(self.config.index_path)
        except (OSError, ValueError, KeyError) as exc:
            raise StartupValidationError(f"cannot initialize retriever: {exc}") from exc
        if not retriever.chunks:
            raise StartupValidationError("retriever index contains no chunks")
        retriever_hash = retriever.config_hash()
        expected_hash = self.config.expected_retriever_config_hash
        if expected_hash is not None and retriever_hash != expected_hash:
            raise StartupValidationError(
                "retriever config hash does not match configured baseline: "
                f"{retriever_hash} != {expected_hash}"
            )
        self.retriever = retriever
        self.metrics.set_build_info(
            {
                "git_sha": os.environ.get("FINLLM_GIT_SHA", "NOT_PROVIDED"),
                "image_digest": os.environ.get(
                    "FINLLM_IMAGE_DIGEST", "NOT_PROVIDED"
                ),
                "model_id": self.config.model_id,
                "model_revision": self.config.model_revision,
                "tokenizer_revision": self.config.tokenizer_revision,
                "prompt_revision": self.config.prompt_revision,
                "corpus_version": self.config.corpus_dir.name,
                "eval_set_version": os.environ.get(
                    "FINLLM_EVAL_SET_VERSION", "eval-v0.1"
                ),
                "retriever_config_hash": retriever_hash,
            }
        )
        with self._state_lock:
            self._retriever_initialized = True
            self._application_initialized = True

        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while True:
            snapshot = self.refresh_inference_readiness()
            if snapshot.inference_endpoint_reachable and snapshot.model_ready:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StartupValidationError(
                    "inference startup validation failed: " + snapshot.detail
                )
            self._stop_event.wait(
                min(self.config.readiness_interval_seconds, remaining)
            )
        self._readiness_thread = threading.Thread(
            target=self._readiness_loop,
            name="finllm-readiness",
            daemon=True,
        )
        self._readiness_thread.start()

    def _readiness_loop(self) -> None:
        while not self._stop_event.wait(self.config.readiness_interval_seconds):
            self.refresh_inference_readiness()

    def refresh_inference_readiness(self) -> ReadinessSnapshot:
        try:
            reachable, model_ready, detail = self.inference.probe_model(
                self.config.model_id
            )
        except Exception as exc:  # dependency probes must not kill the loop
            reachable = False
            model_ready = False
            detail = f"probe failed: {type(exc).__name__}"
        with self._state_lock:
            generation_failure_latched = self._generation_failure_latched
        if reachable and model_ready and generation_failure_latched:
            try:
                generation_ready, generation_detail = self.inference.probe_generation(
                    self.config.model_id
                )
            except Exception as exc:  # noqa: BLE001
                generation_ready = False
                generation_detail = f"generation probe failed: {type(exc).__name__}"
            model_ready = generation_ready
            detail = generation_detail
        with self._state_lock:
            self._inference_reachable = reachable
            self._model_ready = model_ready
            if model_ready:
                self._generation_failure_latched = False
            self._readiness_detail = detail
            self._readiness_checked_at = datetime.now(timezone.utc).isoformat()
        return self.readiness()

    def readiness(self) -> ReadinessSnapshot:
        accepting, _ = self._gate.snapshot()
        with self._state_lock:
            snapshot = ReadinessSnapshot(
                application_initialized=self._application_initialized,
                retriever_initialized=self._retriever_initialized,
                inference_endpoint_reachable=self._inference_reachable,
                model_ready=self._model_ready,
                accepting_requests=accepting,
                checked_at_utc=self._readiness_checked_at,
                detail=self._readiness_detail,
            )
        self.metrics.set_ready(snapshot.ready)
        return snapshot

    def health(self) -> dict[str, str]:
        return {"status": "alive"}

    def admit_rag_request(self, payload: Any) -> RequestLease:
        if not isinstance(payload, dict):
            self.metrics.request_rejected()
            raise BadRequestError("request body must be a JSON object")
        question = payload.get("question")
        role = payload.get("role")
        if not isinstance(question, str) or not question.strip():
            self.metrics.request_rejected()
            raise BadRequestError("question must be a non-empty string")
        if not isinstance(role, str) or not role.strip():
            self.metrics.request_rejected()
            raise BadRequestError("role must be a non-empty string")
        if not self.readiness().ready:
            self.metrics.request_rejected()
            raise ServiceUnavailableError("service is not ready")
        if not self._gate.try_enter():
            self.metrics.request_rejected()
            raise ServiceUnavailableError("service is draining")

        self.metrics.request_started()
        return RequestLease(self)

    def execute_rag_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = payload["question"].strip()
        role = payload["role"].strip()
        retriever = self.retriever
        if retriever is None:
            raise ServiceUnavailableError("retriever is not initialized")
        retrieval_started = time.perf_counter()
        try:
            hits = retriever.search(
                question, role, top_k=self.config.retrieval_top_k
            )
        finally:
            self.metrics.observe_retrieval(time.perf_counter() - retrieval_started)

        messages = build_messages(question, hits, self.config.prompt_revision)
        generation_started = time.perf_counter()
        try:
            answer = self.inference.generate(
                self.config.model_id,
                messages,
                self.config.generation.as_payload(),
            )
        except Exception:
            with self._state_lock:
                self._generation_failure_latched = True
                self._model_ready = False
                self._readiness_detail = "generation path failed; recovery probe pending"
                self._readiness_checked_at = datetime.now(timezone.utc).isoformat()
            self.metrics.set_ready(False)
            raise
        else:
            with self._state_lock:
                self._generation_failure_latched = False
        finally:
            self.metrics.observe_generation(time.perf_counter() - generation_started)

        return {
            "request_id": str(uuid.uuid4()),
            "answer": answer,
            "retrieval": {
                "top_k": self.config.retrieval_top_k,
                "hit_count": len(hits),
                "chunk_ids": [hit["chunk"]["chunk_id"] for hit in hits],
                "retriever_config_hash": retriever.config_hash(),
            },
            "provenance": {
                "model_id": self.config.model_id,
                "model_revision": self.config.model_revision,
                "tokenizer_revision": self.config.tokenizer_revision,
                "prompt_revision": self.config.prompt_revision,
                "corpus_version": self.config.corpus_dir.name,
            },
        }

    def handle_rag_request(self, payload: Any) -> dict[str, Any]:
        """In-process adapter used by evaluation/unit tests.

        The HTTP handler uses the explicit lease methods so completion includes
        response delivery. Direct callers have no transport and therefore
        complete when the response object has been produced.
        """

        lease = self.admit_rag_request(payload)
        success = False
        try:
            response = self.execute_rag_request(payload)
            success = True
            return response
        finally:
            lease.finish(success=success)

    def begin_shutdown(self) -> None:
        self._gate.begin_drain()
        self._stop_event.set()
        self.metrics.set_shutdown_in_progress(True)
        self.metrics.set_ready(False)
        with self._state_lock:
            self._readiness_detail = "service is draining"
            self._readiness_checked_at = datetime.now(timezone.utc).isoformat()

    def wait_for_drain(self) -> bool:
        return self._gate.wait_for_zero(self.config.shutdown_timeout_seconds)

    def close(self) -> None:
        self._stop_event.set()
        if self._readiness_thread and self._readiness_thread.is_alive():
            self._readiness_thread.join(timeout=1.0)
        with self._state_lock:
            self._application_initialized = False
            self._inference_reachable = False
            self._model_ready = False
            self._readiness_detail = "service stopped"
        self.metrics.set_ready(False)
