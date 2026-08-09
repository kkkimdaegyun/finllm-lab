# A파트 교차검토 결과 — Claude → Codex (2026-08-09)

| | |
|---|---|
| 검토자 | Claude (B파트 — Observability / Reliability / Change Safety) |
| 대상 | Codex A파트 — `service/`, `deploy/`, `scripts/deploy/`, `tests/test_service_*`, `tests/test_deploy_*` |
| 대상 트리 | `/home/dgkim/dgkim/FinLLM:0.2` |
| 최초 검토 | 2026-08-09 07:45 UTC |
| 재검증 | 2026-08-09 08:10 UTC |
| 판정 | **accept-with-changes** |

BLOCKER 1 / MAJOR 7 / MINOR 1. 재검증 시점까지 A파트 소스가 변경되지 않아
(최신 mtime `service/` 05:24, `deploy/` 05:30) 전 항목 `NOT_FIXED`다. 이는 품질
판단이 아니라 대응 기회가 없었다는 뜻이다.

**나는 Codex 트리의 파일을 하나도 수정하지 않았다.** CUDA/드라이버도 건드리지
않았다(driver 535.288.01 유지). 이 디렉터리는 전달용 산출물이다.

---

## 먼저 읽을 것

Codex가 자기 계약(`docs/cross-review/v0.2-interface-contract.md`, status
`PROPOSED_FOR_CLAUDE_REVIEW`)을 먼저 선언해 두었다. **아래 판정은 내 계약이
아니라 Codex 자신의 계약과 프로젝트 요구사항을 기준으로 했다.** 계약이 두 벌
존재한다는 사실 자체는 F7로 따로 제출했고, 그것은 양측 공동 책임이다.

## 먼저 인정할 것

지적만 나열하면 오해가 생기므로 먼저 적는다.

- v0.1 기존 84개 테스트 회귀 없음 (`111 tests run, OK, skipped=3`)
- startup fail-fast가 제대로 되어 있다 — config/corpus/index/prompt revision/
  model revision/inference 도달성까지 확인 후 exit 2
- non-root(uid 10001), `read_only` 컨테이너, tmpfs `/tmp`
- base image digest 고정, Python 소스 sha256 검증,
  빌드 시 vllm/torch/transformers 버전 assert (`Dockerfile.vllm:54`)
- `deploy/evidence/2026-08-09-a-part-validation.json`이 `NOT_EXECUTED`/
  `NOT_MEASURED`를 정확히 표시한다. **허위 주장은 발견하지 못했다.**
- in-flight drain 메커니즘 자체는 동작한다 (F9는 마지막 응답 쓰기 구간의 경합)

---

## 권장 처리 순서

| # | ID | 등급 | 한 줄 요약 | 재현 |
|---|---|---|---|---|
| 1 | **F1** | BLOCKER | 추론 엔진이 죽어도 `/ready`가 영구히 200 | `repro/f1_engine_dead.py` |
| 2 | **F9** | MAJOR | graceful shutdown이 in-flight 응답을 자르는데 서버는 200으로 기록 | `repro/f9_inflight_truncation.py` |
| 3 | **F2** | MAJOR | drain 중 `/ready=503`을 관측할 수 없다 (창 0.5초 미만 → 이후 hang) | `repro/f2_drain_window.py` |
| 4 | **F6** | MAJOR | 오프라인 모델 반입 경로 없음 → 런타임 HuggingFace 접속 | `repro/f5_f6_f7_f8_static.sh` |
| 5 | **F4** | MAJOR | listen backlog 5, 연결 무성 드롭이 metric에 안 잡힘 | `repro/f4_backlog_and_drops.py` |
| 6 | **F3** | MAJOR | TTFT metric 부재 + 비스트리밍 + `le=2` bucket 부재 | `repro/f3_metric_contract.py` |
| 7 | **F5** | MAJOR | GPU 기본값 0이 런북과 충돌, VRAM alert 오탐 | `repro/f5_f6_f7_f8_static.sh` |
| 8 | **F7** | MAJOR | 계약 이원화 (공동 책임) | `repro/f5_f6_f7_f8_static.sh` |
| 9 | **F8** | MINOR | 기동 시 retriever hash를 baseline과 대조하지 않음 | `repro/f5_f6_f7_f8_static.sh` |

**F2와 F9는 같은 종료 경로(`service/app.py` → `http_server.py`)에 있으므로 함께
고치는 것이 자연스럽다.**

상세 내용·근거·수정 방향은 [`findings.json`](findings.json)에 있다.

---

## 재현 방법

```bash
cd ops/findings/2026-08-09-a-part-review/repro

# BLOCKER
python3 f1_engine_dead.py

# 신규 MAJOR — 설계 동시성 10에서 in-flight 절단
python3 f9_inflight_truncation.py

# drain 관측 창 정밀 측정
python3 f2_drain_window.py

# backlog 통제 실험 + 동시성별 드롭률
python3 f4_backlog_and_drops.py

# metric 계약 (서비스 기동 불필요)
python3 f3_metric_contract.py

# F5/F6/F7/F8 — F6만 컨테이너 실행
bash f5_f6_f7_f8_static.sh
```

경로는 환경변수로 바꿀 수 있다.

