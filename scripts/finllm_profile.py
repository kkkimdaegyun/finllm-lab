#!/usr/bin/env python3
"""Manage FinLLM Lab deployment profiles and result records."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "profiles.json"
CANDIDATES_PATH = ROOT / "configs" / "model-candidates.json"
SCHEMA_PATH = ROOT / "schemas" / "run-result.schema.json"
DEPLOYMENT_PROFILE_IDS = ("profile-a", "profile-b", "reference")
QUALITY_REFERENCE_ID = "quality-reference-2xa6000"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def candidate_for_model(model_id: str) -> dict[str, Any] | None:
    """Look up a starting candidate so templates inherit its declared size."""
    try:
        with CANDIDATES_PATH.open(encoding="utf-8") as file:
            candidates = json.load(file)["candidates"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    for candidate in candidates:
        if candidate["model_id"] == model_id:
            return candidate
    return None


def schema_errors(record: dict[str, Any]) -> list[str]:
    """Check a record against schemas/run-result.schema.json."""
    try:
        import jsonschema
    except ModuleNotFoundError:
        raise SystemExit(
            "jsonschema is required to validate results against "
            "schemas/run-result.schema.json. Run: python3 -m pip install -e ."
        )
    with SCHEMA_PATH.open(encoding="utf-8") as file:
        schema = json.load(file)
    validator = jsonschema.Draft202012Validator(schema)
    found = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
    return [
        "schema: "
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in found
    ]


def profile_or_exit(config: dict[str, Any], profile_id: str) -> dict[str, Any]:
    try:
        return config["deployment_profiles"][profile_id]
    except KeyError:
        choices = ", ".join(config["deployment_profiles"])
        raise SystemExit(f"Unknown profile {profile_id!r}. Choose one of: {choices}")


def print_profiles(config: dict[str, Any]) -> None:
    header = (
        f"{'ID':<12} {'NAME':<24} {'CLASS':>7} "
        f"{'A6000 UTIL':>12} {'BUDGET':>10}  EXAMPLE"
    )
    print(header)
    print("-" * len(header))
    for profile_id, profile in config["deployment_profiles"].items():
        print(
            f"{profile_id:<12} "
            f"{profile['name']:<24} "
            f"{profile['vram_class_gib']:>5}GB "
            f"{profile['a6000_gpu_memory_utilization']:>12.2f} "
            f"{profile['a6000_executor_budget_gib']:>8.2f}GB  "
            f"{profile['example_hardware']}"
        )
    reference = config["quality_reference"]
    print(
        "\nQuality Reference (not a deployment profile): "
        f"{reference['gpu_count']}× {reference['gpu_model']}, "
        f"tensor parallel {reference['tensor_parallel_size']}"
    )


def show_profile(config: dict[str, Any], profile_id: str) -> None:
    profile = profile_or_exit(config, profile_id)
    print(json.dumps({"id": profile_id, **profile}, ensure_ascii=False, indent=2))


def validate_quantization_for_host(quantization: str, host_architecture: str) -> None:
    if host_architecture.lower() == "ampere" and quantization.lower() == "fp8":
        raise SystemExit(
            "FP8 W8A8 is not supported on Ampere in the current vLLM "
            "compatibility table. Use AWQ/GPTQ/INT8 for the A6000 run, or "
            "run FP8 on supported native hardware."
        )


def serve_command(config: dict[str, Any], args: argparse.Namespace) -> None:
    profile = profile_or_exit(config, args.profile)
    validate_quantization_for_host(args.quantization, args.host_architecture)
    if args.budget_mode == "class-ceiling":
        utilization = profile["a6000_gpu_memory_utilization"]
        budget = profile["a6000_executor_budget_gib"]
    else:
        utilization = profile["deployment_matched_a6000_utilization"]
        budget = profile["deployment_matched_executor_budget_gib"]

    command = [
        "vllm",
        "serve",
        args.model,
        "--revision",
        args.revision,
        "--tokenizer-revision",
        args.tokenizer_revision or args.revision,
        "--served-model-name",
        args.served_model_name or args.model,
        "--gpu-memory-utilization",
        f"{utilization:.2f}",
        "--max-model-len",
        str(args.max_model_len),
        "--max-num-seqs",
        str(args.max_num_seqs),
        "--tensor-parallel-size",
        "1",
        "--port",
        str(args.port),
    ]
    if args.dtype != "auto":
        command.extend(["--dtype", args.dtype])
    if args.quantization != "auto":
        command.extend(["--quantization", args.quantization])
    if args.enforce_eager:
        command.append("--enforce-eager")
    print(shlex.join(command))
    print(
        f"\n# {profile['name']}: A6000 executor budget "
        f"≈ {budget:.2f} GiB ({args.budget_mode})",
        file=sys.stderr,
    )
    if args.profile in {"profile-a", "profile-b"}:
        print(
            "# Evidence scope: memory fit and A6000-observed performance only; "
            "native target-GPU performance requires a separate run.",
            file=sys.stderr,
        )


def estimate_weights(args: argparse.Namespace) -> None:
    decimal_gb = args.params_billions * args.bits / 8
    gib = decimal_gb * 1_000_000_000 / (1024**3)
    print(f"Raw weight lower bound: {decimal_gb:.2f} GB ({gib:.2f} GiB)")
    print(
        "This excludes quantization metadata, KV cache, activations, CUDA graphs, "
        "allocator fragmentation, and runtime headroom."
    )


def new_result(config: dict[str, Any], args: argparse.Namespace) -> None:
    claim_scopes = {
        "memory-budget-emulation": "memory-fit-and-host-observed-performance",
        "native-gpu-validation": "native-target-gpu-performance",
        "quality-reference": "quality-ceiling-only",
    }
    host = config["emulation_host"]
    is_emulation = args.evidence == "memory-budget-emulation"
    is_quality_reference = args.profile == QUALITY_REFERENCE_ID
    if is_quality_reference:
        if args.evidence != "quality-reference":
            raise SystemExit(
                f"{QUALITY_REFERENCE_ID} requires --evidence quality-reference"
            )
        reference = config["quality_reference"]
        profile = {"vram_class_gib": f"{reference['gpu_count']}x48"}
        gpu_model = reference["gpu_model"]
        gpu_count = reference["gpu_count"]
        physical_vram = host["physical_vram_gib"]
        utilization = reference["gpu_memory_utilization_per_gpu"]
        tensor_parallel_size = reference["tensor_parallel_size"]
        memory_budget_mode = "native"
    else:
        profile = profile_or_exit(config, args.profile)
        if args.evidence == "quality-reference":
            raise SystemExit(
                "quality-reference evidence requires "
                f"--profile {QUALITY_REFERENCE_ID}"
            )
        gpu_count = 1
        tensor_parallel_size = 1
        if is_emulation:
            gpu_model = host["gpu_model"]
            physical_vram = host["physical_vram_gib"]
            memory_budget_mode = args.budget_mode
            utilization = (
                profile["deployment_matched_a6000_utilization"]
                if args.budget_mode == "deployment-matched"
                else profile["a6000_gpu_memory_utilization"]
            )
        else:
            # A native run owns the target card, so the nominal class size and
            # the native utilization default are the honest starting values.
            # Zeros here would silently violate the result schema.
            gpu_model = "FILL_ME"
            physical_vram = profile["vram_class_gib"]
            memory_budget_mode = "native"
            utilization = config["native_run_defaults"]["gpu_memory_utilization"]

    candidate = candidate_for_model(args.model)
    parameter_billions = args.parameter_billions or (
        candidate["parameter_billions"] if candidate else None
    )
    if not parameter_billions:
        raise SystemExit(
            f"{args.model} is not listed in configs/model-candidates.json. "
            "Pass --parameter-billions so the record satisfies the schema."
        )
    record = {
        "schema_version": "1.0.0",
        "run_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "profile_id": args.profile,
        "evidence_type": args.evidence,
        "claim_scope": claim_scopes[args.evidence],
        "hardware": {
            "gpu_model": gpu_model,
            "gpu_count": gpu_count,
            "physical_vram_gib": physical_vram,
            "power_limit_w": None,
            "driver_version": "FILL_ME",
            "cuda_version": "FILL_ME",
        },
        "software": {
            "vllm_version": "FILL_ME",
            "torch_version": "FILL_ME",
        },
        "model": {
            "id": args.model,
            "revision": args.revision,
            "tokenizer_revision": args.tokenizer_revision or args.revision,
            "parameter_billions": parameter_billions,
            "quantization": args.quantization,
            "dtype": "auto",
            "max_model_len": args.max_model_len,
        },
        "generation": {
            "thinking_mode": False,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "seed": 42,
            "max_tokens": 256,
        },
        "vllm": {
            "gpu_memory_utilization": utilization,
            "memory_budget_mode": memory_budget_mode,
            "max_num_seqs": args.max_num_seqs,
            "tensor_parallel_size": tensor_parallel_size,
            "command": "FILL_ME",
        },
        "rag": {
            "corpus_version": "FILL_ME",
            "eval_set_version": "FILL_ME",
            "retriever_config_hash": "FILL_ME",
            "prompt_revision": "FILL_ME",
        },
        "workload": {
            "concurrency": config["benchmark_policy"]["concurrency"],
            "request_count": args.request_count,
            "repetition": args.repetition,
        },
        "metrics": {
            "quality_score": 0,
            "answer_correctness": None,
            "groundedness": None,
            "citation_accuracy": None,
            "abstention_safety": None,
            "p50_ttft_ms": 0,
            "p95_ttft_ms": 0,
            "p95_e2e_ms": 0,
            "aggregate_output_tokens_per_s": 0,
            "peak_vram_gib": 0,
            "error_rate": 0,
            "oom_count": 0,
        },
        "decision": {
            "status": "pending",
            "reason": "FILL_ME",
            "monthly_cost_estimate": None,
            "currency": None,
        },
        "notes": (
            "Template only. Every value under 'metrics' is a placeholder, not a "
            "measurement; configuration fields are pre-filled from configs/ so "
            "the template already satisfies the result schema. "
            f"Nominal class: {profile['vram_class_gib']}GB."
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)


def nested(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(path)
        value = value[key]
    return value


def validate_result(config: dict[str, Any], path: Path) -> int:
    with path.open(encoding="utf-8") as file:
        record = json.load(file)

    host = config["emulation_host"]
    host_token = host["gpu_model"].upper().split()[-1]

    required = [
        "schema_version",
        "run_id",
        "timestamp_utc",
        "profile_id",
        "evidence_type",
        "claim_scope",
        "hardware.gpu_model",
        "hardware.gpu_count",
        "software.vllm_version",
        "model.id",
        "model.revision",
        "generation.thinking_mode",
        "generation.temperature",
        "generation.seed",
        "vllm.gpu_memory_utilization",
        "vllm.memory_budget_mode",
        "rag.corpus_version",
        "workload.concurrency",
        "metrics.quality_score",
        "metrics.p95_ttft_ms",
        "metrics.error_rate",
        "metrics.oom_count",
        "decision.status",
        # A record whose rationale is still a placeholder has no recorded
        # judgement, which is the one thing a result is supposed to carry.
        "decision.reason",
    ]
    errors: list[str] = []
    for field in required:
        try:
            value = nested(record, field)
        except KeyError:
            errors.append(f"missing required field: {field}")
            continue
        # Prefix, not equality: a record written by scripts/run_profile_a.sh
        # carried "FILL_ME: 측정값을 보고 직접 작성한다" and passed an
        # equality check, which is exactly the hole this guard exists to close.
        if isinstance(value, str) and value.startswith("FILL_ME"):
            errors.append(f"placeholder remains: {field}")

    # The repository publishes a result contract, so enforce it here instead of
    # trusting the hand-written field list alone.
    errors.extend(schema_errors(record))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    evidence = record["evidence_type"]
    expected_scopes = {
        "memory-budget-emulation": "memory-fit-and-host-observed-performance",
        "native-gpu-validation": "native-target-gpu-performance",
        "quality-reference": "quality-ceiling-only",
    }
    if evidence not in expected_scopes:
        errors.append(f"unsupported evidence_type: {evidence}")
    elif record["claim_scope"] != expected_scopes[evidence]:
        errors.append(
            f"claim_scope must be {expected_scopes[evidence]!r} for {evidence!r}"
        )

    is_quality_reference = record["profile_id"] == QUALITY_REFERENCE_ID
    if is_quality_reference:
        reference = config["quality_reference"]
        if evidence != "quality-reference":
            errors.append(
                f"{QUALITY_REFERENCE_ID} requires quality-reference evidence"
            )
        if record["hardware"]["gpu_count"] != reference["gpu_count"]:
            errors.append(
                f"quality reference requires {reference['gpu_count']} GPUs"
            )
        if (
            record["vllm"].get("tensor_parallel_size")
            != reference["tensor_parallel_size"]
        ):
            errors.append(
                "quality reference tensor_parallel_size does not match config"
            )
        profile = None
    else:
        try:
            profile = profile_or_exit(config, record["profile_id"])
        except SystemExit as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if evidence == "quality-reference":
            errors.append(
                "deployment profiles cannot use quality-reference evidence"
            )

    if evidence == "memory-budget-emulation" and profile is not None:
        budget_mode = record["vllm"]["memory_budget_mode"]
        if budget_mode == "class-ceiling":
            expected = profile["a6000_gpu_memory_utilization"]
        elif budget_mode == "deployment-matched":
            expected = profile["deployment_matched_a6000_utilization"]
        else:
            expected = None
            errors.append(
                "memory-budget-emulation requires class-ceiling or "
                "deployment-matched memory_budget_mode"
            )
        actual = record["vllm"]["gpu_memory_utilization"]
        if expected is not None and not math.isclose(
            actual, expected, abs_tol=0.001
        ):
            errors.append(
                f"gpu_memory_utilization {actual} does not match "
                f"{record['profile_id']} ({expected})"
            )
        if host_token not in record["hardware"]["gpu_model"].upper():
            errors.append("memory-budget-emulation must identify the A6000 host")

    if evidence == "native-gpu-validation":
        # The whole honesty claim rests on this separation, so guard both
        # directions: an emulated run must never be relabelled as a native one.
        actual_gpu = record["hardware"]["gpu_model"].upper()
        if host_token in actual_gpu:
            errors.append(
                f"native-gpu-validation cannot be recorded on the "
                f"{host['gpu_model']} emulation host; that run is "
                "memory-budget-emulation evidence"
            )
        if record["vllm"]["memory_budget_mode"] != "native":
            errors.append(
                "native-gpu-validation requires memory_budget_mode 'native'"
            )
        if profile is not None:
            class_gib = profile["vram_class_gib"]
            physical = record["hardware"]["physical_vram_gib"]
            if not 0.9 * class_gib <= physical <= 1.1 * class_gib:
                errors.append(
                    f"native-gpu-validation on {record['profile_id']} expects a "
                    f"~{class_gib}GB card, but physical_vram_gib is {physical}"
                )

    policy = config["benchmark_policy"]
    metrics = record["metrics"]
    gate_failures: list[str] = []
    if record["workload"]["concurrency"] != policy["concurrency"]:
        gate_failures.append(
            f"concurrency must be {policy['concurrency']} for the default scenario"
        )
    if metrics["quality_score"] < policy["quality_score_min"]:
        gate_failures.append("quality score below threshold")
    if metrics["p95_ttft_ms"] > policy["p95_ttft_ms_max"]:
        gate_failures.append("P95 TTFT above threshold")
    if metrics["error_rate"] > policy["error_rate_max"]:
        gate_failures.append("error rate above threshold")
    if metrics["oom_count"] > policy["oom_count_max"]:
        gate_failures.append("OOM count above threshold")

    expected_status = "fail" if gate_failures else "pass"
    if record["decision"]["status"] not in {expected_status, "pending"}:
        errors.append(
            f"decision.status is {record['decision']['status']!r}, but measured "
            f"gates imply {expected_status!r}"
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"VALID: {path}")
    print(f"Gate result: {expected_status.upper()}")
    if gate_failures:
        for failure in gate_failures:
            print(f"- {failure}")
    print(f"Evidence: {evidence}")
    print(f"Claim scope: {record['claim_scope']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List deployment profiles")

    show = subparsers.add_parser("show", help="Show one deployment profile")
    show.add_argument("profile", choices=DEPLOYMENT_PROFILE_IDS)

    command = subparsers.add_parser(
        "serve-command", help="Build a reproducible vLLM serve command"
    )
    command.add_argument(
        "--profile", required=True, choices=DEPLOYMENT_PROFILE_IDS
    )
    command.add_argument("--model", required=True)
    command.add_argument("--revision", required=True)
    command.add_argument(
        "--tokenizer-revision",
        help="Defaults to --revision; the protocol requires it pinned too",
    )
    command.add_argument("--served-model-name")
    command.add_argument(
        "--quantization",
        default="auto",
        help=(
            "vLLM quantization method (for example auto, awq, gptq, "
            "bitsandbytes, compressed-tensors, fp8)"
        ),
    )
    command.add_argument(
        "--dtype",
        default="auto",
        choices=["auto", "half", "float16", "bfloat16", "float"],
    )
    command.add_argument("--host-architecture", default="ampere")
    command.add_argument(
        "--budget-mode",
        default="class-ceiling",
        choices=["class-ceiling", "deployment-matched"],
    )
    command.add_argument("--max-model-len", type=int, default=8192)
    command.add_argument("--max-num-seqs", type=int, default=10)
    command.add_argument("--port", type=int, default=8000)
    command.add_argument(
        "--enforce-eager",
        action="store_true",
        help=(
            "Skip CUDA graph capture. Graphs cost 2.2-3.5 GiB outside the "
            "executor budget, which is what pushed both Profile A candidates "
            "past a real 24GB card. Costs throughput."
        ),
    )

    estimate = subparsers.add_parser(
        "estimate", help="Estimate the raw weight-size lower bound"
    )
    estimate.add_argument("--params-billions", type=float, required=True)
    estimate.add_argument("--bits", type=float, required=True)

    result = subparsers.add_parser(
        "new-result", help="Create a result-record template"
    )
    result.add_argument(
        "--profile",
        required=True,
        choices=[*DEPLOYMENT_PROFILE_IDS, QUALITY_REFERENCE_ID],
    )
    result.add_argument("--model", required=True)
    result.add_argument("--revision", required=True)
    result.add_argument("--tokenizer-revision", help="Defaults to --revision")
    result.add_argument("--quantization", required=True)
    result.add_argument(
        "--evidence",
        required=True,
        choices=[
            "memory-budget-emulation",
            "native-gpu-validation",
            "quality-reference",
        ],
    )
    result.add_argument(
        "--budget-mode",
        default="class-ceiling",
        choices=["class-ceiling", "deployment-matched"],
        help="Which A6000 probe this record describes (emulation evidence only)",
    )
    result.add_argument(
        "--parameter-billions",
        type=float,
        help="Only needed when the model is not in configs/model-candidates.json",
    )
    result.add_argument("--max-model-len", type=int, default=8192)
    result.add_argument("--max-num-seqs", type=int, default=10)
    result.add_argument("--request-count", type=int, default=30)
    result.add_argument("--repetition", type=int, default=1)
    result.add_argument("--output", required=True)

    validate = subparsers.add_parser(
        "validate-result", help="Validate result fields, evidence scope, and gates"
    )
    validate.add_argument("path", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()
    if args.command == "list":
        print_profiles(config)
    elif args.command == "show":
        show_profile(config, args.profile)
    elif args.command == "serve-command":
        serve_command(config, args)
    elif args.command == "estimate":
        estimate_weights(args)
    elif args.command == "new-result":
        new_result(config, args)
    elif args.command == "validate-result":
        return validate_result(config, args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
