# ADR-0006: 관측 지표와 alert 선택

- 상태: Accepted
- 날짜: 2026-08-09
- 작성자: kkkimdaegyun (B파트 구현: Claude)
- 관련: [ADR-0004](0004-profile-a-model-revised.md), [ADR-0007](0007-change-safety.md)

## 맥락

v0.2의 질문은 "운영 중 품질·성능 이상을 관측할 수 있는가"다.
`docs/portfolio-roadmap.md`의 Milestone 3이 이 ADR의 범위다.

새 도구를 늘리는 것 자체는 성과가 아니다. 추가한 것은 세 개다.

| 도구 | 목적 | 이것 없이 안 되는가 |
|---|---|---|
| Prometheus | 시계열 저장·규칙 평가 | vLLM `/metrics`는 현재값만 준다. "악화되는가"는 시계열이 있어야 답한다 |
| Grafana | 8개 운영 질문에 대한 고정된 조회 | 없어도 되지만, 장애 중에 PromQL을 손으로 짜게 된다 |
| DCGM exporter | GPU util·VRAM·XID | vLLM은 자기 executor 예산만 안다. **peak VRAM이 24GB class를 넘는지는 GPU 쪽에서만 보인다** |

DCGM이 핵심이다. 이 프로젝트의 결론 자체가 "이 구성이 24GB 카드에 들어가는가"이므로,
그 성질을 운영 중에 계속 확인할 수단이 없으면 결론을 유지할 수 없다.

## 결정

### 1. metric은 세 출처에서 모은다

- **gateway (A파트)** — request/retrieval/generation. 이름은
  [interface-contract-v0.2.md](../docs/cross-review/interface-contract-v0.2.md) 1.2절에 고정.
- **vLLM 0.9.2 자체** — queue, KV cache, preemption, TTFT/E2E histogram.
  이름은 추정하지 않고 실행 중인 서버의 `/metrics`에서 확인했다
  (`ops/evidence/vllm-0.9.2-metric-names.txt`, 67개).
- **DCGM exporter** — GPU util/VRAM/전력/XID.

### 2. threshold는 `configs/profiles.json`에서만 온다

`benchmark_policy`의 값을 alert rule에 복사하지 않고, 같은 값인지
`regression_gate.py`의 `alert-threshold-consistency` 단계가 강제한다.
SLO 숫자가 두 벌이면 반드시 갈라진다.

근거 있는 threshold:

| alert | 값 | 출처 |
|---|---|---|
| `FinLLMHighRequestErrorRate` | 1% | `benchmark_policy.error_rate_max` |
| `FinLLMHighP95TTFT` | 2,000ms | `benchmark_policy.p95_ttft_ms_max` |
| `FinLLMGPUMemoryAboveProfileClass` | 24,576 MiB | `profile-a.vram_class_gib` |
| `FinLLMRequestQueueBuildup` | waiting > 0 | `max_num_seqs` = `benchmark_policy.concurrency` = 10 |
| `FinLLMPreemptionObserved` | > 0 | v0.1 baseline은 0건 |

근거 **없는** 값은 전부 `PENDING_THRESHOLD_VALIDATION`으로 표시했다.
모든 `for:` 지속시간, 모든 rate window, KV cache 0.9가 여기 해당한다.
v0.1의 부하시험은 2초 버스트라 "몇 분간 지속되는 이상"을 관측한 적이 없다.

### 3. 존재하지 않는 서비스에 대해 alert를 울리지 않는다

A파트가 아직 배포되지 않았으므로 `finllm-gateway`의 scrape target 목록은
`file_sd_configs`로 분리했고 현재 빈 배열이다. 없는 서비스에 대한
`ServiceNotReady`는 거짓 신호이고, 거짓 신호는 alert 전체의 신뢰를 깎는다.

## 이 결정을 만든 실측

### 3-1. vLLM histogram으로는 2초 SLO를 판정할 수 없다

