# INC-XXX — <한 줄 제목>

> 이 파일을 복사해 쓴다. **실제로 실행하지 않은 항목은 채우지 마라.**
> 실행하지 않았으면 `NOT_EXECUTED`, 측정하지 않았으면 `NOT_MEASURED`,
> 검증 대기면 `PENDING_VALIDATION`으로 남긴다. 빈칸보다 그 표시가 낫다.

| | |
|---|---|
| Incident ID | INC-XXX |
| 상태 | detected / mitigated / resolved / **intentional-experiment** |
| 유형 | 실제 장애 / **의도적 주입 실험** |
| 발생 (UTC) | |
| 탐지 (UTC) | |
| 복구 (UTC) | |
| 탐지 소요 | 발생 → alert firing |
| 복구 소요 | 탐지 → 정상 확인 |
| 영향 | |
| 관련 release | `ops/release/history/<id>.json` |
| 관련 runbook | `ops/runbooks/<...>.md` |

의도적으로 주입한 장애라면 **반드시 그렇게 표시한다.** 실험을 실제 장애처럼
쓰는 것은 이 프로젝트가 금지하는 종류의 거짓이다.

## 1. Baseline — 장애 이전 정상 상태

무엇이 정상이었는지 숫자로 적는다. 이 값이 없으면 "이상"을 정의할 수 없다.

| 지표 | 값 | 출처 |
|---|---:|---|
| `up{job="vllm"}` | | Prometheus |
| P95 TTFT | | |
| 오류율 | | |
| peak VRAM | | |
| quality score | | `ops/baselines/…` |

재현 명령:
```bash
```

## 2. Symptom — 무엇이 관측되었는가

사용자/호출자 관점의 증상과 대시보드 관점의 증상을 나눠 적는다.

## 3. Observation — metric / alert 증거

**어떤 alert가 언제 발화했는가.** 캡처가 아니라 쿼리 결과를 남긴다.

```bash
curl -s http://127.0.0.1:9090/api/v1/alerts
```

| alert | state | activeAt (UTC) | 탐지 지연 |
|---|---|---|---|

관련 시계열 (`query_range` 결과 경로):

- `ops/evidence/…`

## 4. Diagnosis — 근거

가설과 그 가설을 **어떻게 확인했는지**를 함께 적는다.
그럴듯한 설명은 근거가 아니다. ADR-0004의 오진 사례를 기억한다 —
"설명이 그럴듯하고 숫자와 맞는다는 것이 그 설명이 옳다는 뜻은 아니다."

| 가설 | 확인 방법 | 결과 | 판정 |
|---|---|---|---|

근본 원인:

## 5. Mitigation — 무엇을 했는가

```bash
```

## 6. Recovery — 복구 증거

프로세스 생존이 아니라 **metric과 release 일치**로 확인한다.

```bash
python3 scripts/rollback_release.py verify
curl -s --data-urlencode 'query=up{job="vllm"}' http://127.0.0.1:9090/api/v1/query
```

| 지표 | 복구 후 값 | baseline 대비 |
|---|---:|---|

## 7. 이 incident가 드러낸 것

관측·차단 체계 자체의 결함을 적는다. 서비스 결함보다 이쪽이 더 중요하다.

- 탐지가 늦었는가? `for:` 지속시간이 근거 있는 값인가?
- gate를 통과한 변경이 장애를 냈다면, gate에 빠진 검사는 무엇인가?
- runbook의 어느 단계가 실제로 도움이 됐고 어느 단계가 쓸모없었는가?
- alert가 원인을 가리켰는가, 증상만 가리켰는가?

## 8. Follow-up

- [ ] (담당 / 기한)

## 9. 검증하지 못한 것

이 절을 비워두지 마라. 확인하지 못한 것을 확인한 척하는 것이 가장 큰 실패다.

-
