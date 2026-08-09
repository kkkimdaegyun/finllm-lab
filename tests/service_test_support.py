"""Shared fixtures for service tests; no GPU or network dependency required."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from scripts.rag_index import load_corpus, save_index
from service.config import ServiceConfig


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "Qwen/Qwen3-14B-AWQ"
MODEL_REVISION = "31c69efc29464b6bb0aee1398b5a7b50a99340c3"


class FakeInference:
    def __init__(
        self,
        *,
        reachable: bool = True,
        model_ready: bool = True,
        answer: str = "테스트 응답",
    ) -> None:
        self.reachable = reachable
        self.model_ready = model_ready
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    def probe_model(self, model_id: str) -> tuple[bool, bool, str]:
        if not self.reachable:
            return False, False, "stub endpoint unavailable"
        if not self.model_ready:
            return True, False, "stub model not ready"
        return True, model_id == MODEL_ID, "stub model ready"

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        generation: dict[str, Any],
    ) -> str:
        self.requests.append(
            {"model": model_id, "messages": messages, "generation": generation}
        )
        return self.answer

    def probe_generation(self, model_id: str) -> tuple[bool, str]:
        ready = self.reachable and self.model_ready and model_id == MODEL_ID
        return ready, "stub generation ready" if ready else "stub generation failed"


class BlockingInference(FakeInference):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        generation: dict[str, Any],
    ) -> str:
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("test did not release the generation call")
        return super().generate(model_id, messages, generation)


def make_config(
    directory: Path,
    *,
    startup_timeout_seconds: float = 0,
    readiness_interval_seconds: float = 3600,
    shutdown_timeout_seconds: float = 1,
    prompt_revision: str = "prompt-v0.1",
) -> ServiceConfig:
    index_path = directory / "index.json"
    save_index(load_corpus(ROOT / "corpus" / "v0.1"), index_path)
    payload = {
        "schema_version": "1.0.0",
        "service": {
            "host": "127.0.0.1",
            "port": 8080,
            "max_request_bytes": 65536,
            "listen_backlog": 128,
        },
        "rag": {
            "corpus_dir": str(ROOT / "corpus" / "v0.1"),
            "index_path": str(index_path),
            "retrieval_top_k": 3,
            "prompt_revision": prompt_revision,
        },
        "inference": {
            "base_url": "http://127.0.0.1:1/v1",
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "tokenizer_revision": MODEL_REVISION,
            "timeout_seconds": 1,
            "api_key_file": None,
        },
        "lifecycle": {
            "startup_timeout_seconds": startup_timeout_seconds,
            "readiness_interval_seconds": readiness_interval_seconds,
            "shutdown_timeout_seconds": shutdown_timeout_seconds,
        },
        "generation": {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "seed": 42,
            "max_tokens": 64,
            "thinking_mode": False,
        },
    }
    config_path = directory / "service.json"
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ServiceConfig.load(config_path, environ={})
