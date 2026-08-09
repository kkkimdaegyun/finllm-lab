"""Low-overhead in-process Prometheus exposition.

No network call is made while updating metrics. Values are changed under one
short lock and rendered only when Prometheus scrapes ``/metrics``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


DEFAULT_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    180.0,
)


def _number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return format(value, ".12g")


def _label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@dataclass
class Histogram:
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    bucket_counts: list[int] = field(init=False)
    count: int = 0
    total: float = 0.0

    def __post_init__(self) -> None:
        self.bucket_counts = [0 for _ in self.buckets]

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        for index, upper_bound in enumerate(self.buckets):
            if value <= upper_bound:
                self.bucket_counts[index] += 1


class MetricRegistry:
    """Metrics contract consumed by the Observability / Reliability part."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_total = 0
        self._request_errors_total = 0
        self._requests_in_flight = 0
        self._request_duration = Histogram()
        self._retrieval_duration = Histogram()
        self._generation_duration = Histogram()
        self._ready = 0
        self._shutdown_in_progress = 0
        self._build_info: dict[str, str] = {}

    def request_started(self) -> None:
        with self._lock:
            self._requests_total += 1
            self._requests_in_flight += 1

    def request_rejected(self) -> None:
        with self._lock:
            self._requests_total += 1
            self._request_errors_total += 1

    def request_finished(self, duration_seconds: float, *, success: bool) -> None:
        with self._lock:
            self._requests_in_flight = max(0, self._requests_in_flight - 1)
            if not success:
                self._request_errors_total += 1
            self._request_duration.observe(duration_seconds)

    def observe_retrieval(self, duration_seconds: float) -> None:
        with self._lock:
            self._retrieval_duration.observe(duration_seconds)

    def observe_generation(self, duration_seconds: float) -> None:
        with self._lock:
            self._generation_duration.observe(duration_seconds)

    def set_ready(self, ready: bool) -> None:
        with self._lock:
            self._ready = 1 if ready else 0

    def set_shutdown_in_progress(self, active: bool) -> None:
        with self._lock:
            self._shutdown_in_progress = 1 if active else 0

    def set_build_info(self, labels: dict[str, str]) -> None:
        with self._lock:
            self._build_info = dict(labels)

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "requests_total": self._requests_total,
                "request_errors_total": self._request_errors_total,
                "requests_in_flight": self._requests_in_flight,
                "request_duration_count": self._request_duration.count,
                "retrieval_duration_count": self._retrieval_duration.count,
                "generation_duration_count": self._generation_duration.count,
                "ready": self._ready,
                "shutdown_in_progress": self._shutdown_in_progress,
            }

    def render_prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP finllm_requests_total Accepted and rejected RAG requests.",
                "# TYPE finllm_requests_total counter",
                f"finllm_requests_total {self._requests_total}",
                "# HELP finllm_request_errors_total RAG requests that failed or were rejected.",
                "# TYPE finllm_request_errors_total counter",
                f"finllm_request_errors_total {self._request_errors_total}",
                "# HELP finllm_requests_in_flight Accepted RAG requests still executing.",
                "# TYPE finllm_requests_in_flight gauge",
                f"finllm_requests_in_flight {self._requests_in_flight}",
                "# HELP finllm_ready Whether the service accepts production traffic.",
                "# TYPE finllm_ready gauge",
                f"finllm_ready {self._ready}",
                "# HELP finllm_shutdown_in_progress Whether graceful drain is active.",
                "# TYPE finllm_shutdown_in_progress gauge",
                f"finllm_shutdown_in_progress {self._shutdown_in_progress}",
            ]
            lines.extend(
                self._render_histogram(
                    "finllm_request_duration_seconds",
                    "End-to-end duration of accepted RAG requests.",
                    self._request_duration,
                )
            )
            lines.extend(
                self._render_histogram(
                    "finllm_retrieval_duration_seconds",
                    "Duration of local ACL-filtered retrieval.",
                    self._retrieval_duration,
                )
            )
            lines.extend(
                self._render_histogram(
                    "finllm_generation_duration_seconds",
                    "Duration of the inference-server generation call.",
                    self._generation_duration,
                )
            )
            lines.extend(
                [
                    "# HELP finllm_build_info Immutable service and model provenance.",
                    "# TYPE finllm_build_info gauge",
                ]
            )
            if self._build_info:
                labels = ",".join(
                    f'{name}="{_label_value(value)}"'
                    for name, value in sorted(self._build_info.items())
                )
                lines.append(f"finllm_build_info{{{labels}}} 1")
            return "\n".join(lines) + "\n"

    @staticmethod
    def _render_histogram(name: str, help_text: str, histogram: Histogram) -> list[str]:
        lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        for upper_bound, count in zip(histogram.buckets, histogram.bucket_counts):
            lines.append(
                f'{name}_bucket{{le="{_number(upper_bound)}"}} {count}'
            )
        lines.append(f'{name}_bucket{{le="+Inf"}} {histogram.count}')
        lines.append(f"{name}_sum {_number(histogram.total)}")
        lines.append(f"{name}_count {histogram.count}")
        return lines
