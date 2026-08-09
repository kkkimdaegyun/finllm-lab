---
title: FinLLM Lab v0.2 최종 기술 보고서
subtitle: 단일 GPU 금융 Private RAG — 측정에서 운영 안전성까지, 그리고 미완료 release 판정
author: FinLLM Lab
date: 2026-08-09
---

# 1. Executive Summary

FinLLM Lab은 금융 내부문서를 다루는 단일 GPU Private RAG를 대상으로, 모델 선택을 “더 큰 모델”이 아니라 **품질·지연·처리량·메모리·보안의 동시 제약**으로 다룬 프로젝트다. v0.1은 합성 corpus, deterministic retriever, ACL, 60문항 evaluation harness, result schema, load test와 GPU profile을 만들었다. A6000 한 장에서 memory budget을 제한해 Qwen3-8B BF16과 Qwen3-14B AWQ를 비교했고, 결과 provenance를 JSON으로 남겼다.

v0.2의 목표는 그 실험 stack을 Build → Deploy → Observe → Alert → Diagnose → Regression Test → Rollback → Evidence의 운영 loop로 확장하는 것이었다. Serving/Deployment(A)와 Observability/Reliability(B)를 분리 구현한 결과, 각 파트에는 검증 가능한 코드와 증거가 생겼다. A는 pinned container, API, readiness, metrics, startup validation과 graceful drain 단위 테스트를 갖췄다. B는 Prometheus/Grafana, alert, regression gate, 두 incident 기록과 rollback 도구를 갖췄다.

그러나 **최종 release 판정은 FAIL**이다. 두 파트가 서로 다른 디렉터리에 남아 하나의 Git release가 아니고, service/network/metric contract가 불일치한다. B의 gateway target은 비어 있고 A가 내보내는 metric으로 B의 핵심 alert를 계산할 수 없다. GPU regression과 rollback에는 fail-open 경로가 재현됐다. 실제 A6000에서 A+B Compose 전체를 기동하고 alert·drain·digest rollback까지 연결한 증거도 없다.

이 판정은 v0.1 측정이나 개별 구현을 무효화하지 않는다. 오히려 이 프로젝트의 원칙—측정하지 않은 숫자를 주장하지 않고, 실패를 설명으로 덮지 않으며, LLM 판단보다 deterministic evidence를 우선한다—을 최종 단계에도 적용한 결과다.

> Release status: **FAIL / DO_NOT_RELEASE**  
> Native 24GB GPU validation: **NOT_EXECUTED**  
> Integrated A6000 v0.2 rehearsal: **PENDING_VALIDATION**

# 2. 프로젝트를 시작한 이유

금융 Private RAG는 일반적인 챗봇 데모와 실패 비용이 다르다. 외부 API에 내부문서를 보낼 수 없고, 권한이 없는 문서가 retrieval 단계에서 노출되어도 안 된다. 한정된 GPU에서 여러 사용자가 요청할 때 품질만 높거나 메모리에만 들어가는 구성은 운영 가능한 구성이 아니다. 또한 benchmark 한 번의 평균값보다, 어떤 revision과 prompt, retriever, 평가셋으로 측정했는지가 더 중요하다.

따라서 프로젝트의 질문을 다음처럼 정의했다.

> 이미 평가 및 추론 구성이 정해진 단일 GPU 금융 Private RAG를 재현 가능하게 배포하고, 운영 중 품질·성능 이상을 관측하고, 잘못된 변경을 차단하며, 장애 발생 시 안전하게 복구할 수 있는가?

# 3. 문제 정의

판정 기준은 기능 개수 대신 서로 충돌하는 제약으로 구성됐다.

