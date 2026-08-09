# INC-002 — 품질 gate를 통과한 변경이 24GB 적합성을 깨뜨렸다 (rollback 시연)

| | |
|---|---|
| Incident ID | INC-002 |
| 상태 | resolved |
| 유형 | **의도적 주입 실험** (실제 장애 아님) |
| 배포 (UTC) | 2026-08-09T05:14Z (후보 기동) |
| 탐지 (UTC) | 2026-08-09T05:15:00.93Z (alert 조건 성립) |
| rollback (UTC) | 2026-08-09T05:17:29Z |
| rollback 소요 | **33.1초** (목표 15분) |
| 영향 | Profile A의 24GB class 적합성 상실. 처리량 약 7배 저하 |
| 관련 release | `2026-08-09-graph-enabled` → `2026-08-09-baseline` |
| 관련 runbook | [gpu-oom.md](../runbooks/gpu-oom.md), [rollback.md](../runbooks/rollback.md) |
| 증거 | `ops/evidence/rollback-demo/` |

의도적으로 주입한 변경이다. 목적은 두 가지였다.

1. rollback 절차가 문서가 아니라 실제로 동작하는지
2. **품질 gate를 통과하는 나쁜 변경**이 있을 수 있는지

둘 다 답을 얻었고, 두 번째가 더 중요한 결과다.

## 1. 변경 내용

`2026-08-09-baseline`에서 `--enforce-eager` 하나를 뺐다. 즉 CUDA graph를 켰다.
모델·revision·프롬프트·retriever·평가셋은 전부 동일하다.

```diff
- vllm serve … --quantization awq --enforce-eager
+ vllm serve … --quantization awq
```

[ADR-0004](../../decisions/0004-profile-a-model-revised.md)가 이미 이 구성의
위험을 기록해 두었다. CUDA graph는 executor 예산 **바깥에서** 2.2–3.5 GiB를
추가로 쓴다.

## 2. regression gate는 이 변경을 막지 못했다

```
[OK  ] smoke-evaluation      60문항 평가 완료 (100.76s)
[OK  ] acl-runtime           권한 위반 0건
[OK  ] quality-regression    품질 97.667 ≥ 기준 90, baseline 하한 97.667
[OK  ] injection-regression  injection 성공 2건 ≤ baseline 2건
OVERALL: PASS
```

**품질은 소수점까지 동일했다(97.667).** 당연하다 — gate는 평가셋 결과만 보고,
CUDA graph는 답의 내용을 바꾸지 않는다.

그래서 `promote`는 정상적으로 승격했다. 규칙 위반이 아니다.

다만 gate 실행 시간에 신호가 있었다: **13.86초 → 100.76초, 7.3배.**
gate는 이 값을 판정에 쓰지 않는다.

## 3. Observation — alert가 잡았다

| 지표 | baseline | graph-enabled | threshold |
|---|---:|---:|---:|
| GPU1 `FB_USED` | 22,362 MiB (21.84 GiB) | **26,570 MiB (25.95 GiB)** | 24,576 MiB |

```
+  5s  FB_USED=26570 MiB  FinLLMGPUMemoryAboveProfileClass=firing
```

| alert | state | activeAt |
|---|---|---|
| `FinLLMGPUMemoryAboveProfileClass` | **firing** | 2026-08-09T05:15:00.934Z |

v0.1이 같은 구성(deployment-matched 0.46, graph on)에서 기록한 peak VRAM은
**25.67 GiB**였다. 이번 측정은 25.95 GiB로, 0.28 GiB 차이는 있으나 같은 현상을
독립적으로 재현했다. 처리량 저하도 v0.1의 56.4 tok/s(graph) 대비 방향이 같다.

## 4. Diagnosis

runbook [gpu-oom.md](../runbooks/gpu-oom.md) 2절 분기표의 첫 줄에 그대로 해당했다.

```bash
$ grep -c "Graph capturing" work/v02/serve-2026-08-09-graph-enabled.log
```

근본 원인: `--enforce-eager` 제거로 CUDA graph capture가 활성화되어 executor
예산 바깥의 메모리를 추가 점유했다. **`gpu_memory_utilization`을 낮추는
것으로는 해결되지 않는다** — 이 값은 executor 예산만 정하고 graph는 그 밖에
있기 때문이다. ADR-0004가 실측으로 보인 그대로다.

## 5. Rollback

```bash
python3 scripts/rollback_release.py rollback --to 2026-08-09-baseline \
  --reason "peak VRAM이 Profile A의 24GB class를 초과 — ADR-0004의 CUDA graph 문제 재현" \
  --incident INC-002 --exec
```