```bash
export FINLLM_A_ROOT=/home/dgkim/dgkim/FinLLM:0.2
export FINLLM_PYTHON=/home/dgkim/dgkim/new_project/.venv/bin/python
export FINLLM_REPRO_WORK=/tmp/finllm-repro
```

GPU는 필요 없다. `_stub_vllm.py`가 결정적 stub 추론 서버 역할을 하며 세 가지
모드(`ok` / `engine_dead` / `slow`)로 장애를 주입한다. 실제 vLLM과 모델은
쓰지 않는다.

---

## 재검증에서 내가 정정한 것

이 프로젝트는 "LLM의 의견은 증거가 아니다"를 원칙으로 하므로, 내 원래 판정 중
틀린 부분을 먼저 밝힌다.

**F2의 근거가 부정확했다.** 최초 리뷰에서 나는 "SIGTERM 직후 `+0s`부터
`/health`·`/ready` 모두 connection refused"라고 적었다. 2초 간격 폴링이 만든
착각이었다. 0.5초 timeout 연속 폴링으로 다시 재니 `t=0.002s`에 **503이 실제로
나왔다.** 정확한 전이는 이렇다.

```
t=0.001~0.002s  /ready = 503              창이 0.5초 미만
t=0.502s        TimeoutError              거부가 아니라 hang
t=10.02s        ConnectionRefused         프로세스 종료
```

결론(운영상 drain을 관측할 수 없다)은 유지되고, hang이 refused보다
healthcheck에 불리하다는 점에서 근거는 오히려 강해졌다. 하지만 **폴링 간격이
만든 관측 한계를 결과로 착각한 것**이고, 이건 내가 INC-001에서 DCGM
collect-interval 때문에 이미 한 번 겪은 실수와 같은 종류다.

**F4의 인과를 확인하지 않고 제출했었다.** "backlog=5가 원인"이라고 썼지만
변수를 바꿔보지 않았다. 이번에 `request_queue_size`만 512로 올린 서브클래스와
비교해 드롭이 사라지는 것을 확인했다. 동시에 드롭률이 백엔드 속도에 크게
의존한다는 것도 드러났다(in-process fake 1.7% vs HTTP stub 53.3%). 최초 보고한
37.5%는 조건을 명시했어야 했다.

**신규 F9는 재검증이 아니었으면 못 찾았다.** F2를 정밀하게 다시 재려고 폴링을
촘촘히 한 결과 in-flight 요청이 `IncompleteRead`로 잘리는 것이 보였다.

---

## 내가 확인하지 못한 것

- **실제 vLLM 컨테이너를 GPU에 올린 통합 검증.** F6 때문에 네트워크 없이는
  기동이 불가능하고, 네트워크를 열면 약 9GB를 내려받는다. BLOCKER 재현은
  stub으로 했다.
- **"vLLM API server는 살아 있는데 engine만 죽는" 상태가 vLLM 0.9.2에서 실제로
  발생하는지.** F1은 "`/ready`가 생성 경로를 관측하지 않는다"는 성질을 증명한
  것이며, 그 성질은 백엔드가 어떤 이유로든 생성만 실패할 때 항상 성립한다.
- **`docker compose up`.** 컨테이너 restart 동작, `depends_on: restart: true`
  전이, `stop_grace_period` 하의 drain은 `PENDING_VALIDATION`이다.
- **이미지 빌드 재현성.** 정적 확인만 했고 재빌드 digest 동일성은 미확인이다.
  apt 패키지 버전 미고정은 결함으로 제출하지 않았다.
- **성능·VRAM·품질.** 일절 측정하지 않았다. `NOT_MEASURED`.
- **F5의 오탐 시나리오.** 산술과 설정값에 근거한 판단이며 GPU 0에 실제
  배포해 확인하지 않았다.
- **F9의 경합 창 폭.** 관측된 조건에서의 실패만 보고하며 일반 실패 확률
  모델은 세우지 않았다.

## 이해상충

나는 B파트 구현자다. **F7(계약 이원화)와 F5(내 alert 임계값이 근거에 등장)에는
이해상충이 있다.** 두 항목은 "누가 맞는가"를 판정하지 않고 통합 시 실제로
깨지는 지점만 기술했다.

---

## 통합에 관한 별도 지적

findings 밖의 구조적 문제다. A파트는 `/home/dgkim/dgkim/FinLLM:0.2`,
B파트는 `/home/dgkim/dgkim/FinLLM-0.2`에 있고 **서로의 파일이 하나도 없다.**
Codex는 v0.1 스냅샷을 클론해 작업했으므로 내 계약을 본 적이 없고, 나도 Codex
것을 본 적이 없다. 계약이 두 벌 생긴 것(F7)은 그 결과다.

두 트리를 합치기 전까지 "regression gate가 배포를 차단한다"도 "Prometheus가
gateway를 scrape한다"도 실제로는 성립하지 않는다. **통합이 다음 작업의 첫
항목이 되어야 한다.**

참고로 콜론이 들어간 경로(`FinLLM:0.2`) 자체는 이번엔 문제가 되지 않았다.
Codex가 bind mount 대신 named volume을 써서 `docker compose config`가 exit 0으로
통과한다. 다만 bind mount를 추가하는 순간 깨진다 — 콜론 경로에서
`docker compose up`은 `invalid volume specification`으로 실패한다(별도 확인).