| 축 | 질문 | 저장소의 기준/증거 |
|---|---|---|
| 품질 | 답이 맞고 근거가 있으며 필요한 경우 거부하는가 | `scripts/rag_eval.py`, `datasets/eval-v0.1.jsonl` |
| 보안 | ACL 위반과 prompt injection을 측정하는가 | retrieval tests, 5개 injection case |
| 성능 | concurrency 10에서 TTFT와 처리량이 어떤가 | `scripts/load_test.py`, result JSON |
| 메모리 | executor 예산과 실제 peak VRAM을 구분하는가 | `scripts/gpu_watch.py`, result schema |
| 재현성 | model/tokenizer/prompt/corpus/retriever provenance가 있는가 | configs, schemas, result records |
| 운영 | health와 readiness가 분리되고 장애를 탐지·복구하는가 | A service, B monitoring/ops |
| 변경 안전성 | 잘못된 변경이 non-zero로 차단되는가 | regression gate, CI |

# 4. 운영 제약

## 4.1 On-premise와 single GPU

대상은 Kubernetes나 multi-node cluster가 아니다. 단일 GPU 부서 서비스가 범위다. 이 제약은 단순화이면서 동시에 single point of failure다. 모델 재기동 중 무중단 전환을 보장하지 않으며, incident 실험에서도 사용자 체감 중단 시간은 `NOT_MEASURED`다.

## 4.2 GPU memory

실험 장비는 48GiB NVIDIA RTX A6000이다. `gpu_memory_utilization=0.50` 또는 `0.46`은 A6000 executor가 쓸 예산을 줄여 24GiB-class 조건을 탐색하는 실험 장치다. 이것은 실제 24GB GPU에서의 native 실행이 아니다. GPU architecture, memory bandwidth, driver/커널 경로가 다르므로 A6000의 tok/s와 TTFT를 RTX 4090/5090 값으로 바꿔 부를 수 없다.

## 4.3 Security

corpus는 합성 문서 16개이며 실제 고객 데이터가 아니다. ACL은 생성 모델의 선의에 맡기지 않고 retrieval 전 필터로 적용한다. 단, prompt injection 5건 중 2건 성공이 남아 있다. baseline gate는 이 결함을 해결한 것이 아니라 더 악화되는 것을 막는다.

## 4.4 Reproducibility

주요 model과 tokenizer revision은 40자리 commit SHA로 고정됐다. 최종 실험의 Qwen3-14B-AWQ revision은 `31c69efc29464b6bb0aee1398b5a7b50a99340c3`, prompt는 `prompt-v0.1`, eval set은 `eval-v0.1`, retriever hash는 `11d1f8cfeb42`다.

# 5. FinLLM v0.1에서 만든 것

v0.1의 핵심은 다음의 재사용 가능한 measurement stack이다.

- synthetic 금융 corpus 16개와 ACL metadata
- 60문항 evaluation set과 deterministic scoring
- answer correctness, groundedness, citation accuracy, abstention safety 분해
- prompt injection 및 unauthorized retrieval 진단
- deterministic sparse retriever와 config hash
- OpenAI-compatible inference adapter
- concurrent load test와 server/user latency 분리
- `memory-budget-emulation`/`native-gpu-validation`을 구분하는 result schema
- model/tokenizer/prompt/corpus/eval/retriever provenance
- GPU peak sampling과 반복 측정

v0.2는 평가 로직을 복제하지 않고 이 모듈을 adapter로 재사용하는 것을 원칙으로 했다. A 서비스는 `scripts.rag_index.Retriever`와 `scripts.rag_eval.build_messages`를 사용한다.

# 6. v0.2에서 부족했던 것

v0.1 결과는 “어떤 구성이 실험에서 나았는가”를 답하지만 다음 질문에는 답하지 못했다.

- process liveness와 traffic readiness가 분리되는가?
- model/index가 준비되지 않았을 때 silent fallback 없이 실패하는가?
- SIGTERM 이후 새 요청을 막고 accepted request를 drain하는가?
- request/retrieval/generation latency를 운영 중 볼 수 있는가?
- threshold를 넘으면 실제 alert가 발생하는가?
- 품질이 나빠진 변경을 merge/deploy 전에 막는가?
- 장애 후 known-good revision으로 되돌리고 readiness를 확인하는가?

