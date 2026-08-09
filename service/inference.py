"""Small OpenAI-compatible inference adapter used by the service runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class InferenceError(RuntimeError):
    """Inference dependency returned an unusable response."""


class InferenceBackend(Protocol):
    def probe_model(self, model_id: str) -> tuple[bool, bool, str]: ...

    def probe_generation(self, model_id: str) -> tuple[bool, str]: ...

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        generation: dict[str, Any],
    ) -> str: ...


class OpenAIInferenceClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        api_key_file: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.api_key = (
            api_key_file.read_text(encoding="utf-8").strip()
            if api_key_file is not None
            else None
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "finllm-service/0.2",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def probe_model(self, model_id: str) -> tuple[bool, bool, str]:
        request = Request(f"{self.base_url}/models", headers=self._headers())
        try:
            with urlopen(request, timeout=min(self.timeout_seconds, 10.0)) as response:
                raw = response.read()
        except HTTPError as exc:
            return True, False, f"inference /models returned HTTP {exc.code}"
        except (
            URLError,
            TimeoutError,
        ) as exc:
            return False, False, f"{type(exc).__name__}: {exc}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return True, False, f"invalid inference /models JSON: {type(exc).__name__}"
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return True, False, "inference /models response has an invalid schema"
        model_ids = {
            item.get("id")
            for item in payload.get("data", [])
            if isinstance(item, dict)
        }
        if model_id not in model_ids:
            return True, False, f"configured model {model_id!r} is not listed"
        return True, True, "model listed by inference endpoint"

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        generation: dict[str, Any],
    ) -> str:
        payload = {"model": model_id, "messages": messages, **generation}
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
        except (
            HTTPError,
            URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise InferenceError(f"inference request failed: {type(exc).__name__}") from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceError("inference response does not match the chat contract") from exc
        if not isinstance(content, str):
            raise InferenceError("inference response content is not a string")
        return content

    def probe_generation(self, model_id: str) -> tuple[bool, str]:
        """Probe the engine path only after a real generation failure latched.

        `/models` is served by the API process and can stay healthy after the
        execution engine has failed. This one-token request is therefore used
        only while recovering from a failure, never on the normal readiness
        scrape path.
        """

        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "health"}],
            "temperature": 0,
            "max_tokens": 1,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=min(self.timeout_seconds, 10.0)) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
        except (
            HTTPError,
            URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            return False, f"generation health probe failed: {type(exc).__name__}"
        if not isinstance(content, str):
            return False, "generation health probe returned non-string content"
        return True, "generation engine probe succeeded"
