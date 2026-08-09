# Runbook — P95 지연 악화 / 오류율 상승

대응 alert: `FinLLMHighP95TTFT`, `FinLLMVLLMHighP95TTFT`,
`FinLLMHighRequestErrorRate`, `FinLLMRequestQueueBuildup`

## Symptom

- Grafana "P95 latency가 악화되는가?" 패널이 2초 점선을 넘는다.
- 또는 "오류가 증가하는가?" 패널이 1% 점선을 넘는다.
- 사용자는 "첫 글자가 늦게 나온다"고 말한다.

## 0. 먼저 어느 TTFT인지 확인한다

이 프로젝트에서 가장 흔한 오진이다. 두 값은 다르다.

| 값 | 의미 | v0.1 baseline |
|---|---|---:|
| 서버 TTFT | 요청 dispatch → 첫 토큰 | 132ms |
| 사용자 TTFT | 요청 도착 → 첫 토큰 (대기 포함) | 1,287ms |

**10배 차이가 난다.** 서버 TTFT가 정상인데 사용자가 느리다고 하면 문제는
추론이 아니라 **대기열**이다. 3번으로 간다.

## 1. Check — 무엇을 먼저 보는가

```bash
# 지금 값
curl -s --data-urlencode 'query=histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[1m])))' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool

# 큐가 쌓였는가
curl -s --data-urlencode 'query=sum(vllm:num_requests_waiting)' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool

# preemption이 일어났는가 (KV cache 압박의 직접 신호)
curl -s --data-urlencode 'query=increase(vllm:num_preemptions_total[5m])' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool
```

Grafana에서는 `FinLLM Service — Profile A` 대시보드의 아래 3개를 순서대로 본다.

1. "queue가 쌓이는가?" — `waiting > 0`인가
2. "retrieval이 병목인가, generation이 병목인가?" — 두 P95의 상대 크기
3. "GPU가 포화되는가?" / "VRAM이 위험 수준인가?"

## 2. Diagnosis — 분기표

| 관측 | 원인 | 조치 |
|---|---|---|
| `retrieval p95` ≫ `generation p95` | 검색 병목. index 손상·재빌드 중·corpus 급증 | 4-A |
| `generation p95` 상승 + `waiting > 0` | 설계 동시성 10 초과 | 4-B |
| `waiting > 0` + `preemptions > 0` | KV cache 부족 | [gpu-oom.md](gpu-oom.md) |
| 서버 TTFT 정상 + 사용자 TTFT 높음 | 클라이언트 대기(버스트) | 4-B |
| `GPU_UTIL` 낮은데 느림 | GPU가 일하고 있지 않다 — upstream/네트워크/CPU 전처리 | 4-C |
| `SM_CLOCK` 하락 + `GPU_TEMP` 상승 | 열/전력 throttling | 4-D |
| 최근 배포 직후 시작됨 | 변경이 원인 | 5번 rollback |

**주의 — 양자화를 먼저 의심하지 마라.** v0.1에서 정확히 이 오진을 했다.
14B AWQ의 처리량 열세를 "Ampere AWQ 역양자화 비용"으로 설명했지만 실제
원인은 CUDA graph였다(`--enforce-eager`로 57.2 → 313.2 tok/s).
[ADR-0004](../../decisions/0004-profile-a-model-revised.md) 참조.
**그럴듯한 설명이 숫자와 맞는다는 것이 그 설명이 옳다는 뜻은 아니다.**
가설을 세웠으면 변수 하나만 바꿔 확인하고 넘어가라.

## 3. 대기열인지 확인하는 법

`load_test.py`가 두 값을 모두 보고한다.

```bash
.venv/bin/python scripts/load_test.py --model Qwen/Qwen3-14B-AWQ \
  --dataset datasets/smoke.jsonl --base-url http://127.0.0.1:8000/v1 \
  --concurrency 10 --requests 30 --output work/v02/triage-load.json
```

`p95_client_queue_ms`가 크면 서버가 아니라 도착률 문제다. 부하 모델 자체가
버스트(모든 요청이 t=0에 도착)라는 점도 함께 고려한다.

## 4. Mitigation

**4-A 검색 병목**
```bash
python3 scripts/rag_index.py config-hash --index work/v02/index-v02.json
# baseline과 다르면 index가 바뀐 것이다
```
baseline 해시는 `ops/baselines/profile-a-baseline.json`의 `retriever_config_hash`
(`11d1f8cfeb42`). 다르면 index를 baseline corpus로 재빌드한다.

**4-B 동시성 초과**
설계 동시성은 10이다(`benchmark_policy.concurrency` = `max_num_seqs`).
그 이상은 이 구성이 만족시키기로 한 조건이 아니다. 단기 조치는 상류에서
동시 요청을 10으로 제한하는 것이고, 항구적 조치는 용량 재산정이다.
**`max_num_seqs`를 조용히 올려서 SLO를 통과시키지 마라** — 그건 측정 조건을
바꾼 것이므로 재측정과 새 result 레코드가 필요하다.

**4-C GPU가 놀고 있음**
```bash
curl -s http://127.0.0.1:8000/health          # vLLM 자체
nvidia-smi --id=1 --query-gpu=utilization.gpu,memory.used --format=csv
```
vLLM이 죽었으면 [service-not-ready.md](service-not-ready.md)로 간다.

**4-D throttling**
```bash
nvidia-smi --id=1 --query-gpu=clocks_throttle_reasons.active,temperature.gpu,power.draw --format=csv
```
드라이버·CUDA는 건드리지 않는다. 냉각과 전력 한도는 설비 문제로 보고한다.

## 5. Rollback

변경 직후 시작된 악화라면 원인을 다 밝히기 전에 되돌린다.

```bash
python3 scripts/rollback_release.py list
python3 scripts/rollback_release.py rollback --to <previous-release-id> \
  --reason "P95 TTFT SLO 초과, 배포 직후 시작"
```

절차와 검증은 [rollback.md](rollback.md)에 있다.

## 6. Evidence to collect

incident report를 쓰려면 아래를 남긴다. **되돌리기 전에 수집한다** — 복구
후에는 증거가 사라진다.

- [ ] Prometheus 쿼리 결과 (장애 구간 포함 시간범위)
      `curl 'http://127.0.0.1:9090/api/v1/query_range?query=...&start=...&end=...&step=5s'`
- [ ] 발화한 alert: `curl -s http://127.0.0.1:9090/api/v1/alerts`
- [ ] `work/…/serve-*.log` 서버 로그 (KV cache, preemption, OOM 라인)
- [ ] `load_test.py` 출력 — 서버 TTFT와 사용자 TTFT 둘 다
- [ ] `ops/release/current-release.json` — 그때 무엇이 돌고 있었는가
- [ ] regression gate 리포트 (변경이 gate를 통과했는가, 통과했다면 왜 못 잡았는가)

마지막 항목이 중요하다. gate를 통과한 변경이 장애를 냈다면 **gate에 빠진
검사가 무엇인지**가 이 incident의 진짜 산출물이다.