v0.2는 이 질문을 닫기 위한 layer였다. 최종 감사에서는 개별 구성요소 존재가 아니라 연결된 loop의 증거를 요구했다.

# 7. 전체 Architecture와 현재 상태

의도한 구조는 다음과 같다.

```text
Client
  → FinLLM API (/health, /ready, /metrics)
      → ACL Retriever / frozen index
      → vLLM OpenAI API
          → single NVIDIA GPU

Prometheus ← API metrics / vLLM metrics / DCGM exporter
    ↓
Grafana + alert rules → runbook → diagnose → rollback

CI → deterministic tests → schema → CPU/GPU regression gate → release manifest
```

하지만 제출된 실제 topology는 분리돼 있다.

| 위치 | 존재하는 v0.2 범위 | Git 상태 |
|---|---|---|
| `/home/dgkim/dgkim/FinLLM:0.2` | A: `deploy/`, `service/`, deploy scripts/tests | 자체 Git repo가 아님 |
| `/home/dgkim/dgkim/FinLLM-0.2` | B: `monitoring/`, `ops/`, regression, CI | canonical `main@47cbc5a` |

A는 `finllm-api`와 Compose default network를 정의한다. B는 `finllm-gateway`와 external `finllm-net`을 요구한다. A는 label 없는 6개 application metric을 내보내지만 B alert는 `status` label, `finllm_ready`, `finllm_generation_ttft_seconds`, `finllm_build_info`를 사용한다. 이 차이 때문에 그림의 Prometheus 화살표는 현재 실제 연결이 아니다.

# 8. 모델/양자화 선택 과정

최종 비교는 2026-08-08c eager 결과 세 반복이다. 아래 값은 세 result의 산술평균이고, 모두 **A6000 memory-budget-emulation**이다.

| 모델/조건 | Quality | server P95 TTFT | user P95 TTFT | output tok/s | peak VRAM | max concurrency @8192 | 증거 유형 |
|---|---:|---:|---:|---:|---:|---:|---|
| Qwen3-8B BF16, 0.50, eager | 95.926 | 77.329ms | 1,344.653ms | 286.955 | 24.006GiB | 7.31 | memory-budget-emulation |
| Qwen3-14B AWQ, 0.50, eager | 97.667 | 129.828ms | 1,310.587ms | 313.238 | 23.836GiB | 11.05 | memory-budget-emulation |
| Qwen3-14B AWQ, 0.46, eager | 97.667 | 129.995ms | 1,273.402ms | 315.331 | 21.961GiB | 9.53 | memory-budget-emulation |

모든 행은 concurrency 10, 30 requests, repetitions 3, `max_model_len=8192`, `max_num_seqs=10` 조건이다. 세 구성 모두 기록된 30개 요청에서 error rate 0, OOM 0이었다. 그러나 `max_concurrency_at_max_model_len`은 vLLM이 full 8192-token budget으로 산출한 capacity 지표이고 실제 요청 concurrency와 같은 뜻이 아니다. 특히 0.46 구성의 9.53을 “full-length concurrency 10 충족”으로 쓰면 안 된다.

0.46 AWQ는 A6000에서 관측한 peak VRAM이 가장 낮고 품질이 높았다. 이것은 다음 단계 native validation 후보를 정하는 근거다. 실제 24GB 카드 적합성의 최종 증거는 아니다.

원본:

- `results/2026-08-08c-profile-a-qwen3-8b-bf16-classceiling-eager-r{1,2,3}.json`
- `results/2026-08-08c-profile-a-qwen3-14b-awq-classceiling-eager-r{1,2,3}.json`
- `results/2026-08-08c-profile-a-qwen3-14b-awq-deploymentmatched-eager-r{1,2,3}.json`

# 9. Evaluation 설계

60문항은 answerable, abstention, unauthorized, injection 유형으로 나뉜다. 평가는 단일 “LLM judge 점수”가 아니라 명시적 expected facts와 citations, refusal 조건으로 계산한다. ACL violation은 전체 run을 실패시키고, unauthorized 답변에서 leaked fact를 말하면 거부 문구가 있어도 실패한다.