실행 중인 vLLM 0.9.2의 `vllm:time_to_first_token_seconds` bucket 경계:

```
… 0.75, 1.0, 2.5, 5.0, 7.5 …
```

**`2.0`이 없다.** `histogram_quantile`은 bucket 안에서 선형보간하므로 이
histogram의 P95는 `[1.0, 2.5]` 구간에서 임의의 값을 준다. 이 프로젝트의 hard
gate가 정확히 2,000ms이므로 **gate와 alert가 다른 값을 보게 된다.**

그래서 gateway histogram에 `le=2` 경계를 계약으로 요구했고, vLLM 쪽 alert는
`severity: warning` + "gate 판정용 아님" 주석을 달아 조기 신호로만 쓴다.
`tests/test_monitoring_config.py`가 이 사실을 회귀 테스트로 고정한다.

### 3-2. DCGM 기본 설정은 이 프로젝트의 부하를 놓친다

DCGM exporter의 기본 `collect-interval`은 30초다. 이 프로젝트의 부하시험은
2초 버스트다(2026-08-08c wall 2.03–2.06s).

실제로 관측한 것: **endpoint가 `DCGM_FI_DEV_GPU_UTIL=91`을 보고하는 동안
Prometheus에는 `0`이 저장되어 있었다.** 대시보드는 "GPU가 놀고 있다"고
보여주고 있었고, 그것은 거짓이었다.

`-c 2000`으로 낮춰 scrape 간격(5s)보다 짧게 만든 뒤, 부하 중 Prometheus가
64 → 89 → 90 → 92로 추적하는 것을 확인했다.

**대시보드에 값이 그려진다는 것이 그 값이 옳다는 뜻은 아니다.**
ADR-0004에서 배운 것과 같은 종류의 함정이다.

### 3-3. DCGM 기본 field set에 `FB_TOTAL`이 없다

완료 조건 10이 "GPU memory total"을 요구하는데 기본 counters에는
`FB_USED`/`FB_FREE`만 있다. `monitoring/dcgm/finllm-counters.csv`로 필요한
9개 field만 명시적으로 골랐다. 전체 기본값을 켜는 대신 각 줄이 대시보드
질문 하나 또는 alert 하나에 대응한다.

### 3-4. `gpu_cache_usage_perc`는 0–1 fraction이다

부하 중 실측값 `0.00656`. 100분율로 착각하고 threshold를 90으로 두면 절대
울리지 않는 alert가 된다. 실행해서 확인했다.

## 결과

- alert 11개. 그중 근거 있는 threshold 5개, `PENDING_THRESHOLD_VALIDATION` 표시 다수.
- dashboard 13패널. 요구된 8개 운영 질문에 각각 대응하며,
  `tests/test_monitoring_config.py`가 질문 누락과 잘못된 metric 이름을 막는다.
- scrape target 3종 UP 확인, 실제 부하에서 쿼리가 실제 값을 반환하는 것 확인.

## 포기한 것과 위험

- **alertmanager를 넣지 않았다.** 알림 경로(메일·메신저)는 조직 인프라에
  종속적이고, 이 프로젝트에서 검증할 방법이 없다. Prometheus의 alert 상태
  전이를 API로 관측하는 것까지만 한다.
- **`for:` 지속시간이 전부 미검증이다.** 이 값이 탐지 시간을 직접 결정한다.
  INC-001에서 실제 탐지 시간을 기록했고, incident가 쌓이면 그것이 근거가 된다.
- **retrieval/generation metric은 아직 관측하지 못했다.** A파트 미배포다.
  계약과 dashboard 패널, alert는 준비돼 있으나 실제 값으로 검증한 적은 없다 →
  `PENDING_VALIDATION`.
- **단일 노드 관측이다.** HA·다중 노드는 범위 밖이며
  `docs/on-prem-architecture.md`의 구분을 그대로 따른다.
