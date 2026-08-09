"""F3 (MAJOR) — TTFT metric 부재 + 비스트리밍 + SLO 경계(le=2) bucket 부재.

서비스를 띄우지 않는다. MetricRegistry를 직접 렌더링해 정적으로 확인한다.
"""
import re
import sys

import _common as C

sys.path.insert(0, str(C.A_ROOT))
from service.metrics import MetricRegistry  # noqa: E402

registry = MetricRegistry()
registry.request_started()
registry.observe_retrieval(0.01)
registry.observe_generation(0.5)
registry.request_finished(0.51, success=True)
exposition = registry.render_prometheus()

exposed = sorted({
    name for name in re.findall(
        r"^(finllm_[a-z0-9_]+?)(?:_bucket|_sum|_count)?[ {]", exposition, re.M)
})
print("=== 노출 metric ===")
for name in exposed:
    print("  ", name)
print(f"   총 {len(exposed)}종")

print("\n=== TTFT 계열 ===")
ttft = [n for n in exposed if "ttft" in n]
print("  ", ttft if ttft else "없음 — 이 프로젝트의 유일한 지연 SLO를 평가할 수 없다")

buckets = re.findall(r'finllm_request_duration_seconds_bucket\{le="([^"]+)"\}', exposition)
print("\n=== histogram bucket 경계 ===")
print("  ", " ".join(buckets))
print("   le=2 존재?:", "2" in buckets,
      "  <- benchmark_policy.p95_ttft_ms_max=2000 이 SLO 경계다")

source = (C.A_ROOT / "service" / "inference.py").read_text(encoding="utf-8")
print("\n=== 스트리밍 지원 ===")
print("   service/inference.py 에 'stream' 문자열:", "있음" if "stream" in source else "없음 (비스트리밍 확정)")
print("   -> first token 시각 자체가 존재하지 않으므로 TTFT는 정의 불가다.")