최종 14B AWQ 결과는 quality 97.667, answer correctness 100.0, groundedness 93.333, citation accuracy 100.0, abstention safety 96.667이다. ACL violation은 0이지만 injection success는 2다. 따라서 “보안 해결”이 아니라 “ACL 회귀는 없고 prompt injection 결함은 공개돼 있음”이 정확한 표현이다.

# 10. Serving / Deployment

A 구현은 다음 계약을 코드와 GPU-free test로 갖췄다.

| 항목 | 구현 | 이번 감사에서 확인한 것 |
|---|---|---|
| pinned base | Python 3.10.12 digest, CUDA 12.2.2 base digest | Dockerfile line과 local image ID |
| dependency | vLLM 0.9.2, torch 2.7.0+cu126, transformers 4.53.2 | lock/build evidence |
| non-root | uid/gid 10001 | Dockerfile와 evidence |
| cache/secret | named HF cache, secret file | Compose render |
| `/health` | process liveness only | unit test pass |
| `/ready` | app/retriever/endpoint/model/admission | success/failure unit tests pass |
| `/metrics` | six application metrics | exposition test pass |
| startup | config/index/model/revision/endpoint validation | failure tests pass |
| shutdown | admission close + bounded drain | deterministic unit test pass |

실행한 명령:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
→ Ran 111 tests, OK (skipped=3)

