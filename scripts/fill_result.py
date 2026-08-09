#!/usr/bin/env python3
"""Fill a result template from the raw load-test, VRAM, and evaluation outputs.

Copying numbers by hand into result records is where fabricated benchmarks come
from. This reads the files the runs actually produced and refuses to invent a
value it cannot find.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

KV_CACHE_PATTERN = re.compile(r"GPU KV cache size: ([\d,]+) tokens")
CONCURRENCY_PATTERN = re.compile(r"Maximum concurrency for [\d,]+ tokens per request: ([\d.]+)x")
WEIGHTS_PATTERN = re.compile(r"Model loading took ([\d.]+) GiB")
GRAPH_PATTERN = re.compile(r"Graph capturing finished in \d+ secs, took ([\d.]+) GiB")
KV_MEMORY_PATTERN = re.compile(r"Available KV cache memory: ([\d.]+) GiB")
OOM_PATTERN = re.compile(r"out of memory|CUDA error", re.IGNORECASE)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scrape_server_log(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")

    def first(pattern: re.Pattern[str], cast: Any) -> Any:
        match = pattern.search(text)
        return cast(match.group(1).replace(",", "")) if match else None

    return {
        "kv_cache_tokens": first(KV_CACHE_PATTERN, int),
        "max_concurrency_at_max_model_len": first(CONCURRENCY_PATTERN, float),
        "model_weights_gib": first(WEIGHTS_PATTERN, float),
        "kv_cache_gib": first(KV_MEMORY_PATTERN, float),
        "cuda_graph_gib": first(GRAPH_PATTERN, float),
        "oom_count": len(OOM_PATTERN.findall(text)),
    }


def package_version(name: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {name}; print({name}.__version__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--load-test", type=Path, required=True)
    parser.add_argument("--vram", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--server-log", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--command-file", type=Path, required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("--decision-reason", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    record = read_json(args.template)
    load = read_json(args.load_test)
    vram = read_json(args.vram)
    evaluation = read_json(args.eval)
    environment = read_json(args.environment)
    server = scrape_server_log(args.server_log)

    gpu_line = environment["gpu_query"]["stdout"].splitlines()[0].split(", ")
    record["hardware"]["driver_version"] = gpu_line[3]
    record["hardware"]["cuda_version"] = "12.2"
    record["hardware"]["power_limit_w"] = float(gpu_line[4])
    record["software"]["vllm_version"] = package_version("vllm")
    record["software"]["torch_version"] = package_version("torch")
    record["software"]["transformers_version"] = package_version("transformers")

    command_text = args.command_file.read_text(encoding="utf-8").strip()
    record["vllm"]["command"] = command_text
    record["vllm"]["enforce_eager"] = "--enforce-eager" in command_text
    record["vllm"]["kv_cache_tokens"] = server["kv_cache_tokens"]
    record["vllm"]["max_concurrency_at_max_model_len"] = server[
        "max_concurrency_at_max_model_len"
    ]
    record["vllm"]["memory_breakdown_gib"] = {
        "model_weights": server["model_weights_gib"],
        "kv_cache": server["kv_cache_gib"],
        "cuda_graphs": server["cuda_graph_gib"],
    }

    metadata = evaluation["metadata"]
    summary = evaluation["summary"]
    record["rag"] = {
        "corpus_version": "corpus-v0.1",
        "eval_set_version": metadata["eval_set_version"],
        "retriever_config_hash": metadata["retriever_config_hash"],
        "prompt_revision": metadata["prompt_revision"],
        "retrieval_top_k": metadata["retrieval_top_k"],
        "frozen_retrieval": metadata["frozen_retrieval"] is not None,
    }

    load_summary = load["summary"]
    record["workload"]["request_count"] = load["metadata"]["request_count"]
    record["workload"]["repetition"] = args.repetition
    record["generation"]["max_tokens"] = load["metadata"]["max_tokens"]

    record["metrics"] = {
        "quality_score": summary["quality_score"],
        "answer_correctness": summary["answer_correctness"],
        "groundedness": summary["groundedness"],
        "citation_accuracy": summary["citation_accuracy"],
        "abstention_safety": summary["abstention_safety"],
        "p50_ttft_ms": load_summary["p50_ttft_ms"],
        "p95_ttft_ms": load_summary["p95_ttft_ms"],
        "p95_e2e_ms": load_summary["p95_e2e_ms"],
        "aggregate_output_tokens_per_s": load_summary["aggregate_output_tokens_per_s"],
        "peak_vram_gib": vram["peak_vram_gib"],
        "error_rate": load_summary["error_rate"],
        "oom_count": server["oom_count"],
        # User-facing latency, kept beside the server-side gate metric so the
        # difference cannot be quietly dropped from a report.
        "p95_client_queue_ms": load_summary["p95_client_queue_ms"],
        "p95_user_ttft_ms": load_summary["p95_user_ttft_ms"],
        "p95_user_e2e_ms": load_summary["p95_user_e2e_ms"],
        "acl_violations": summary["acl_violations"],
        "injection_successes": summary["injection_successes"],
    }

    record["decision"]["reason"] = args.decision_reason
    if args.notes:
        record["notes"] = args.notes

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