```
[restart] GPU 1 회수 완료 (0s, 4 MiB)
[restart] ready after 30s
rollback: 2026-08-09-graph-enabled -> 2026-08-09-baseline
current-release.json -> 2026-08-09-baseline
rollback 소요: 33.1s
```

INC-001에서 고친 GPU 회수 로직이 이번엔 0초에 회수했다(graceful 종료 경로라
고아 프로세스가 없었다).

## 6. Recovery — 증거

```
$ python3 scripts/rollback_release.py verify
declared release: 2026-08-09-baseline
served models   : ['Qwen/Qwen3-14B-AWQ']
VERIFY: OK — 선언된 release와 실제 서비스가 일치한다
```

| 지표 | rollback 후 | baseline | 판정 |
|---|---:|---:|---|
| GPU1 `FB_USED` | 22,362 MiB | 22,362 MiB | 일치 |
| `up{job="vllm"}` | 1 | 1 | 일치 |
| 발화 중 alert | **0건** | 0건 | 일치 |
| active release | `2026-08-09-baseline` | — | 일치 |

append-only 감사 기록 (`ops/release/rollback-log.jsonl`):

```json
{"at_utc":"2026-08-09T05:17:29.068065+00:00","action":"rollback",
 "release_id":"2026-08-09-baseline","from_release_id":"2026-08-09-graph-enabled",
 "reason":"peak VRAM이 Profile A의 24GB class를 초과 — ADR-0004의 CUDA graph 문제 재현",
 "executed":true,"exec_returncode":0,"elapsed_seconds":33.112,"incident":"INC-002"}
```

`rollback_release.py list`에서 `2026-08-09-graph-enabled`는 `rolled-back`
상태로 남는다. 삭제하지 않는다 — 무엇을 왜 되돌렸는지가 기록의 핵심이다.

## 7. 이 incident가 드러낸 것

**1. "gate를 통과했으니 안전하다"고 말할 수 없다.**
regression gate는 품질·권한·injection을 본다. peak VRAM은 부하시험에서
나오는 값이라 gate 경로에 없다. 이 변경을 잡은 것은 gate가 아니라 alert였다.
배포 전 방어(gate)와 배포 후 방어(alert)가 서로 다른 것을 본다는 사실을
명시적으로 기록해 둔다. → [ADR-0007](../../decisions/0007-change-safety.md)

**2. `promote`는 현재 발화 중인 alert를 보지 않는다.**
alert는 05:15:00에 이미 firing이었고 promote는 05:16:55에 성공했다.
gate 리포트만 확인하고 live 상태는 확인하지 않기 때문이다. 후보를 실제로
띄워 gate를 돌리는 워크플로에서는 이 구멍이 실질적이다.

**3. gate 실행 시간이 성능 회귀의 부수 신호였다.**
13.86초 → 100.76초. 판정에는 쓰지 않지만 사람이 보면 이상하다는 것을 안다.
자동 판정 항목으로 승격할 가치가 있는지는 별도 검토가 필요하다.

**4. rollback 목표시간을 크게 밑돌았다.**
목표 15분, 실측 33.1초. 다만 이는 **단일 노드 재기동** 방식이고 그동안
서비스는 중단된다. canary/blue-green이 아니다.

## 8. Follow-up

- [ ] `promote`에 "현재 critical alert가 firing이면 거부" 조건 추가 검토
- [ ] gate에 부하시험 기반 VRAM 단계 추가 검토 (GPU 단계 한정, `gpu_watch.py` 재사용)
- [ ] gate 실행 시간을 성능 회귀 신호로 기록할지 검토
- [ ] A파트 배포 후 재실행 — 컨테이너 이미지 digest 단위 rollback으로 다시 확인

## 9. 검증하지 못한 것

- **서비스 중단 시간.** rollback 중 요청 트래픽을 흘리지 않았으므로 사용자가
  겪는 중단 길이는 `NOT_MEASURED`다. 재기동 30초가 그대로 중단 시간일
  가능성이 높지만 측정하지 않았다.
- **graph-enabled 구성의 정식 peak VRAM.** `gpu_watch.py`로 샘플링한 정식
  측정이 아니라 DCGM 관측값이다. 정식 result 레코드를 만들지 않았다.
- **A파트 컨테이너 단위 rollback.** 현재 rollback은 호스트 vLLM 프로세스를
  재기동한다. image digest 단위 rollback은 미검증 → `PENDING_VALIDATION`.
- **7.3배 지연이 CUDA graph 때문이라는 인과.** 방향은 ADR-0004와 일치하지만
  이번 실험은 변수를 하나만 바꾼 통제 실험이 아니라 gate 실행 시간 비교였다.
