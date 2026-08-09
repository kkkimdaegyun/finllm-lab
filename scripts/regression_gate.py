#!/usr/bin/env python3
"""Block a change that breaks quality, permissions, or the result contract.

This is the gate that decides whether a model / prompt / retriever / config
change may be deployed. It reuses what v0.1 already built — the unit tests, the
evaluation set, the result schema, the validator, and the quality thresholds in
configs/profiles.json — instead of inventing a second set of rules.

Two stages, deliberately separated:

    --stage cpu   nothing here needs a GPU or a running service
    --stage gpu   needs a live OpenAI-compatible endpoint

A CI runner without a GPU runs `--stage cpu` and every GPU check is reported as
"skipped", never as "passed". Claiming a GPU benchmark ran on a CPU runner is
the exact dishonesty this project exists to avoid.

Exit code is 0 only when every stage that actually ran passed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG_PATH = ROOT / "configs" / "profiles.json"
BASELINE_PATH = ROOT / "ops" / "baselines" / "profile-a-baseline.json"
ALERT_RULES_PATH = ROOT / "monitoring" / "prometheus" / "rules" / "finllm-alerts.yml"

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"


# --------------------------------------------------------------------------
# Alert thresholds must come from configs/profiles.json, never from a second
# copy inside the rule file. interface-contract-v0.2.md 불변 규칙 7.
# Each entry maps an alert name to the value its `expr` must compare against.
# --------------------------------------------------------------------------
def _expected_alert_thresholds(config: dict[str, Any]) -> dict[str, float]:
    policy = config["benchmark_policy"]
    profile_a = config["deployment_profiles"]["profile-a"]
    return {
        "FinLLMHighRequestErrorRate": policy["error_rate_max"],
        "FinLLMVLLMHighP95TTFT": policy["p95_ttft_ms_max"] / 1000.0,
        "FinLLMGPUMemoryAboveProfileClass": profile_a["vram_class_gib"] * 1024,
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Result:
    def __init__(
        self,
        stage: str,
        status: str,
        detail: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.stage = stage
        self.status = status
        self.detail = detail
        self.evidence = evidence or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


# ==========================================================================
# CPU stages
# ==========================================================================


def stage_unit_tests(ctx: dict[str, Any]) -> Result:
    """v0.1의 결정적 테스트 전체. 하나라도 깨지면 변경을 막는다.

    FINLLM_GATE_NESTED로 재귀를 끊는다. tests/test_regression_gate.py가 이
    스크립트를 subprocess로 실행하는데, 이 단계가 다시 전체 테스트를 돌리면
    무한 재귀가 된다. 실제로 그렇게 만들어서 테스트 스위트가 멈추는 것을
    확인했고, 그래서 이 가드가 있다.
    """
    environment = dict(os.environ)
    environment["FINLLM_GATE_NESTED"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    tail = (completed.stderr or completed.stdout).strip().splitlines()
    ran = next((line for line in tail if line.startswith("Ran ")), "Ran ?")
    if completed.returncode != 0:
        return Result(
            "unit-tests",
            FAIL,
            f"unittest 실패 ({ran})",
            {"returncode": completed.returncode, "tail": tail[-15:]},
        )
    return Result("unit-tests", PASS, ran, {"returncode": 0})


def stage_result_schema(ctx: dict[str, Any]) -> Result:
    """results/의 모든 레코드가 아직 계약을 만족하는가."""
    import finllm_profile  # noqa: PLC0415 - path is set up above

    records = sorted((ROOT / "results").glob("*.json"))
    if not records:
        return Result("result-schema", FAIL, "results/에 레코드가 없다")

    broken: list[str] = []
    for record in records:
        buffer_out, buffer_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer_out), contextlib.redirect_stderr(
            buffer_err
        ):
            try:
                code = finllm_profile.validate_result(ctx["config"], record)
            except SystemExit:
                code = 1
        if code != 0:
            broken.append(f"{record.name}: {buffer_err.getvalue().strip()[:200]}")

    if broken:
        return Result(
            "result-schema",
            FAIL,
            f"{len(broken)}/{len(records)} 레코드가 계약 위반",
            {"broken": broken[:5]},
        )
    return Result(
        "result-schema",
        PASS,
        f"{len(records)}개 레코드 전부 validate-result 통과",
        {"record_count": len(records)},
    )


def stage_eval_set_integrity(ctx: dict[str, Any]) -> Result:
    """평가셋이 baseline 이후 조용히 바뀌지 않았는가.

    tests/test_eval_set.py는 평가셋이 *유효한지* 본다. 이 단계는 평가셋이
    *변했는지* 본다. 채점 대상을 바꿔놓고 점수가 올랐다고 말하는 것이
    regression gate가 막아야 할 가장 흔한 자기기만이다.
    """
    baseline = ctx["baseline"]
    dataset = ROOT / "datasets" / f"{baseline['eval_set_version']}.jsonl"
    if not dataset.exists():
        return Result("eval-set-integrity", FAIL, f"평가셋 없음: {dataset}")

    actual = sha256_file(dataset)
    expected = baseline["eval_set_sha256"]
    if actual != expected:
        return Result(
            "eval-set-integrity",
            FAIL,
            "평가셋이 baseline과 다르다. 의도한 변경이면 baseline을 새로 고정하고 "
            "재측정 근거를 ADR로 남겨라",
            {"expected_sha256": expected, "actual_sha256": actual},
        )
    return Result(
        "eval-set-integrity",
        PASS,
        f"{dataset.name} sha256 일치",
        {"sha256": actual},
    )


def stage_retrieval_acl(ctx: dict[str, Any]) -> Result:
    """권한 없는 문서가 검색되는가. GPU도 모델도 필요 없다.

    v0.1의 핵심 보안 주장 — "권한은 retrieval 이전 데이터 층에서 강제한다" —
    을 매 변경마다 확인한다. 이것이 CPU 단계에 있다는 사실 자체가 그 주장의
    증거다. 모델이 필요 없으므로 모델을 바꿔도 이 성질은 유지된다.
    """
    import rag_index  # noqa: PLC0415

    index_path = ctx["index_path"]
    if not index_path.exists():
        chunks = rag_index.load_corpus(ROOT / "corpus" / ctx["baseline"]["corpus_version"].replace("corpus-", ""))
        rag_index.save_index(chunks, index_path)

    retriever = rag_index.Retriever.from_index_file(index_path)
    dataset = ROOT / "datasets" / f"{ctx['baseline']['eval_set_version']}.jsonl"
    cases = [
        json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    violations: list[dict[str, Any]] = []
    checked = 0
    for case in cases:
        forbidden = set(case.get("forbidden_doc_ids") or [])
        if not forbidden:
            continue
        checked += 1
        hits = retriever.search(case["question"], case["role"], top_k=ctx["top_k"])
        leaked = sorted({hit["chunk"]["doc_id"] for hit in hits} & forbidden)
        if leaked:
            violations.append({"case": case["id"], "role": case["role"], "leaked": leaked})

    if violations:
        return Result(
            "retrieval-acl",
            FAIL,
            f"{len(violations)}개 문항에서 권한 없는 문서가 검색됐다",
            {"violations": violations[:5]},
        )
    return Result(
        "retrieval-acl",
        PASS,
        f"forbidden_doc_ids를 가진 {checked}개 문항 전부 위반 0건",
        {"cases_checked": checked, "top_k": ctx["top_k"]},
    )


def stage_retriever_config_hash(ctx: dict[str, Any]) -> Result:
    """검색 결과를 바꾸는 입력이 바뀌었는가."""
    import rag_index  # noqa: PLC0415

    retriever = rag_index.Retriever.from_index_file(ctx["index_path"])
    actual = retriever.config_hash()
    expected = ctx["baseline"]["retriever_config_hash"]
    if actual != expected:
        return Result(
            "retriever-config-hash",
            FAIL,
            "retriever 설정 또는 corpus가 바뀌었다. baseline 품질 수치는 더 이상 "
            "비교 대상이 아니다 — 재측정하고 baseline을 갱신하라",
            {"expected": expected, "actual": actual},
        )
    return Result("retriever-config-hash", PASS, f"config_hash {actual} 유지", {"config_hash": actual})


def stage_prompt_revision(ctx: dict[str, Any]) -> Result:
    import rag_eval  # noqa: PLC0415

    actual = rag_eval.PROMPT_REVISION
    expected = ctx["baseline"]["prompt_revision"]
    if actual != expected:
        return Result(
            "prompt-revision",
            FAIL,
            "prompt revision이 baseline과 다르다. 프롬프트를 바꿨으면 품질을 "
            "재측정하고 baseline을 갱신해야 한다",
            {"expected": expected, "actual": actual},
        )
    return Result("prompt-revision", PASS, f"prompt revision {actual} 유지")


def stage_alert_threshold_consistency(ctx: dict[str, Any]) -> Result:
    """alert threshold가 configs/profiles.json과 어긋나지 않는가.

    SLO 숫자가 두 벌 존재하면 반드시 갈라진다. Prometheus rule 파일은 실행
    시점에 JSON을 읽을 수 없으므로, 대신 여기서 두 값이 같은지 강제한다.
    """
    if not ALERT_RULES_PATH.exists():
        return Result("alert-threshold-consistency", FAIL, f"alert rule 파일 없음: {ALERT_RULES_PATH}")

    text = ALERT_RULES_PATH.read_text(encoding="utf-8")
    expected = _expected_alert_thresholds(ctx["config"])

    # PyYAML은 이 프로젝트의 선언된 의존성이 아니다(폐쇄망 전제). rule 파일을
    # alert 블록으로 잘라 비교 연산자의 숫자만 읽는다.
    blocks = re.split(r"^\s*-\s+alert:\s*", text, flags=re.MULTILINE)[1:]
    found: dict[str, float] = {}
    for block in blocks:
        name = block.splitlines()[0].strip()
        expr_match = re.search(r"^\s*expr:\s*(\|?)\s*\n?(.*?)(?=^\s{8}\w+:)", block, re.MULTILINE | re.DOTALL)
        expr = expr_match.group(2) if expr_match else block
        numbers = re.findall(r"[><]=?\s*([0-9]+(?:\.[0-9]+)?)", expr)
        if numbers:
            found[name] = float(numbers[-1])

    mismatches = []
    for name, want in expected.items():
        if name not in found:
            mismatches.append(f"{name}: rule 파일에서 비교 임계값을 찾지 못함")
        elif abs(found[name] - want) > 1e-9:
            mismatches.append(f"{name}: rule={found[name]} != configs={want}")

    if mismatches:
        return Result(
            "alert-threshold-consistency",
            FAIL,
            "alert threshold가 configs/profiles.json과 다르다",
            {"mismatches": mismatches},
        )
    return Result(
        "alert-threshold-consistency",
        PASS,
        f"{len(expected)}개 alert threshold가 configs/profiles.json과 일치",
        {"checked": {k: found[k] for k in expected}},
    )


# ==========================================================================
# GPU / live-service stages
# ==========================================================================


def stage_smoke_evaluation(ctx: dict[str, Any]) -> Result:
    """실제 endpoint에 평가셋을 돌린다. 이후 GPU 단계가 이 결과를 쓴다."""
    output = ctx["work_dir"] / f"gate-eval-{uuid.uuid4().hex}.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "rag_eval.py"),
        "--index",
        str(ctx["index_path"]),
        "--base-url",
        ctx["base_url"],
        "--model",
        ctx["model"],
        "--output",
        str(output),
        "--human-review-sample",
        "0",
    ]
    if ctx["frozen_retrieval"]:
        command.extend(["--frozen-retrieval", str(ctx["frozen_retrieval"])])

    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    elapsed = round(time.perf_counter() - started, 2)

    if completed.returncode != 0:
        return Result(
            "smoke-evaluation",
            FAIL,
            f"rag_eval이 non-zero({completed.returncode})로 실패했다",
            {
                "returncode": completed.returncode,
                "output_created": output.exists(),
                "stderr": completed.stderr[-800:],
            },
        )
    if not output.exists():
        return Result(
            "smoke-evaluation",
            FAIL,
            "rag_eval이 결과를 만들지 못했다",
            {"returncode": completed.returncode, "stderr": completed.stderr[-800:]},
        )

    report = load_json(output)
    ctx["evaluation"] = report
    summary = report["summary"]
    # rag_eval은 ACL 누출이 있으면 non-zero로 끝난다. 그것은 아래 전용 단계가
    # 판정하므로 여기서는 결과를 만들었는지만 본다.
    return Result(
        "smoke-evaluation",
        PASS,
        f"{summary['case_count']}문항 평가 완료 ({elapsed}s)",
        {
            "output": str(output.relative_to(ROOT)),
            "quality_score": summary["quality_score"],
            "generation_errors": summary.get("generation_errors", 0),
            "wall_seconds": elapsed,
        },
    )


def stage_acl_runtime(ctx: dict[str, Any]) -> Result:
    evaluation = ctx.get("evaluation")
    if evaluation is None:
        return Result("acl-runtime", FAIL, "평가 결과가 없다")
    summary = evaluation["summary"]
    count = summary["acl_violations"]
    if count > 0:
        return Result(
            "acl-runtime",
            FAIL,
            f"권한 위반 {count}건 — 품질 점수와 무관하게 배포 불가",
            {"cases": summary.get("acl_violation_cases", [])},
        )
    return Result("acl-runtime", PASS, "권한 위반 0건", {"acl_violations": 0})


def stage_quality_regression(ctx: dict[str, Any]) -> Result:
    """절대 기준과 baseline 대비 회귀를 둘 다 본다."""
    evaluation = ctx.get("evaluation")
    if evaluation is None:
        return Result("quality-regression", FAIL, "평가 결과가 없다")

    summary = evaluation["summary"]
    actual = summary["quality_score"]
    policy_min = ctx["config"]["benchmark_policy"]["quality_score_min"]
    baseline = ctx["baseline"]
    baseline_quality = baseline["quality_score"]
    tolerance = baseline["quality_regression_tolerance"]
    floor = baseline_quality - tolerance

    failures = []
    if actual < policy_min:
        failures.append(f"절대 기준 미달: {actual} < {policy_min} (benchmark_policy.quality_score_min)")
    if actual < floor:
        failures.append(
            f"baseline 대비 회귀: {actual} < {floor:.3f} "
            f"(baseline {baseline_quality} - 허용편차 {tolerance})"
        )

    evidence = {
        "quality_score": actual,
        "policy_minimum": policy_min,
        "baseline_quality": baseline_quality,
        "tolerance": tolerance,
        "regression_floor": round(floor, 3),
        "tolerance_source": baseline["quality_regression_tolerance_source"],
        "axes": {
            name: summary[name]
            for name in (
                "answer_correctness",
                "groundedness",
                "citation_accuracy",
                "abstention_safety",
            )
        },
    }
    if failures:
        return Result("quality-regression", FAIL, "; ".join(failures), evidence)
    return Result(
        "quality-regression",
        PASS,
        f"품질 {actual} ≥ 기준 {policy_min}, baseline 하한 {floor:.3f}",
        evidence,
    )


def stage_injection_regression(ctx: dict[str, Any]) -> Result:
    """injection 방어가 baseline보다 나빠지지 않았는가.

    baseline은 5문항 중 2회 성공이다. 이 프로젝트는 그것을 미해결 결함으로
    공개해 두었다. gate의 역할은 그것을 통과시키는 것이 아니라 **더 나빠지는
    것을 막는** 것이다.
    """
    evaluation = ctx.get("evaluation")
    if evaluation is None:
        return Result("injection-regression", FAIL, "평가 결과가 없다")
    summary = evaluation["summary"]
    actual = summary["injection_successes"]
    allowed = ctx["baseline"]["injection_successes_max"]
    evidence = {
        "injection_successes": actual,
        "baseline_max": allowed,
        "cases": summary.get("injection_success_cases", []),
        "note": "baseline 2건은 미해결 결함으로 공개된 값이다. gate는 악화만 막는다.",
    }
    if actual > allowed:
        return Result(
            "injection-regression",
            FAIL,
            f"prompt injection 성공 {actual}건 > baseline {allowed}건 — 방어가 악화됐다",
            evidence,
        )
    return Result("injection-regression", PASS, f"injection 성공 {actual}건 ≤ baseline {allowed}건", evidence)


# ==========================================================================

CPU_STAGES: list[tuple[str, Callable[[dict[str, Any]], Result]]] = [
    ("unit-tests", stage_unit_tests),
    ("result-schema", stage_result_schema),
    ("eval-set-integrity", stage_eval_set_integrity),
    ("retrieval-acl", stage_retrieval_acl),
    ("retriever-config-hash", stage_retriever_config_hash),
    ("prompt-revision", stage_prompt_revision),
    ("alert-threshold-consistency", stage_alert_threshold_consistency),
]

GPU_STAGES: list[tuple[str, Callable[[dict[str, Any]], Result]]] = [
    ("smoke-evaluation", stage_smoke_evaluation),
    ("acl-runtime", stage_acl_runtime),
    ("quality-regression", stage_quality_regression),
    ("injection-regression", stage_injection_regression),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["cpu", "gpu", "all"], default="cpu")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default=None, help="기본값은 baseline의 model_id")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--index", type=Path, default=ROOT / "work" / "v02" / "gate-index.json")
    parser.add_argument(
        "--frozen-retrieval",
        type=Path,
        default=None,
        help="retrieval을 동결해 generation-only 비교를 만든다",
    )
    parser.add_argument("--top-k", type=int, default=8, help="rag_eval 기본값과 같아야 한다")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="STAGE",
        help=(
            "이 단계를 실행하지 않는다(여러 번 지정 가능). 결과에 skipped로 "
            "기록되며 통과로 세지 않는다."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_json(CONFIG_PATH)
    baseline = load_json(args.baseline)

    work_dir = args.index.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    ctx: dict[str, Any] = {
        "config": config,
        "baseline": baseline,
        "index_path": args.index,
        "base_url": args.base_url,
        "model": args.model or baseline["model"]["id"],
        "frozen_retrieval": args.frozen_retrieval,
        "top_k": args.top_k,
        "work_dir": work_dir,
    }

    selected: list[tuple[str, Callable[[dict[str, Any]], Result]]] = []
    skipped: list[str] = []
    if args.stage in {"cpu", "all"}:
        selected.extend(CPU_STAGES)
    else:
        skipped.extend(name for name, _ in CPU_STAGES)
    if args.stage in {"gpu", "all"}:
        selected.extend(GPU_STAGES)
    else:
        skipped.extend(name for name, _ in GPU_STAGES)

    explicitly_skipped = set(args.skip)
    unknown = explicitly_skipped - {name for name, _ in CPU_STAGES + GPU_STAGES}
    if unknown:
        raise SystemExit(f"알 수 없는 단계: {sorted(unknown)}")
    if explicitly_skipped:
        skipped.extend(name for name, _ in selected if name in explicitly_skipped)
        selected = [item for item in selected if item[0] not in explicitly_skipped]

    print(f"regression gate — stage={args.stage}")
    print(f"baseline: {baseline['baseline_id']}")
    print("-" * 72)

    results: list[Result] = []
    for name, function in selected:
        started = time.perf_counter()
        try:
            result = function(ctx)
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failing check
            result = Result(name, FAIL, f"{type(exc).__name__}: {exc}")
        elapsed = round(time.perf_counter() - started, 2)
        result.evidence.setdefault("seconds", elapsed)
        results.append(result)
        mark = "OK  " if result.status == PASS else "FAIL"
        print(f"[{mark}] {name:32} {result.detail}")

    for name in skipped:
        results.append(
            Result(
                name,
                SKIPPED,
                f"--stage {args.stage} 에서는 실행하지 않음 (통과가 아니다)",
            )
        )
        print(f"[SKIP] {name:32} 실행하지 않음 — 통과로 세지 않는다")

    executed = [r for r in results if r.status != SKIPPED]
    if not executed:
        results.append(
            Result(
                "gate-execution",
                FAIL,
                "실행된 stage가 0개다 — skip-only gate는 release evidence가 아니다",
            )
        )
    elif args.stage == "all" and skipped:
        results.append(
            Result(
                "gate-execution",
                FAIL,
                "--stage all은 CPU/GPU 전 stage를 실행해야 한다",
                {"skipped": sorted(set(skipped))},
            )
        )

    failed = [r for r in results if r.status == FAIL]
    passed = [r for r in results if r.status == PASS]
    overall = FAIL if failed else PASS

    report = {
        "schema_version": "1.0.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        "overall": overall,
        "counts": {"pass": len(passed), "fail": len(failed), "skipped": len(skipped)},
        "baseline_id": baseline["baseline_id"],
        "provenance": {
            "model": ctx["model"],
            "base_url": args.base_url if args.stage in {"gpu", "all"} else None,
            "eval_set_version": baseline["eval_set_version"],
            "prompt_revision": baseline["prompt_revision"],
            "retriever_config_hash": baseline["retriever_config_hash"],
        },
        "stages": [r.as_dict() for r in results],
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nreport: {args.output}")

    print("-" * 72)
    print(f"OVERALL: {overall.upper()}  (pass={len(passed)} fail={len(failed)} skipped={len(skipped)})")
    if failed:
        print("\n차단 사유:")
        for result in failed:
            print(f"  - {result.stage}: {result.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
