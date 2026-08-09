# INC-003 — 의도적 API 중단과 immutable container 복구

| | |
|---|---|
| Incident ID | INC-003 |
| 상태 | resolved / **intentional-experiment** |
| 유형 | 의도적 주입 실험 |
| 발생 (UTC) | 2026-08-09 23:14:30.862947 UTC (SIGTERM) |
| 탐지 (UTC) | 2026-08-09 23:15:14.628239 UTC |
| 복구 확인 (UTC) | 2026-08-09 23:17:10.196636 UTC |
| 탐지 소요 | 43.765초 (SIGTERM → alert activeAt) |
| 복구 소요 | alert activeAt → `/ready` 정상 확인 115.568초; 실제 성공 rollback 명령 6.332초 |
| 영향 | loopback 검증 환경의 API만 중단. 외부 사용자 트래픽 없음 |
| 복구 release | `ops/release/history/2026-08-09-v02-container-good.json` |
| 관련 runbook | `ops/runbooks/rollback.md` |

이 기록은 실제 고객 장애가 아니라 완료 조건을 검증하기 위해 의도적으로 실행한
실험이다. GPU 1의 vLLM은 중단하지 않았고 CUDA/driver도 변경하지 않았다.

## 1. Baseline

| 지표 | 장애 전 값 | 출처 |
|---|---:|---|
| Prometheus targets | gateway/vLLM/DCGM/Prometheus 모두 `up` | `ops/evidence/final-rehearsal/prometheus-targets-ready.json` |
| API `/ready` | HTTP 200, `status=ready` | `ops/evidence/final-rehearsal/ready.json` |
| GPU 1 framebuffer | 22,362 MiB | `ops/evidence/final-rehearsal/nvidia-smi-start.csv` |
| quality score | 98.333 / 60문항 | `ops/evidence/final-rehearsal/gate-all.json` |
| incident 직전 P95 TTFT | NOT_MEASURED | 부하 창과 별도 query-range를 기록하지 않음 |

## 2. Fault injection과 사용자 관점 증상

20개의 실제 RAG 요청이 처리 중일 때 Compose가 API 컨테이너에 SIGTERM을 보냈다.
서비스는 새 요청 수락을 중지하고 `/ready`를 503으로 전환했으며, 기존 20개 요청은
모두 HTTP 200 본문을 끝까지 전달했다. 20/20 결과와 각 응답 byte 수는
`ops/evidence/final-rehearsal/graceful-clients.json`에 있다. drain 이후 API는
의도대로 중단되어 새 연결을 받을 수 없었다.

## 3. Alert 증거

| alert | state | activeAt (UTC) | 탐지 지연 |
|---|---|---|---:|
| `FinLLMGatewayDown` | firing | 2026-08-09 23:15:14.628239 | 43.765초 |

- firing payload: `ops/evidence/final-rehearsal/incident-alerts-firing.json`
- target 상태: `ops/evidence/final-rehearsal/incident-targets-down.json`
- 설정된 `for: 30s`는 프로젝트 실측 근거가 없어 rule annotation에도
  `PENDING_THRESHOLD_VALIDATION`으로 표시되어 있다.

## 4. Diagnosis

| 가설 | 확인 방법 | 결과 | 판정 |
|---|---|---|---|
| 전체 추론 stack 장애 | Prometheus target과 vLLM `/models` 확인 | vLLM target은 `up`; gateway만 `down` | 반증 |
| API가 drain 없이 요청을 절단 | 20개 client body와 readiness 전이 확인 | 20/20 완주, drain 동안 503 | 반증 |
| API 컨테이너만 중단 | Compose 상태와 gateway target 확인 | API stopped, gateway scrape down | 채택 |

근본 원인은 테스트가 실행한 `docker compose stop finllm-api`이다.

## 5. Mitigation과 fail-closed 동작

첫 rollback은 release manifest Git SHA 오기로 실패했다. restart script가 manifest와
`deploy/.env`의 불일치를 차단했고, audit log에 `state_mutated:false`로 남겼다. 오기를
실제 image provenance SHA `95dd24deba5669919e12b8535dbaf3128646ae5e`로 고친 후,
다음을 다시 실행했다.

```bash
python3 scripts/rollback_release.py rollback \
  --to 2026-08-09-v02-container-good \
  --reason 'INC-003 intentional API outage; restore immutable known-good container' \
  --incident INC-003 --exec --verify-timeout 180
```

성공 rollback은 API image
`sha256:16986083cba5b7c775ad40cf0cee17dd6680fe5b00b03d3be0a7c14e99270feb`
일치를 확인한 뒤 Compose를 재기동했다.

## 6. Recovery 증거

| 검증 | 복구 후 값 |
|---|---|
| `/ready` | HTTP 200, `status=ready` |
| 선언 release 대 실제 model/build info | `VERIFY: OK` |
| Prometheus active alert | 없음 |
| rollback state mutation | 성공 시에만 `true` |

- 실행 로그: `ops/evidence/final-rehearsal/rollback-execution-success.log`
- 독립 verify: `ops/evidence/final-rehearsal/rollback-verify.log`
- recovery alert payload: `ops/evidence/final-rehearsal/incident-alerts-after-recovery.json`
- current release: `ops/evidence/final-rehearsal/current-release-after-rollback.json`

## 7. 드러난 점과 follow-up

- alert는 원인이 아니라 gateway scrape 실패라는 증상을 정확히 가리켰다.
- provenance 불일치는 복구를 진행하지 않고 state를 보존했다.
- 탐지 43.765초 중 `for: 30s`와 scrape 정렬의 영향은 확인됐지만, 실제 운영
  허용치로 타당한지는 `PENDING_THRESHOLD_VALIDATION`이다.
- vLLM image digest는 manifest의 runtime 확장 필드에 기록했지만 현재 rollback
  verifier는 API digest와 build info를 중심으로 검사한다. vLLM container digest
  자동 대조는 후속 개선 사항이다.

## 8. 검증하지 못한 것

- 외부 load balancer의 connection draining: NOT_EXECUTED (본 범위는 단일 호스트 Compose)
- 실제 고객 트래픽 영향: NOT_EXECUTED (의도적 loopback 실험)
- native 24GB GPU에서의 동일 복구: NOT_EXECUTED; A6000 측정과 혼동하지 않는다.