docker compose --file deploy/compose.yaml config --quiet
→ exit 0
```

확인한 local image:

- API: `sha256:05a55cc54bd8b88461434a775bfcdb938d5d097fc7e12879249fb5986483c8d3`
- vLLM: `sha256:ec36cea2d1d23d28bc5ce56d33ab1bc5f96258a870181b161d1df8269fd99a3d`

실제 model을 로드한 Compose 실행은 `NOT_EXECUTED`; A6000 서비스 통합은 `PENDING_VALIDATION`; A deployment latency/throughput/VRAM은 `NOT_MEASURED`다.

# 11. Observability

B는 Prometheus 5초 scrape, vLLM file discovery, DCGM exporter, Grafana dashboard와 alert rule을 정의했다. vLLM raw metric 이름과 TTFT bucket에 2.0초 경계가 없다는 증거도 남겼다. 실제 host-vLLM outage에서 `up{job="vllm"}` alert evidence가 있다.

하지만 A application observability는 닫히지 않았다.

- `monitoring/prometheus/targets/gateway.json`은 빈 배열이다.
- B dashboard/alert의 `finllm_ready`, `finllm_generation_ttft_seconds`, `finllm_build_info`는 A에 없다.
- B error-rate 식의 `{status=~"5.."}` label은 A `finllm_requests_total`에 없다.
- A histogram에는 `2.5` bucket이 있고 B contract는 `2.0` bucket을 요구한다.
- DCGM query에 service GPU selector가 없어 두 A6000 host의 다른 GPU workload도 alert 대상이 된다.

따라서 dashboard JSON이 valid하고 query 문자열이 존재하는 것과, 실제 service 상태를 올바르게 관찰한다는 것은 다르다.

# 12. SLO와 Alert

`configs/profiles.json`의 hard policy는 quality ≥90, P95 TTFT ≤2,000ms, error rate ≤1%, OOM 0, concurrency 10이다. B test는 alert threshold가 config와 같음을 확인한다. 다만 1분 rate window와 1~2분 `for`는 `PENDING_THRESHOLD_VALIDATION`이다.

프로젝트의 load burst는 약 2초다. 재현용 synthetic series에서는 P95가 30초 시점에 4.875초였지만 70초에 rate 결과가 사라져 2분 `for` 이후 firing하지 않았다. 숫자가 config와 같아도 window와 지속시간이 workload와 맞지 않으면 이상을 놓칠 수 있다.

# 13. Regression Gate

CPU stage는 unit tests, result schema, eval set integrity, retrieval ACL, retriever hash, prompt revision, alert threshold consistency를 검사한다. GPU stage는 live evaluation, ACL, quality, injection regression을 검사한다. CPU-only CI가 GPU stage를 `skipped`로 표시하는 설계 방향은 맞다.

하지만 직접 재현된 두 fail-open이 release를 차단한다.

1. `stage_smoke_evaluation`은 기존 `gate-eval.json`을 지우지 않고 subprocess return code를 검사하지 않는다. endpoint/index가 실패해도 stale 파일이 남아 있으면 이후 stage가 그 결과를 사용해 exit 0이 될 수 있다.
2. 모든 11개 stage를 `--skip`하면 fail이 없다는 이유로 `overall=pass`, `pass=0`, `skipped=11`, exit 0이 된다.

또한 `current-release.json`은 `regression_gate.stage="all"`이라 쓰면서 CPU report를 가리킨다. promote는 report의 내부 overall/stage/provenance를 검증하지 않고 manifest status와 파일 존재만 신뢰한다.

# 14. Incident 실험

INC-001은 vLLM upstream outage를 의도적으로 만들고 target/alert/GPU process/recovery evidence를 남겼다. INC-002는 `--enforce-eager`를 제거해 graph-enabled 경로를 실행한 뒤 GPU memory alert와 host-vLLM rollback을 기록했다.

INC-002 evidence의 관측값:

| 상태 | GPU1 FB_USED | 출처 |
|---|---:|---|
| graph-enabled | 26,570MiB | `ops/evidence/rollback-demo/03-alerts-graph-enabled.json` |
| rollback 후 | 22,362MiB | incident report/evidence |

alert snapshot에는 `state="firing"`이 존재하므로 실제 firing evidence는 있다. 다만 `activeAt=05:15:00.934Z`는 pending 조건이 시작된 시간이며 `for:1m` 규칙의 firing 시간이 아니다. 문서의 “+5초 firing”은 증거와 맞지 않는다.

# 15. Rollback

INC-002의 host-vLLM restart는 return code 0, elapsed 33.112초로 기록됐다. `/v1/models`와 GPU metric을 기준으로 baseline 복귀도 문서화됐다. 이 값은 container rollback time이나 사용자 downtime이 아니다.

release-grade rollback으로는 세 가지가 부족하다.

- manifest `image_digest`가 null이고 restart는 mutable shell/host `.venv`에 의존한다.
- `--exec`가 없거나 restart가 실패해도 `current-release.json`을 target으로 바꾼다.
- verify는 `/v1/models`만 필수로 보고 gateway metrics 실패를 “A 미배포”로 건너뛰며 `/ready`를 확인하지 않는다.

따라서 “host process rollback 실험을 수행했다”는 사실과 “안전한 immutable release rollback이 있다”는 주장을 분리해야 한다.

# 16. Claude ↔ Codex Cross Review

분업은 ownership과 interface contract를 먼저 두고 A/B를 독립 구현한 뒤 상대 구현을 검토하는 방식이었다. 이 구조는 A/B contract 불일치, stale evaluation, all-skip pass, rollback state mutation 같은 결함을 실제로 드러냈다.

중요한 운영 원칙은 다음과 같다.

```text
LLM = 구현자와 검토자
Contract = 협업 경계
Reproduction = finding 근거
Test / schema / actual run = 최종 judge
```

다만 contract가 두 파일로 갈라진 상태에서 합의/통합 단계가 없었던 것이 이번 release 실패의 직접 원인이다.

# 17. 주요 실패와 오진

## 사례 A — AWQ throughput 오진

**BEFORE**  
graph-enabled AWQ class-ceiling 결과의 약 57.2 tok/s를 보고 AWQ dequantization overhead가 원인이라고 해석할 수 있었다.

**Problem**  
이 가설만으로는 8B BF16과의 차이가 weight format 때문인지 CUDA execution path 때문인지 구분할 수 없다.

**Investigation**  
`--enforce-eager`를 추가해 graph path를 바꾸고 동일 모델/revision/evaluation 조건으로 세 번 재측정했다.

**AFTER**  
14B AWQ class-ceiling 평균은 313.238 tok/s, server P95 TTFT 129.828ms가 됐다. 같은 조작에서 8B BF16은 약 296→286.955 tok/s로 소폭 하락했다. 따라서 “AWQ 자체가 느리다”는 해석은 반증됐고, 느린 현상은 graph-enabled 경로와 연관됐다.

**Evidence boundary**  
어떤 내부 kernel이 정확한 root cause인지는 profiling하지 않았다. “CUDA graph가 AWQ 역양자화보다 근본 원인”이라고 확정하면 과장이다. 정확한 결론은 **이 모델/환경에서 성능 저하가 graph-enabled path와 함께 나타났고 eager에서 사라졌다**다.

## 사례 B — 테스트 PASS와 release safety의 혼동

**BEFORE**  
B tree의 119 tests와 regression report pass는 change safety가 구현된 인상을 준다.

**Problem**  
unit test는 stale output과 zero-stage gate, failed restart 이후 state mutation을 검사하지 않았다.

**Investigation**  
실패 endpoint에 stale `gate-eval.json`을 둔 실행, 모든 stage skip 실행, `restart_command=false`인 isolated rollback을 직접 재현했다.

**AFTER**  
세 경우 모두 release 판단을 잘못 만들 수 있는 exit/state가 확인됐다. 최종 judge는 테스트 개수 대신 negative-path reproduction을 우선해 FAIL을 냈다.

# 18. 실제 해결 과정과 미해결 경계

프로젝트가 실제로 해결한 문제:

- model/tokenizer revision과 evaluation provenance를 result schema로 강제
- A6000 관측과 target GPU 추정을 evidence type으로 분리
- retrieval ACL을 deterministic test로 검증
- health/readiness를 의미상 분리한 service core
- shutdown admission/drain을 단위 수준에서 검증
- vLLM/GPU incident evidence와 host rollback audit log 생성
- AI cross-review finding을 reproduction으로 다시 판정

아직 해결하지 못한 문제:

- 두 파트의 canonical integration
- Prometheus application scrape/alert contract
- fail-closed regression and promotion
- immutable digest rollback and readiness verification
- actual A6000 end-to-end Compose rehearsal
- native 24GB GPU validation

# 19. 최종 측정 결과의 해석

가장 유력한 다음 검증 후보는 Qwen3-14B-AWQ, revision `31c69e…`, `gpu_memory_utilization=0.46`, `--enforce-eager`다. A6000 memory-budget-emulation에서 세 반복 평균 quality 97.667, server P95 TTFT 129.995ms, user P95 TTFT 1,273.402ms, output 315.331 tok/s, peak VRAM 21.961GiB를 관측했다.

이 수치가 의미하는 것:

- 같은 A6000/같은 workload에서 비교 후보보다 좋은 quality와 낮은 memory observation을 보였다.
- concurrency 10의 30-request load에서 기록된 error/OOM은 0이었다.
- actual 24GB GPU에서도 같은 memory fit, throughput, latency가 나온다는 뜻은 아니다.
- A 서비스 container를 거친 end-to-end latency라는 뜻도 아니다.

# 20. 현재 시스템의 남은 문제

1. **Integration:** A/B를 한 commit으로 합치고 contract를 하나로 고정해야 한다.
2. **Application alerts:** 실제 A metrics로 service down, error rate, latency alert를 재현해야 한다.
3. **Gate semantics:** fresh output, executed-stage minimum, report integrity를 fail closed로 만들어야 한다.
4. **Rollback atomicity:** restart와 readiness가 성공한 후에만 current release를 바꿔야 한다.
5. **Immutability:** full Git SHA와 container digest를 release identity로 강제해야 한다.
6. **Security:** injection success 2/5를 줄이되 같은 evaluation semantics로 재측정해야 한다.
7. **External validity:** synthetic corpus/60 cases와 production traffic 사이의 차이를 검증해야 한다.

# 21. 이 프로젝트에서 배운 기술

- GPU executor budget과 process-level peak VRAM은 다른 값이다.
- TTFT는 server scheduling과 client queue를 분리해야 해석할 수 있다.
- quantization 성능 문제는 weight format을 원인으로 단정하기 전에 execution mode를 통제해야 한다.
- readiness는 liveness가 아니며 shutdown admission state까지 포함해야 한다.
- Prometheus metric 이름뿐 아니라 label과 histogram bucket도 interface contract다.
- regression gate의 핵심은 정상 경로가 아니라 실패 시 non-zero와 stale evidence 차단이다.
- rollback은 명령 실행이 아니라 immutable target, 성공 검증, state commit 순서를 포함한다.
- provenance가 없으면 정확한 숫자도 재사용 가능한 evidence가 아니다.
- AI review finding도 재현 전에는 의견이고, 재현된 command output 이후에야 근거가 된다.

# 22. 다음 프로젝트: Quantization Autopsy

다음 질문은 “AWQ가 빠른가 느린가”가 아니다.

- eager와 graph path에서 어떤 kernel/shape가 병목인가?
- quantized weight의 memory saving이 KV cache와 concurrency에 어떻게 재분배되는가?
- prompt length, output length, batch/concurrency 변화에 따라 TTFT와 decode throughput이 어떻게 갈라지는가?
- A6000 Ampere에서 관측한 현상이 Ada/Blackwell native target에서도 같은가?
- Nsight Systems/Compute, PyTorch profiler, vLLM metrics를 어떤 provenance schema로 연결할 것인가?

이 질문을 `Quantization Autopsy`로 이어가려면 먼저 v0.2의 integration blocker를 닫아, profiling 대상 release 자체가 재현 가능해야 한다.

# 23. 면접에서 설명한다면

## 30초

“단일 A6000 금융 Private RAG에서 모델 크기만 비교하지 않고 품질, TTFT, 처리량, VRAM, ACL을 하나의 evidence schema로 측정했습니다. AWQ가 느리다는 초기 해석을 추가 eager 실험으로 반증했고, serving·metrics·regression·incident·rollback까지 확장했습니다. 최종 감사에서는 AI가 만든 코드의 테스트 개수를 믿지 않고 stale evaluation과 rollback fail-open을 직접 재현해 release를 FAIL로 판정했습니다.”

## 가장 먼저 보여줄 세 가지

1. `2026-08-08c` result 세트와 ADR-0004: 오진을 추가 실험으로 수정한 과정.
2. A service readiness/shutdown tests와 두 interface contract: 운영 의미와 integration failure.
3. regression/rollback negative reproduction과 `final-release-review.json`: evidence가 결론을 바꾼 사례.

# Appendix A. 최종 검증 기록

| 검증 | 결과 |
|---|---|
| A tests | VERIFIED — 111 run, 3 GPU integration skipped |
| B tests | VERIFIED — 119 run |
| result schema | VERIFIED — 27/27 in both trees |
| A Compose config | VERIFIED — exit 0 |
| A local image IDs | VERIFIED |
| real A6000 v0.2 Compose | PENDING_VALIDATION |
| A metrics scraped by B | NOT_EXECUTED / incompatible current contract |
| native 24GB GPU | NOT_EXECUTED |
| immutable digest rollback | PENDING_VALIDATION |

# Appendix B. 핵심 source map

- Final judge: `docs/final-review/final-release-review.json`
- Result contract: `schemas/run-result.schema.json`
- Profile policy: `configs/profiles.json`
- A service source: `/home/dgkim/dgkim/FinLLM:0.2/service/`
- A deployment evidence: `/home/dgkim/dgkim/FinLLM:0.2/deploy/evidence/2026-08-09-a-part-validation.json`
- B monitoring: `monitoring/`
- B regression: `scripts/regression_gate.py`
- B rollback: `scripts/rollback_release.py`
- Incident evidence: `ops/evidence/`, `ops/incidents/`
- Canonical Git commit reviewed: `47cbc5a01320fb203a537392c7b209834225e05a`
