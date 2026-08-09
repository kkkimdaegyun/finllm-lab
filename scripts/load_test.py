#!/usr/bin/env python3
"""Small OpenAI-compatible streaming load test focused on TTFT."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # Allow --help and static checks before install.
    httpx = None  # type: ignore[assignment]


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile_value
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if "messages" not in row:
                raise ValueError(f"{path}:{line_number}: missing messages")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: dataset is empty")
    return rows


async def send_one(
    client: Any,
    semaphore: asyncio.Semaphore,
    url: str,
    api_key: str,
    model: str,
    row: dict[str, Any],
    max_tokens: int,
    request_id: int,
    enable_thinking: bool,
    temperature: float,
    top_p: float,
    top_k: int,
    min_p: float,
    seed: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": row["messages"],
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "seed": seed,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    started = 0.0
    first_token_at: float | None = None
    completion_tokens = 0
    chunks = 0
    error: str | None = None

    # The request "arrives" when the caller is ready to send it. Time spent
    # waiting for a client concurrency slot is queueing the user really feels,
    # so it is measured separately instead of being silently excluded.
    arrived = time.perf_counter()
    async with semaphore:
        started = time.perf_counter()
        try:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    usage = event.get("usage")
                    if usage and usage.get("completion_tokens") is not None:
                        completion_tokens = int(usage["completion_tokens"])
                    choices = event.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            chunks += 1
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
        except Exception as exc:  # noqa: BLE001 - preserve per-request failure
            error = f"{type(exc).__name__}: {exc}"
        ended = time.perf_counter()

    if completion_tokens == 0:
        completion_tokens = chunks
    return {
        "request_id": request_id,
        "case_id": row.get("id", str(request_id)),
        "client_queue_ms": round((started - arrived) * 1000, 3),
        "ttft_ms": (
            round((first_token_at - started) * 1000, 3)
            if first_token_at is not None
            else None
        ),
        "user_ttft_ms": (
            round((first_token_at - arrived) * 1000, 3)
            if first_token_at is not None
            else None
        ),
        "e2e_ms": round((ended - started) * 1000, 3),
        "user_e2e_ms": round((ended - arrived) * 1000, 3),
        "completion_tokens": completion_tokens,
        "error": error or (None if first_token_at is not None else "no output token"),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if httpx is None:
        raise RuntimeError(
            "httpx is required for load testing. Run: python3 -m pip install -e ."
        )
    dataset = load_dataset(args.dataset)
    rows = [dataset[index % len(dataset)] for index in range(args.requests)]
    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.timeout)
    url = f"{args.base_url.rstrip('/')}/chat/completions"
    wall_started = time.perf_counter()
    async with httpx.AsyncClient(timeout=timeout) as client:
        samples = await asyncio.gather(
            *[
                send_one(
                    client,
                    semaphore,
                    url,
                    args.api_key,
                    args.model,
                    row,
                    args.max_tokens,
                    index,
                    args.enable_thinking,
                    args.temperature,
                    args.top_p,
                    args.top_k,
                    args.min_p,
                    args.seed,
                )
                for index, row in enumerate(rows)
            ]
        )
    wall_seconds = time.perf_counter() - wall_started
    successful = [sample for sample in samples if sample["error"] is None]
    ttfts = [sample["ttft_ms"] for sample in successful]
    user_ttfts = [sample["user_ttft_ms"] for sample in successful]
    queues = [sample["client_queue_ms"] for sample in successful]
    e2es = [sample["e2e_ms"] for sample in successful]
    user_e2es = [sample["user_e2e_ms"] for sample in successful]
    total_tokens = sum(sample["completion_tokens"] for sample in successful)
    return {
        "metadata": {
            "base_url": args.base_url,
            "model": args.model,
            "dataset": str(args.dataset),
            "concurrency": args.concurrency,
            "request_count": args.requests,
            "max_tokens": args.max_tokens,
            "enable_thinking": args.enable_thinking,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "top_k": args.top_k,
            "min_p": args.min_p,
            "seed": args.seed,
            "streaming": True,
            "arrival_model": (
                "closed burst: all requests are offered at t=0 and admitted "
                f"{args.concurrency} at a time"
            ),
            "ttft_definition": (
                "ttft_ms measures dispatch to first token (server-side). "
                "user_ttft_ms adds client_queue_ms, the wait for a concurrency "
                "slot, and is what a user in the burst actually experiences."
            ),
        },
        "summary": {
            "successful_requests": len(successful),
            "failed_requests": len(samples) - len(successful),
            "error_rate": round((len(samples) - len(successful)) / len(samples), 6),
            "p50_ttft_ms": round(statistics.median(ttfts), 3) if ttfts else None,
            "p95_ttft_ms": round(percentile(ttfts, 0.95), 3) if ttfts else None,
            "p95_client_queue_ms": (
                round(percentile(queues, 0.95), 3) if queues else None
            ),
            "p50_user_ttft_ms": (
                round(statistics.median(user_ttfts), 3) if user_ttfts else None
            ),
            "p95_user_ttft_ms": (
                round(percentile(user_ttfts, 0.95), 3) if user_ttfts else None
            ),
            "p95_e2e_ms": round(percentile(e2es, 0.95), 3) if e2es else None,
            "p95_user_e2e_ms": (
                round(percentile(user_e2es, 0.95), 3) if user_e2es else None
            ),
            "aggregate_output_tokens_per_s": round(total_tokens / wall_seconds, 3),
            "wall_seconds": round(wall_seconds, 3),
        },
        "samples": samples,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--api-key", default="local-token")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Enable model thinking mode; disabled by default for RAG latency runs",
    )
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--min-p", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.concurrency < 1 or args.requests < 1:
        raise SystemExit("concurrency and requests must be positive")
    if httpx is None:
        raise SystemExit(
            "httpx is required for load testing. Run: python3 -m pip install -e ."
        )
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
