#!/usr/bin/env python3
"""Aggregate FinLLM GPU evidence into a reproducible quantization autopsy."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parents[1]
DEFAULT_RESULTS = REPO_ROOT / "results"
DEFAULT_ARTIFACTS = PROJECT_DIR / "artifacts"
DEFAULT_PORTFOLIO = PROJECT_DIR / "portfolio" / "index.html"

METRICS = (
    "quality_score",
    "p95_ttft_ms",
    "p95_user_ttft_ms",
    "aggregate_output_tokens_per_s",
    "peak_vram_gib",
)


def mean(values: list[float]) -> float:
    return statistics.fmean(values)


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _variant(run: dict[str, Any]) -> str:
    model = run["model"]
    size = f"{int(round(float(model['parameter_billions'])))}B"
    precision = "AWQ 4-bit" if model["quantization"] == "awq" else "BF16"
    budget = run["vllm"]["memory_budget_mode"].replace("-", " ")
    execution = "eager" if run["vllm"].get("enforce_eager") else "cuda graph"
    return f"{size} {precision} · {budget} · {execution}"


def load_runs(results_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(list(results_dir.glob("2026-08-08b-profile-a-*.json"))
                   + list(results_dir.glob("2026-08-08c-profile-a-*.json")))
    runs = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["model"]["id"] not in {"Qwen/Qwen3-8B", "Qwen/Qwen3-14B-AWQ"}:
            continue
        payload["_path"] = str(path.resolve())
        payload["_variant"] = _variant(payload)
        runs.append(payload)
    if not runs:
        raise ValueError(f"분석할 2026-08-08b/c 결과가 없습니다: {results_dir}")
    return runs


def validate_runs(runs: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    run_ids: set[str] = set()
    groups: dict[str, int] = defaultdict(int)
    for run in runs:
        if run.get("run_id") in run_ids:
            errors.append(f"duplicate run_id: {run.get('run_id')}")
        run_ids.add(run.get("run_id"))
        if run.get("evidence_type") != "memory-budget-emulation":
            errors.append(f"unexpected evidence type: {run.get('_path')}")
        if run.get("hardware", {}).get("gpu_model") != "NVIDIA RTX A6000":
            errors.append(f"unexpected GPU: {run.get('_path')}")
        if run.get("software", {}).get("vllm_version") != "0.9.2":
            errors.append(f"mixed vLLM version: {run.get('_path')}")
        if run.get("workload", {}).get("concurrency") != 10:
            errors.append(f"mixed concurrency: {run.get('_path')}")
        missing = [metric for metric in METRICS if metric not in run.get("metrics", {})]
        if missing:
            errors.append(f"missing metrics {missing}: {run.get('_path')}")
        groups[run["_variant"]] += 1
    wrong_repeats = {key: value for key, value in groups.items() if value != 3}
    if wrong_repeats:
        errors.append(f"각 구성은 3회 반복이어야 합니다: {wrong_repeats}")
    if len(groups) != 6:
        errors.append(f"6개 구성이 필요하지만 {len(groups)}개 발견")
    if errors:
        raise ValueError("\n".join(errors))


def aggregate(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[run["_variant"]].append(run)
    rows = []
    for variant, items in grouped.items():
        first = items[0]
        metrics = {}
        for metric in METRICS:
            values = [float(item["metrics"][metric]) for item in items]
            metrics[metric] = round(mean(values), 4)
            metrics[f"{metric}_stdev"] = round(stdev(values), 4)
        breakdown = first["vllm"]["memory_breakdown_gib"]
        rows.append({
            "variant": variant,
            "model_id": first["model"]["id"],
            "parameter_billions": first["model"]["parameter_billions"],
            "quantization": first["model"]["quantization"],
            "memory_budget_mode": first["vllm"]["memory_budget_mode"],
            "gpu_memory_utilization": first["vllm"]["gpu_memory_utilization"],
            "execution_mode": "eager" if first["vllm"].get("enforce_eager") else "cuda_graph",
            "repeats": len(items),
            "model_weights_gib": breakdown["model_weights"],
            "kv_cache_gib": breakdown["kv_cache"],
            "cuda_graphs_gib": breakdown["cuda_graphs"],
            "max_concurrency": first["vllm"]["max_concurrency_at_max_model_len"],
            **metrics,
        })
    return sorted(rows, key=lambda item: (
        item["parameter_billions"], item["memory_budget_mode"], item["execution_mode"]
    ))


def find_row(rows: list[dict[str, Any]], *, model: str, budget: str, execution: str) -> dict[str, Any]:
    matches = [row for row in rows if row["model_id"] == model
               and row["memory_budget_mode"] == budget
               and row["execution_mode"] == execution]
    if len(matches) != 1:
        raise ValueError(f"구성 식별 실패: {model}, {budget}, {execution}")
    return matches[0]


def build_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    validate_runs(runs)
    rows = aggregate(runs)
    awq_graph = find_row(rows, model="Qwen/Qwen3-14B-AWQ", budget="class-ceiling",
                         execution="cuda_graph")
    awq_eager = find_row(rows, model="Qwen/Qwen3-14B-AWQ", budget="class-ceiling",
                         execution="eager")
    bf16_graph = find_row(rows, model="Qwen/Qwen3-8B", budget="class-ceiling",
                          execution="cuda_graph")
    bf16_eager = find_row(rows, model="Qwen/Qwen3-8B", budget="class-ceiling",
                          execution="eager")
    recommended = find_row(rows, model="Qwen/Qwen3-14B-AWQ", budget="deployment-matched",
                           execution="eager")

    findings = {
        "awq_throughput_recovery_x": round(
            awq_eager["aggregate_output_tokens_per_s"]
            / awq_graph["aggregate_output_tokens_per_s"], 3
        ),
        "bf16_throughput_change_pct": round(
            (bf16_eager["aggregate_output_tokens_per_s"]
             / bf16_graph["aggregate_output_tokens_per_s"] - 1) * 100, 2
        ),
        "awq_user_ttft_reduction_pct": round(
            (1 - awq_eager["p95_user_ttft_ms"] / awq_graph["p95_user_ttft_ms"]) * 100, 2
        ),
        "weight_memory_saved_gib": round(
            bf16_eager["model_weights_gib"] - awq_eager["model_weights_gib"], 4
        ),
        "kv_cache_gain_gib": round(
            awq_eager["kv_cache_gib"] - bf16_eager["kv_cache_gib"], 4
        ),
        "recommended_peak_headroom_gib": round(24 - recommended["peak_vram_gib"], 4),
        "recommended_quality_gain_points": round(
            recommended["quality_score"] - bf16_eager["quality_score"], 4
        ),
    }
    return {
        "schema_version": 1,
        "title": "Quantization Autopsy",
        "generated_from": "FinLLM Lab v0.2 immutable result records",
        "evidence_boundary": {
            "type": "memory-budget-emulation",
            "host_gpu": "NVIDIA RTX A6000",
            "target_card_claim": "NOT_EXECUTED",
            "warning": "A6000 관측값이며 실제 RTX 4090/24GB 성능이 아니다.",
        },
        "protocol": {
            "runs": len(runs),
            "configurations": len(rows),
            "repeats_per_configuration": 3,
            "concurrency": 10,
            "requests_per_run": 30,
            "vllm_version": "0.9.2",
        },
        "configurations": rows,
        "findings": findings,
        "recommendation": {
            "variant": recommended["variant"],
            "reason": (
                "24GB executor budget에서 품질·사용자 TTFT·처리량 gate를 만족하고 "
                "A6000 관측 peak 기준 약 2GiB 여유를 남긴 구성"
            ),
        },
        "limitations": [
            "8B BF16과 14B AWQ는 모델 크기가 달라 순수 양자화 효과를 식별할 수 없다.",
            "CUDA graph의 정확한 kernel root cause는 프로파일러로 측정하지 않아 NOT_MEASURED다.",
            "실제 24GB Ada/Blackwell 카드 검증은 NOT_EXECUTED다.",
            "금융 QA 60문항 합성 평가셋 결과를 production traffic으로 일반화하지 않는다.",
        ],
    }


def write_csv(summary: dict[str, Any], path: Path) -> None:
    rows = summary["configurations"]
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_report(summary: dict[str, Any], path: Path) -> None:
    f = summary["findings"]
    lines = [
        "# Quantization Autopsy — Evidence Report",
        "",
        "> 18 runs · 6 configurations · 3 repetitions · NVIDIA RTX A6000",
        "",
        "## 결론",
        "",
        f"- 14B AWQ는 CUDA graph를 끄자 처리량이 **{f['awq_throughput_recovery_x']:.2f}×** 회복됐다.",
        f"- 같은 변경에서 8B BF16 처리량 변화는 **{f['bf16_throughput_change_pct']:+.2f}%**였다.",
        f"- 14B AWQ의 사용자 P95 TTFT는 **{f['awq_user_ttft_reduction_pct']:.2f}%** 감소했다.",
        f"- AWQ 모델 가중치는 8B BF16보다 **{f['weight_memory_saved_gib']:.2f}GiB** 작았고, "
        f"KV cache는 **{f['kv_cache_gain_gib']:.2f}GiB** 더 확보했다.",
        "",
        "따라서 초기의 ‘AWQ라서 느리다’는 설명은 기각한다. 확인된 범위는 "
        "vLLM 0.9.2 + Ampere + 해당 AWQ 모델에서 graph-enabled path와 성능 저하가 "
        "함께 나타났다는 것까지다.",
        "",
        "## 구성별 집계",
        "",
        "| 구성 | 품질 | 사용자 P95 TTFT | 처리량 | Peak VRAM | 최대 동시성 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary["configurations"]:
        lines.append(
            f"| {row['variant']} | {row['quality_score']:.3f} | "
            f"{row['p95_user_ttft_ms']:.1f}ms | "
            f"{row['aggregate_output_tokens_per_s']:.1f} tok/s | "
            f"{row['peak_vram_gib']:.2f}GiB | {row['max_concurrency']:.2f} |"
        )
    lines.extend([
        "",
        "## 권고",
        "",
        f"**{summary['recommendation']['variant']}**",
        "",
        summary["recommendation"]["reason"] + ".",
        "",
        "## 증거 경계",
        "",
        f"- {summary['evidence_boundary']['warning']}",
        *[f"- {item}" for item in summary["limitations"]],
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def render_html(summary: dict[str, Any]) -> str:
    payload = json.dumps(summary, ensure_ascii=False).replace("</", "<\\/")
    f = summary["findings"]
    cards = [
        ("AWQ 처리량 회복", f"{f['awq_throughput_recovery_x']:.2f}×", "CUDA graph → eager"),
        ("사용자 TTFT 감소", f"{f['awq_user_ttft_reduction_pct']:.1f}%", "14B AWQ class ceiling"),
        ("가중치 메모리 절감", f"{f['weight_memory_saved_gib']:.2f} GiB", "14B AWQ vs 8B BF16"),
        ("24GB headroom", f"{f['recommended_peak_headroom_gib']:.2f} GiB", "권고 구성 A6000 관측"),
    ]
    card_html = "".join(
        f'<article class="metric"><span>{html.escape(label)}</span><strong>{value}</strong><small>{html.escape(note)}</small></article>'
        for label, value, note in cards
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quantization Autopsy</title>
<style>
:root{{--bg:#08111f;--panel:#0f1c2e;--line:#20334c;--text:#eef5ff;--muted:#9db0c7;--cyan:#35d5ff;--lime:#9bf56d;--amber:#ffc857}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 80% 0,#12345a 0,transparent 35%),var(--bg);color:var(--text);font-family:"Noto Sans KR","Noto Sans",sans-serif}}
main{{max-width:1180px;margin:auto;padding:56px 24px 80px}}.eyebrow{{color:var(--cyan);font-size:13px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}}h1{{font-size:clamp(42px,7vw,82px);line-height:.98;margin:14px 0 20px;letter-spacing:-.05em}}.lead{{max-width:820px;color:var(--muted);font-size:19px;line-height:1.7}}.badge{{display:inline-flex;margin-top:20px;padding:9px 13px;border:1px solid var(--amber);color:var(--amber);border-radius:999px;font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:42px 0}}.metric,.panel{{background:linear-gradient(145deg,#12233a,#0c1728);border:1px solid var(--line);border-radius:18px;padding:20px}}.metric span,.metric small{{display:block;color:var(--muted)}}.metric strong{{display:block;font-size:31px;margin:10px 0;color:var(--lime)}}
.panel{{margin-top:18px;padding:26px}}h2{{font-size:25px;margin:0 0 18px}}select{{background:#091522;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.bar-row{{display:grid;grid-template-columns:minmax(240px,1.4fr) 3fr 90px;gap:12px;align-items:center;margin:14px 0}}.bar-label{{font-size:13px;color:#c6d4e5}}.track{{height:17px;background:#07101c;border-radius:999px;overflow:hidden}}.bar{{height:100%;background:linear-gradient(90deg,var(--cyan),var(--lime));border-radius:999px;transition:width .4s}}.value{{font-variant-numeric:tabular-nums;text-align:right}}
.flow{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.step{{padding:17px;border:1px solid var(--line);border-radius:14px;color:var(--muted)}}.step b{{display:block;color:var(--text);margin-bottom:7px}}.finding{{border-left:3px solid var(--cyan);padding:4px 0 4px 18px;line-height:1.75}}.warning{{border-color:var(--amber);color:#ffe1a1}}footer{{margin-top:42px;color:var(--muted);font-size:12px}}@media(max-width:800px){{.grid,.flow{{grid-template-columns:1fr 1fr}}.bar-row{{grid-template-columns:1fr}}}}@media(max-width:520px){{.grid,.flow{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">Inference Engineering · Evidence-Driven Debugging</div><h1>Quantization<br>Autopsy</h1>
<p class="lead">“4-bit라서 느리다”는 그럴듯한 설명을 추가 실험으로 반증했습니다. 모델 정밀도만 보지 않고 실행 경로, KV cache, queueing, 사용자 TTFT를 함께 해부합니다.</p>
<div class="badge">MEMORY-BUDGET-EMULATION · NATIVE 24GB GPU NOT EXECUTED</div>
<section class="grid">{card_html}</section>
<section class="panel"><h2>구성별 지표 비교</h2><label>지표 <select id="metric"><option value="aggregate_output_tokens_per_s">처리량 (tok/s)</option><option value="p95_user_ttft_ms">사용자 P95 TTFT (ms)</option><option value="peak_vram_gib">Peak VRAM (GiB)</option><option value="quality_score">품질 점수</option><option value="max_concurrency">최대 동시성</option></select></label><div id="bars"></div></section>
<section class="panel"><h2>진단 흐름</h2><div class="flow"><div class="step"><b>01 관측</b>14B AWQ 처리량 1/5</div><div class="step"><b>02 가설</b>4-bit dequant 비용</div><div class="step"><b>03 통제</b>같은 모델·예산, 실행 경로 변경</div><div class="step"><b>04 반증</b>eager에서 처리량 회복</div><div class="step"><b>05 결론 제한</b>kernel root cause는 NOT_MEASURED</div></div></section>
<section class="panel"><h2>채택 판단</h2><p class="finding"><b>{html.escape(summary['recommendation']['variant'])}</b><br>{html.escape(summary['recommendation']['reason'])}.</p><p class="finding warning">8B BF16과 14B AWQ는 크기가 다르므로 순수한 양자화 효과를 식별한 실험은 아닙니다. 실제 24GB 카드 성능도 아직 주장하지 않습니다.</p></section>
<footer>18 immutable runs · 6 configurations · 3 repetitions · vLLM 0.9.2 · RTX A6000</footer>
<script id="data" type="application/json">{payload}</script><script>
const data=JSON.parse(document.getElementById('data').textContent);const select=document.getElementById('metric');const bars=document.getElementById('bars');
function draw(){{const key=select.value;const rows=data.configurations;const max=Math.max(...rows.map(r=>r[key]));bars.innerHTML=rows.map(r=>`<div class="bar-row"><div class="bar-label">${{r.variant}}</div><div class="track"><div class="bar" style="width:${{(r[key]/max*100).toFixed(1)}}%"></div></div><div class="value">${{Number(r[key]).toLocaleString(undefined,{{maximumFractionDigits:2}})}}</div></div>`).join('')}}select.addEventListener('change',draw);draw();
</script></main></body></html>"""


