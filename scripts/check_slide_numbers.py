#!/usr/bin/env python3
"""Check that every figure on the deck matches the recorded measurements.

A slide is the artifact most likely to be read without the repository next to
it, so a stale number there is the most expensive kind. This compares the deck
data against results/ and fails loudly on any mismatch.
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAG = "2026-08-08c"


def load_measurements() -> dict[tuple[str, str], dict[str, float]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for path in glob.glob(str(ROOT / "results" / f"{TAG}-profile-a-*.json")):
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        key = (record["model"]["id"], record["vllm"]["memory_budget_mode"])
        grouped.setdefault(key, []).append(record)

    measurements = {}
    for key, records in grouped.items():
        metrics = [r["metrics"] for r in records]
        measurements[key] = {
            "quality": metrics[0]["quality_score"],
            "p95_ttft": max(m["p95_ttft_ms"] for m in metrics),
            "p95_user_ttft": max(m["p95_user_ttft_ms"] for m in metrics),
            "tok_s": statistics.fmean(m["aggregate_output_tokens_per_s"] for m in metrics),
            "peak_vram": max(m["peak_vram_gib"] for m in metrics),
            "concurrency": records[0]["vllm"]["max_concurrency_at_max_model_len"],
            "injection": metrics[0]["injection_successes"],
            "acl": metrics[0]["acl_violations"],
        }
    return measurements


def main() -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_slides

    measured = load_measurements()
    if not measured:
        print(f"no result records for tag {TAG}", file=sys.stderr)
        return 1

    eight = measured[("Qwen/Qwen3-8B", "class-ceiling")]
    awq_ceiling = measured[("Qwen/Qwen3-14B-AWQ", "class-ceiling")]
    awq_matched = measured[("Qwen/Qwen3-14B-AWQ", "deployment-matched")]

    # (label, slide text, measured value, formatter)
    checks = [
        ("8B quality", "95.9", eight["quality"], lambda v: f"{v:.1f}"),
        ("8B p95 ttft", "81ms", eight["p95_ttft"], lambda v: f"{v:.0f}ms"),
        ("8B p95 user ttft", "1,349ms", eight["p95_user_ttft"], lambda v: f"{v:,.0f}ms"),
        ("8B tok/s", "287.0", eight["tok_s"], lambda v: f"{v:.1f}"),
        ("8B peak vram", "24.01GiB", eight["peak_vram"], lambda v: f"{v:.2f}GiB"),
        ("8B concurrency", "7.31", eight["concurrency"], lambda v: f"{v:.2f}"),
        ("14B ceiling quality", "97.7", awq_ceiling["quality"], lambda v: f"{v:.1f}"),
        ("14B ceiling tok/s", "313.2", awq_ceiling["tok_s"], lambda v: f"{v:.1f}"),
        ("14B ceiling peak", "23.84GiB", awq_ceiling["peak_vram"], lambda v: f"{v:.2f}GiB"),
        ("14B ceiling conc", "11.05", awq_ceiling["concurrency"], lambda v: f"{v:.2f}"),
        ("14B matched quality", "97.7", awq_matched["quality"], lambda v: f"{v:.1f}"),
        ("14B matched tok/s", "315.3", awq_matched["tok_s"], lambda v: f"{v:.1f}"),
        ("14B matched user ttft", "1,287ms", awq_matched["p95_user_ttft"], lambda v: f"{v:,.0f}ms"),
        ("14B matched peak", "21.96GiB", awq_matched["peak_vram"], lambda v: f"{v:.2f}GiB"),
        ("14B matched conc", "9.53", awq_matched["concurrency"], lambda v: f"{v:.2f}"),
        ("injection 8B", "2 / 5", eight["injection"], lambda v: f"{v:.0f} / 5"),
        ("injection 14B", "2 / 5", awq_matched["injection"], lambda v: f"{v:.0f} / 5"),
        ("acl 8B", "0건", eight["acl"], lambda v: f"{v:.0f}건"),
    ]

    deck_text = " ".join(
        str(value)
        for slide in build_slides.SLIDES
        for value in _walk(slide)
    )

    failures = []
    for label, shown, value, fmt in checks:
        expected = fmt(value)
        if expected != shown:
            failures.append(f"{label}: 슬라이드 '{shown}' != 레코드 '{expected}'")
        elif shown not in deck_text:
            failures.append(f"{label}: '{shown}' 이(가) 슬라이드에 없음")

    for line in failures:
        print(f"MISMATCH: {line}", file=sys.stderr)
    if failures:
        return 1
    print(f"{len(checks)}개 수치가 {TAG} 결과 레코드와 일치합니다.")
    return 0


def _walk(node) -> list:
    if isinstance(node, dict):
        return [v for item in node.values() for v in _walk(item)]
    if isinstance(node, (list, tuple)):
        return [v for item in node for v in _walk(item)]
    return [node]


if __name__ == "__main__":
    raise SystemExit(main())
