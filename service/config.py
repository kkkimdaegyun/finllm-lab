"""Configuration loading and fail-fast startup validation."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


class StartupValidationError(RuntimeError):
    """Raised when the service cannot safely start with the supplied config."""


REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _required(mapping: Mapping[str, Any], key: str, section: str) -> Any:
    if key not in mapping:
        raise StartupValidationError(f"missing config field: {section}.{key}")
    return mapping[key]


def _section(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = _required(payload, key, "<root>")
    if not isinstance(value, Mapping):
        raise StartupValidationError(f"config section {key} must be an object")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise StartupValidationError(f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StartupValidationError(f"{field} must be a number") from exc
    if not math.isfinite(number):
        raise StartupValidationError(f"{field} must be finite")
    return number


def _positive(value: Any, field: str, *, allow_zero: bool = False) -> float:
    number = _number(value, field)
    invalid = number < 0 if allow_zero else number <= 0
    if invalid:
        qualifier = "non-negative" if allow_zero else "positive"
        raise StartupValidationError(f"{field} must be {qualifier}")
    return number


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise StartupValidationError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    raise StartupValidationError(f"{field} must be an integer")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StartupValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _path(value: Any, field: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise StartupValidationError(f"{field} must be a non-empty path")
    return Path(value)


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise StartupValidationError(f"{field} must be a boolean")
    return value


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    seed: int
    max_tokens: int
    thinking_mode: bool

    def as_payload(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.thinking_mode},
        }


@dataclass(frozen=True)
class ServiceConfig:
    source_path: Path
    host: str
    port: int
    max_request_bytes: int
    listen_backlog: int
    corpus_dir: Path
    index_path: Path
    retrieval_top_k: int
    prompt_revision: str
    expected_retriever_config_hash: str | None
    inference_base_url: str
    model_id: str
    model_revision: str
    tokenizer_revision: str
    inference_timeout_seconds: float
    inference_api_key_file: Path | None
    startup_timeout_seconds: float
    readiness_interval_seconds: float
    shutdown_timeout_seconds: float
    generation: GenerationConfig

    @classmethod
    def load(
        cls,
        path: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "ServiceConfig":
        path = Path(path)
        if not path.is_file():
            raise StartupValidationError(f"config file does not exist: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StartupValidationError(f"cannot read config file {path}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise StartupValidationError("config root must be a JSON object")
        if payload.get("schema_version") != "1.0.0":
            raise StartupValidationError("config schema_version must be '1.0.0'")

        env = os.environ if environ is None else environ
        service = _section(payload, "service")
        rag = _section(payload, "rag")
        inference = _section(payload, "inference")
        lifecycle = _section(payload, "lifecycle")
        generation = _section(payload, "generation")

        def pick(name: str, default: Any) -> Any:
            return env[name] if name in env else default

        api_key_value = pick(
            "FINLLM_INFERENCE_API_KEY_FILE",
            inference.get("api_key_file"),
        )
        api_key_path = (
            None
            if api_key_value is None or api_key_value == ""
            else _path(api_key_value, "inference.api_key_file")
        )
        config = cls(
            source_path=path.resolve(),
            host=_string(
                pick("FINLLM_HOST", _required(service, "host", "service")),
                "service.host",
            ),
            port=_integer(
                pick("FINLLM_PORT", _required(service, "port", "service")),
                "service.port",
            ),
            max_request_bytes=_integer(
                pick(
                    "FINLLM_MAX_REQUEST_BYTES",
                    _required(service, "max_request_bytes", "service"),
                ),
                "service.max_request_bytes",
            ),
            listen_backlog=_integer(
                pick("FINLLM_LISTEN_BACKLOG", service.get("listen_backlog", 128)),
                "service.listen_backlog",
            ),
            corpus_dir=_path(
                pick("FINLLM_CORPUS_DIR", _required(rag, "corpus_dir", "rag")),
                "rag.corpus_dir",
            ),
            index_path=_path(
                pick("FINLLM_INDEX_PATH", _required(rag, "index_path", "rag")),
                "rag.index_path",
            ),
            retrieval_top_k=_integer(
                pick(
                    "FINLLM_RETRIEVAL_TOP_K",
                    _required(rag, "retrieval_top_k", "rag"),
                ),
                "rag.retrieval_top_k",
            ),
            prompt_revision=_string(
                pick(
                    "FINLLM_PROMPT_REVISION",
                    _required(rag, "prompt_revision", "rag"),
                ),
                "rag.prompt_revision",
            ),
            expected_retriever_config_hash=(
                None
                if pick(
                    "FINLLM_EXPECTED_RETRIEVER_CONFIG_HASH",
                    rag.get("retriever_config_hash"),
                ) in {None, ""}
                else _string(
                    pick(
                        "FINLLM_EXPECTED_RETRIEVER_CONFIG_HASH",
                        rag.get("retriever_config_hash"),
                    ),
                    "rag.retriever_config_hash",
                )
            ),
            inference_base_url=_string(
                pick(
                    "FINLLM_INFERENCE_BASE_URL",
                    _required(inference, "base_url", "inference"),
                ),
                "inference.base_url",
            ),
            model_id=_string(
                pick(
                    "FINLLM_MODEL_ID",
                    _required(inference, "model_id", "inference"),
                ),
                "inference.model_id",
            ),
            model_revision=_string(
                pick(
                    "FINLLM_MODEL_REVISION",
                    _required(inference, "model_revision", "inference"),
                ),
                "inference.model_revision",
            ),
            tokenizer_revision=_string(
                pick(
                    "FINLLM_TOKENIZER_REVISION",
                    _required(inference, "tokenizer_revision", "inference"),
                ),
                "inference.tokenizer_revision",
            ),
            inference_timeout_seconds=_positive(
                pick(
                    "FINLLM_INFERENCE_TIMEOUT_SECONDS",
                    _required(inference, "timeout_seconds", "inference"),
                ),
                "inference.timeout_seconds",
            ),
            inference_api_key_file=api_key_path,
            startup_timeout_seconds=_positive(
                pick(
                    "FINLLM_STARTUP_TIMEOUT_SECONDS",
                    _required(lifecycle, "startup_timeout_seconds", "lifecycle"),
                ),
                "lifecycle.startup_timeout_seconds",
                allow_zero=True,
            ),
            readiness_interval_seconds=_positive(
                pick(
                    "FINLLM_READINESS_INTERVAL_SECONDS",
                    _required(lifecycle, "readiness_interval_seconds", "lifecycle"),
                ),
                "lifecycle.readiness_interval_seconds",
            ),
            shutdown_timeout_seconds=_positive(
                pick(
                    "FINLLM_SHUTDOWN_TIMEOUT_SECONDS",
                    _required(lifecycle, "shutdown_timeout_seconds", "lifecycle"),
                ),
                "lifecycle.shutdown_timeout_seconds",
                allow_zero=True,
            ),
            generation=GenerationConfig(
                temperature=_number(
                    pick(
                        "FINLLM_GENERATION_TEMPERATURE",
                        _required(generation, "temperature", "generation"),
                    ),
                    "generation.temperature",
                ),
                top_p=_number(
                    pick(
                        "FINLLM_GENERATION_TOP_P",
                        _required(generation, "top_p", "generation"),
                    ),
                    "generation.top_p",
                ),
                top_k=_integer(
                    pick(
                        "FINLLM_GENERATION_TOP_K",
                        _required(generation, "top_k", "generation"),
                    ),
                    "generation.top_k",
                ),
                min_p=_number(
                    pick(
                        "FINLLM_GENERATION_MIN_P",
                        _required(generation, "min_p", "generation"),
                    ),
                    "generation.min_p",
                ),
                seed=_integer(
                    pick(
                        "FINLLM_GENERATION_SEED",
                        _required(generation, "seed", "generation"),
                    ),
                    "generation.seed",
                ),
                max_tokens=_integer(
                    pick(
                        "FINLLM_GENERATION_MAX_TOKENS",
                        _required(generation, "max_tokens", "generation"),
                    ),
                    "generation.max_tokens",
                ),
                thinking_mode=_boolean(
                    _required(generation, "thinking_mode", "generation"),
                    "generation.thinking_mode",
                ),
            ),
        )
        config.validate_static()
        return config

    def validate_static(self) -> None:
        errors: list[str] = []
        if not self.host.strip():
            errors.append("service.host must not be empty")
        if not 1 <= self.port <= 65535:
            errors.append("service.port must be between 1 and 65535")
        if self.max_request_bytes <= 0:
            errors.append("service.max_request_bytes must be positive")
        if self.listen_backlog < 10:
            errors.append("service.listen_backlog must be at least design concurrency 10")
        if not self.corpus_dir.is_dir():
            errors.append(f"corpus directory does not exist: {self.corpus_dir}")
        if not self.index_path.is_file():
            errors.append(f"retriever index does not exist: {self.index_path}")
        if self.retrieval_top_k <= 0:
            errors.append("rag.retrieval_top_k must be positive")
        if not self.prompt_revision.strip():
            errors.append("rag.prompt_revision must not be empty")
        if self.expected_retriever_config_hash is not None and not re.fullmatch(
            r"[0-9a-f]{12}", self.expected_retriever_config_hash
        ):
            errors.append("rag.retriever_config_hash must be a 12-character hex digest")
        parsed = urlparse(self.inference_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("inference.base_url must be an absolute http(s) URL")
        if not self.model_id.strip():
            errors.append("inference.model_id must not be empty")
        if not REVISION_PATTERN.fullmatch(self.model_revision):
            errors.append("inference.model_revision must be a 40-character commit SHA")
        if not REVISION_PATTERN.fullmatch(self.tokenizer_revision):
            errors.append(
                "inference.tokenizer_revision must be a 40-character commit SHA"
            )
        if self.inference_api_key_file and not self.inference_api_key_file.is_file():
            errors.append(
                "inference API key file does not exist: "
                f"{self.inference_api_key_file}"
            )
        if not 0 <= self.generation.temperature:
            errors.append("generation.temperature must be non-negative")
        if not 0 <= self.generation.top_p <= 1:
            errors.append("generation.top_p must be between 0 and 1")
        if not 0 <= self.generation.min_p <= 1:
            errors.append("generation.min_p must be between 0 and 1")
        if self.generation.top_k < 0:
            errors.append("generation.top_k must be non-negative")
        if self.generation.max_tokens <= 0:
            errors.append("generation.max_tokens must be positive")
        if errors:
            raise StartupValidationError("; ".join(errors))
