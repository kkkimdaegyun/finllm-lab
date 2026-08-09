# FinLLM Lab v0.2 — 이력서·면접 요약

> 정확성 경계: 운영 rehearsal은 actual RTX A6000에서 실행했다. 과거 24GB-class
> 성능/메모리 수치는 A6000 `memory-budget-emulation`이며 actual 24GB native 결과가 아니다.

## 1. 이력서용 프로젝트 설명 3줄

금융 문서용 단일 GPU Private RAG에서 모델·양자화 구성을 품질, TTFT, 처리량,
peak VRAM, ACL 기준으로 비교하고 provenance를 schema로 고정했다. AWQ 저성능에 대한
초기 해석을 eager 통제 실험으로 수정했다. v0.2에서 pinned Compose, readiness/metrics,
Prometheus/Grafana, fail-closed regression, incident와 immutable rollback을 actual A6000
rehearsal로 연결했다.

## 2. 이력서 bullet

- 합성 금융 corpus 16개와 60문항에서 correctness, groundedness, citation,
  abstention, ACL, prompt injection을 deterministic하게 평가하고 27개 기존 result의
  model/tokenizer/prompt/eval/retriever provenance를 유지했다.
- RTX A6000에서 concurrency 10, 30 requests, n=3으로 BF16/AWQ의 TTFT·throughput·VRAM을
  비교하고 `memory-budget-emulation`을 native GPU 결과와 schema 수준에서 분리했다.
- Qwen3-14B-AWQ graph-enabled 저성능을 dequantization으로 단정하지 않고
  `--enforce-eager` 통제 실험을 추가해 class-ceiling 평균 313.238 tok/s를 재측정했다.
- CUDA base를 변경하지 않고 version-pinned API/vLLM container, `/health`, `/ready`,
  `/metrics`, startup validation과 body-delivery까지 포함한 graceful drain을 구현했다.
- actual A6000에서 11-stage regression PASS, Prometheus target 4개 up, 20/20 in-flight
  drain, service-down alert firing, immutable image rollback과 readiness verify를 실행했다.

## 3. 기술면접 30초 설명

“단일 RTX A6000 금융 RAG에서 품질, TTFT, 처리량, VRAM, ACL을 동일 provenance로
비교했습니다. AWQ가 느리다는 초기 해석을 eager 통제 실험으로 수정하고, pinned
Compose·Prometheus·회귀 gate·incident·immutable rollback까지 실제 실행으로 닫았습니다.
AI agent는 구현과 cross-review에 사용했지만 최종 판정은 test, schema, command output과
actual GPU evidence로 내렸습니다.”

## 4. 기술면접 2분 설명

“문제는 금융 내부문서를 외부 API 없이 single GPU에서 서비스하는 것이었습니다.
가장 큰 모델 대신 quality 90 이상, P95 TTFT 2초 이하, concurrency 10, OOM 0,
ACL violation 0을 기준으로 잡았습니다. 16개 합성 문서와 60문항 평가에서 revision과
retriever hash까지 result JSON에 기록했습니다.

A6000 executor budget을 제한해 8B BF16과 14B AWQ를 비교했습니다. 처음 graph-enabled
AWQ가 약 57 tok/s여서 dequantization을 의심했지만 eager 변수만 바꾼 세 반복에서
313.238 tok/s가 나왔습니다. 그래서 원인을 AWQ 자체로 단정하지 않고 graph-enabled
path와 함께 나타난 현상으로 좁혔습니다.

v0.2에서는 pinned container, health/readiness, metrics, Prometheus/Grafana, regression,
incident, rollback을 추가했습니다. cross-review에서 generation failure readiness,
response body drain, stale/skip gate, failed rollback state mutation을 재현해 수정했습니다.
마지막 actual A6000 rehearsal에서 153개 test, 27개 schema, 11개 gate를 통과했고,
20개 요청 drain 후 GatewayDown alert와 immutable recovery까지 확인했습니다. 단, actual
24GB native 성능과 production traffic은 검증했다고 주장하지 않습니다.”

## 5. 예상 질문 10개와 evidence

| 질문 | 답변 핵심 | repository evidence |
|---|---|---|
| 왜 14B AWQ를 골랐나? | 같은 A6000 workload에서 quality와 memory observation이 유리 | `results/2026-08-08c-*`, ADR-0004 |
| 24GB 조건은 어떻게 만들었나? | executor budget emulation; native fit과 구분 | `configs/profiles.json`, result `evidence_type` |
| TTFT가 왜 두 종류인가? | server scheduling과 client queue를 분리 | result `p95_ttft_ms`, `p95_user_ttft_ms` |
| AWQ 오진을 어떻게 고쳤나? | eager on/off 통제 실험과 세 반복 | `2026-08-08b`/`08c`, ADR-0004 |
| 품질은 어떻게 계산하나? | expected fact/citation/refusal 기반 60 cases | `scripts/rag_eval.py`, dataset |
| 보안은 해결됐나? | ACL 0, injection 1/5는 known limitation | `gate-all.json` |
| health와 ready를 왜 나눴나? | liveness와 dependency/admission은 다른 상태 | `service/runtime.py`, endpoint tests |
| shutdown 안전성은 어떻게 증명했나? | readiness 503 전환과 20/20 body 완주 | INC-003, `graceful-clients.json` |
| gate fail-open은 어떻게 막았나? | unique output, non-zero, skip-only failure | `scripts/regression_gate.py`, tests |
| rollback은 무엇을 검증하나? | full SHA/digest → restart → ready/build info → state | manifest, rollback log, verify log |

## 6. 표현 규칙

사용 가능한 표현:

- “actual A6000에서 v0.2 운영 rehearsal을 실행했다”
- “A6000 memory-budget-emulation에서 후보를 비교했다”
- “immutable local image digest로 rollback하고 readiness를 확인했다”

사용하면 안 되는 표현:

- “RTX 4090/5090에서 315 tok/s를 달성했다”
- “actual 24GB GPU 적합성을 검증했다”
- “완전한 prompt injection 방어를 구현했다”
- “production customer traffic과 high availability를 검증했다”