def build(results_dir: Path, artifacts: Path, portfolio: Path) -> dict[str, Any]:
    summary = build_summary(load_runs(results_dir))
    artifacts.mkdir(parents=True, exist_ok=True)
    portfolio.parent.mkdir(parents=True, exist_ok=True)
    (artifacts / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(summary, artifacts / "benchmark.csv")
    write_report(summary, artifacts / "REPORT.md")
    portfolio.write_text(render_html(summary), encoding="utf-8")
    return summary


def check(artifacts: Path, portfolio: Path) -> dict[str, Any]:
    summary_path = artifacts / "summary.json"
    if not summary_path.is_file() or not portfolio.is_file():
        raise ValueError("산출물이 없습니다. 먼저 build를 실행하세요.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["protocol"] != {
        "runs": 18, "configurations": 6, "repeats_per_configuration": 3,
        "concurrency": 10, "requests_per_run": 30, "vllm_version": "0.9.2",
    }:
        raise ValueError(f"protocol drift: {summary['protocol']}")
    if summary["evidence_boundary"]["target_card_claim"] != "NOT_EXECUTED":
        raise ValueError("native GPU evidence boundary was weakened")
    if not math.isclose(summary["findings"]["awq_throughput_recovery_x"], 5.47, abs_tol=.05):
        raise ValueError("AWQ recovery finding drifted")
    html_text = portfolio.read_text(encoding="utf-8")
    for token in ("Quantization Autopsy", "NOT EXECUTED", "NOT_MEASURED"):
        if token not in html_text:
            raise ValueError(f"portfolio token missing: {token}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "check"))
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--portfolio", type=Path, default=DEFAULT_PORTFOLIO)
    args = parser.parse_args()
    if args.command == "build":
        summary = build(args.results_dir, args.artifacts, args.portfolio)
        print(json.dumps({"protocol": summary["protocol"], "findings": summary["findings"]},
                         ensure_ascii=False, indent=2))
    else:
        summary = check(args.artifacts, args.portfolio)
        print(f"PASS: {summary['protocol']['runs']} runs, "
              f"{summary['protocol']['configurations']} configurations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
