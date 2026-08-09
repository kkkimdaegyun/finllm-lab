# FinLLM Lab v0.2 — 이력서·면접 요약

> 정확성 경계: 모든 성능 숫자는 RTX A6000에서 수행한 `memory-budget-emulation` 관측값이다. 실제 24GB GPU native 결과가 아니며, v0.2 통합 release 최종 판정은 `FAIL`이다.

## 1. 이력서용 프로젝트 설명 3줄

금융 내부문서용 단일 GPU Private RAG에서 모델·양자화 구성을 품질, TTFT, 처리량, peak VRAM, ACL 기준으로 비교하고 revision/provenance를 JSON schema로 고정했다. A6000에서 Qwen3-8B BF16과 Qwen3-14B AWQ를 반복 측정해 AWQ 저성능에 대한 초기 가설을 추가 통제 실험으로 수정했다. Serving, metrics, regression, incident, rollback layer를 확장하고 독립 cross-review에서 integration과 fail-open 결함을 재현해 release를 보류했다.

## 2. 이력서 bullet

- 합성 금융 corpus 16개·60문항 평가셋에서 answer correctness, groundedness, citation, abstention, ACL, prompt injection을 deterministic하게 평가하는 harness와 provenance 계약을 활용했다.
- RTX A6000 한 장에서 concurrency 10, 30 requests, 3회 반복으로 BF16/AWQ 구성의 TTFT·throughput·VRAM을 비교했으며, A6000 관측과 target GPU 추정을 schema 수준에서 분리했다.
- Qwen3-14B-AWQ graph-enabled 저성능을 AWQ dequantization 문제로 단정하지 않고 `--enforce-eager` 통제 실험을 추가해 class-ceiling 평균 output 313.238 tok/s를 재측정했다.
- `/health`, `/ready`, `/metrics`, startup validation, bounded graceful drain을 포함한 single-GPU serving layer와 pinned container 구성을 별도 A 파트에서 구현·검증했다(실제 A6000 Compose 통합은 `PENDING_VALIDATION`).
- Prometheus/Grafana, regression gate, incident/rollback 구현을 cross-review해 stale evaluation, zero-stage pass, failed-restart state mutation을 재현하고 최종 release를 `FAIL`로 판정했다.

## 3. 기술면접 30초 설명

“이 프로젝트는 LLM API 데모가 아니라 단일 GPU에서 어떤 금융 RAG 구성이 운영 가능한지를 증거로 판단하는 실험입니다. 품질·TTFT·처리량·VRAM·ACL을 같은 provenance로 측정했고, AWQ가 느리다는 초기 해석을 추가 eager 실험으로 반증했습니다. 이후 serving과 LLMOps layer를 만들었지만 cross-review에서 metric contract와 regression/rollback fail-open을 직접 재현해 완료라고 쓰지 않고 release를 보류했습니다.”

## 4. 기술면접 2분 설명

“문제는 금융 내부문서를 외부 API 없이 single GPU에서 서비스하는 것이었습니다. 가장 큰 모델을 돌리는 대신 quality 90 이상, P95 TTFT 2초 이하, concurrency 10, OOM 0, ACL violation 0을 기준으로 잡았습니다. 16개 합성 문서와 60문항 evaluation을 만들고 model/tokenizer revision, prompt, corpus, retriever hash를 result JSON에 기록했습니다.

A6000의 executor budget을 24GiB-class로 제한해 8B BF16과 14B AWQ를 비교했습니다. 모든 속도 값은 A6000 관측이며 실제 4090 값이 아닙니다. 처음 graph-enabled AWQ가 약 57 tok/s여서 dequantization overhead를 의심했지만, `--enforce-eager`를 통제 변수로 추가하자 14B AWQ class-ceiling 세 반복 평균이 313.238 tok/s로 바뀌었습니다. 그래서 원인을 AWQ 자체로 단정하지 않고 graph execution path와 연관된 현상으로 수정했습니다.

v0.2에서는 health/readiness, metrics, pinned container, Prometheus/Grafana, alert, regression, incident와 rollback을 추가했습니다. 마지막 cross-review에서 정상 테스트 230개 통과만 보지 않고 실패 경로를 실행했습니다. stale evaluation 재사용, 모든 gate skip인데 exit 0, restart 실패 뒤 active state 변경을 재현했고, A/B가 별도 트리에 있으며 metric contract도 맞지 않는다는 것을 확인했습니다. 그래서 최종 판정은 FAIL입니다. 제가 강조하는 점은 AI를 구현자와 리뷰어로 사용하더라도 최종 판단은 contract, deterministic test, schema와 실제 측정으로 통제했다는 것입니다.”

## 5. 예상 질문 10개와 보여줄 evidence

| 질문 | 답변의 핵심 | repository evidence |
|---|---|---|
| 왜 14B AWQ를 후보로 골랐나? | 같은 A6000 workload에서 quality와 memory observation이 유리했기 때문이며 native fit 확정은 아님 | `results/2026-08-08c-*`, `decisions/0004-profile-a-model-revised.md` |
| A6000에서 24GB 조건을 어떻게 만들었나? | executor budget을 제한한 emulation; 실제 24GB GPU와 구분 | `configs/profiles.json`, result `evidence_type` |
| TTFT가 왜 server와 user 두 종류인가? | client queue와 server scheduling을 분리하기 위해서 | result `p95_ttft_ms`, `p95_user_ttft_ms`, `p95_client_queue_ms` |
| AWQ 오진을 어떻게 고쳤나? | eager on/off 추가 실험, 세 반복 비교, 원인 표현 범위 축소 | `2026-08-08b` vs `2026-08-08c` results, ADR-0004 |
| 품질 97.667은 어떻게 계산되나? | 네 평가 축의 deterministic scoring; 60 cases | `scripts/rag_eval.py`, `datasets/eval-v0.1.jsonl` |
| 보안은 해결됐나? | ACL violation 0이지만 injection 2/5가 남음 | `ops/evidence/gate-baseline-gpu.json` |
| health와 ready를 왜 나눴나? | process liveness와 dependency/admission 상태가 다르기 때문 | A `service/runtime.py`, service endpoint tests |
| observability가 왜 release blocker인가? | 실제 A metric/label과 B PromQL이 맞지 않고 target도 비어 있음 | 두 cross-review contract, `gateway.json`, alert rules |
| regression gate가 있는데 왜 FAIL인가? | stale output과 zero-stage pass로 fail-open이 재현됨 | `scripts/regression_gate.py`, final review JSON |
| rollback을 했는데 왜 완료가 아닌가? | host restart evidence는 있으나 immutable digest, atomic state, `/ready` 확인이 없음 | `rollback-log.jsonl`, `rollback_release.py`, release manifest |

## 6. 표현 규칙

사용 가능한 표현:

- “A6000에서 관측했다”
- “memory-budget-emulation에서 후보를 비교했다”
- “GPU-free unit/contract test로 검증했다”
- “host-vLLM rollback 실험을 수행했다”
- “release blocker를 재현해 배포를 보류했다”

사용하면 안 되는 표현:

- “RTX 4090에서 315 tok/s를 달성했다”
- “24GB GPU 적합성을 검증했다”
- “production closed loop를 완성했다”
- “완전한 prompt injection 방어를 구현했다”
- “immutable container rollback을 검증했다”
