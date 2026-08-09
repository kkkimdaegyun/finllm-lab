# INC-001 — vLLM upstream 강제 종료 실험, 그리고 복구가 드러낸 VRAM 누수

| | |
|---|---|
| Incident ID | INC-001 |
| 상태 | resolved |
| 유형 | **의도적 주입 실험** (실제 장애 아님) |
| 발생 (UTC) | 2026-08-09T05:04:36Z |
| 탐지 (UTC) | 2026-08-09T05:05:11Z |
| 복구 (UTC) | 2026-08-09T05:08:38Z (2차 시도) |
| 탐지 소요 | **35초** |
| 복구 소요 | 1차 181초(부분 실패) → 2차 41초(완전) |
| 영향 | 추론 불가. 사용자 트래픽 없음(실험 환경) |
| 관련 release | `ops/release/history/2026-08-09-baseline.json` |
| 관련 runbook | [service-not-ready.md](../runbooks/service-not-ready.md), [gpu-oom.md](../runbooks/gpu-oom.md) |
| 증거 | `ops/evidence/INC-001/` |

이것은 **의도적으로 주입한 장애**다. 실제 사용자 영향이 있었던 사건이 아니다.

목적은 "vLLM이 죽으면 관측 체계가 그것을 알아채는가"였다. 그 질문에는
답했고, **의도하지 않았던 실제 결함을 하나 더 찾았다.**

## 1. Baseline — 주입 이전

| 지표 | 값 | 출처 |
|---|---:|---|
| `up{job="vllm"}` | 1 | Prometheus |
| 발화 중 alert | **0건** | `01-alerts-baseline.json` |
| scrape target | dcgm / prometheus / vllm 전부 `up` | Prometheus |
| GPU1 `FB_USED` | 22,362 MiB | DCGM |
| active release | `2026-08-09-baseline` | `00-release-at-incident.json` |

주입 전 alert가 0건이라는 사실이 중요하다. 상시 울리는 alert가 있었다면
이 실험은 아무것도 증명하지 못한다.

## 2. Symptom — 주입

```bash
pkill -9 -f "vllm serve"      # 2026-08-09T05:04:36Z
```

SIGKILL을 골랐다. graceful shutdown이 아니라 **프로세스가 갑자기 사라지는**
상황(OOM killer, 하드웨어 오류, 컨테이너 강제 종료)을 모사하기 위해서다.

## 3. Observation — alert가 실제로 발화했다

| alert | state | activeAt (UTC) | 주입 대비 |
|---|---|---|---:|
| `FinLLMVLLMUpstreamDown` | firing | 2026-08-09T05:04:39.628Z | **+3.6초에 조건 성립** |
| | | firing 확인 05:05:11Z | **+35초** |

분해하면 이렇다.

```
+0.0s   SIGKILL
+3.6s   Prometheus scrape 실패 → up=0 → alert 조건 성립 (pending)
        (scrape_interval 5s 안에 들어온다)
+33.6s  for: 30s 경과 → firing
+35.0s  폴링 주기 5s 때문에 관측 시점이 35s
```

**탐지 시간의 대부분(30/35초)은 `for: 30s`가 만든 것이다.** 이 값은
`PENDING_THRESHOLD_VALIDATION`으로 표시해 둔 미검증 값이었고, 이번 실험이
그 값이 탐지 시간에 정확히 어떻게 기여하는지를 처음으로 보여줬다.

target 상태도 정확히 분리됐다 (`03-targets-during.json`).

```
dcgm         up
prometheus   up
vllm         down   Get "http://host.docker.internal:8000/metrics": dial tcp ...
```

관측 스택은 살아 있고 서비스만 죽었다 — 관측 스택과 서비스 스택을 분리한
설계가 의도대로 동작했다. 관측 스택이 함께 죽었다면 장애를 볼 수 없었다.

## 4. Diagnosis

runbook [service-not-ready.md](../runbooks/service-not-ready.md) 2절의 분기표대로
확인했다.

| 가설 | 확인 방법 | 결과 | 판정 |
|---|---|---|---|
| OOM으로 죽었다 | 서버 로그 `out of memory` grep | **0건** | 기각 |
| 기동 중이었다 | 로그 마지막 줄 | 정상 `200 OK` 응답 중이었다 | 기각 |
| 외부 신호로 죽었다 | 주입 명령 기록 | SIGKILL 주입 | **확인** |

근본 원인: 의도적 SIGKILL. **여기까지는 계획대로였다.**

## 5. 계획에 없던 발견 — GPU 메모리가 회수되지 않았다

복구 단계에서 `ops/release/restart.sh`가 GPU를 회수하지 못했다.

```
[restart] GPU 1 회수 완료 (120s, 23010 MiB)     ← 회수 실패인데 "완료"라고 찍었다
```

진단:

```
$ nvidia-smi --id=1 --query-compute-apps=pid,used_memory --format=csv
pid, used_gpu_memory [MiB]
2595824, 23004 MiB

$ ps -o pid,ppid,cmd -p 2595824
    PID    PPID CMD
2595824       1 …/python -c from multiprocessing.spawn import spawn_main; \
                  spawn_main(tracker_fd=33, pipe_handle=35) --multiprocessing-fork
```

**vLLM의 engine worker는 명령줄에 `vllm serve` 문자열이 없다.** 부모를
SIGKILL하면 이 worker는 `PPID=1`로 고아가 되어 살아남고, **VRAM 23,004 MiB를
그대로 쥔다.** `pkill -f "vllm serve"`는 이 프로세스를 절대 잡지 못한다.

결과적으로 새 인스턴스가 고아 위에 겹쳐 떴고, 그래서 이 alert가 울렸다.

| alert | state | activeAt |
|---|---|---|
| `FinLLMGPUMemoryAboveProfileClass` | pending | 2026-08-09T05:07:35.934Z |

23,004(고아) + 신규 인스턴스 > 24,576 MiB(Profile A의 24GB class). **이 alert는
내가 설계한 목적 그대로 동작했다** — "이 구성이 24GB 카드에 더 이상 들어가지
않는다"를 잡아낸 것이다. 다만 원인은 모델 구성이 아니라 정리 실패였다.

1차 복구는 `up=1`을 만들었지만(181초) **서비스는 열화된 상태였다.**
`up=1`만 봤다면 복구했다고 잘못 판단했을 것이다.

### 이 결함은 v0.1에도 있다

`scripts/run_profile_a.sh`의 `stop_server()`가 같은 패턴을 쓴다.

```bash
pkill -f "vllm serve" 2>/dev/null || true
...
echo "WARNING: GPU ${GPU_INDEX} still busy after stopping the server" >&2
```

경고만 출력하고 **다음 구성 측정을 그대로 진행한다.** 그 경우 다음 후보의
peak VRAM은 앞 모델의 잔여 메모리를 포함한 오염된 값이 된다.
2026-08-08c 측정에서 이 일이 실제로 일어났다는 증거는 없다 — peak VRAM이
구성별로 21.96 / 23.84 / 24.01 GiB로 뚜렷이 갈리므로 오염됐다면 이렇게
깨끗하게 나오지 않는다. 하지만 **막아주는 장치가 없었다는 것은 사실이다.**
→ Follow-up 참조.

## 6. Mitigation

`restart.sh`를 고쳤다. 프로세스 이름이 아니라 **GPU 점유 사실**로 찾는다.

- `nvidia-smi --id=<GPU> --query-compute-apps=pid`로 대상 GPU만 조회
- 현재 사용자 소유 프로세스만 종료 (다른 GPU·다른 사용자는 건드리지 않는다)
- 무엇을 죽였는지 항상 출력
- **회수 실패 시 기동하지 않고 non-zero로 종료한다.** 오염된 상태로 측정을
  시작하는 것보다 멈추는 것이 낫다

## 7. Recovery — 증거

2차 복구:

```
05:07:57Z  restart 시작
           [restart] GPU 1 잔존 프로세스 정리: pid=2595824 (…multiprocessing…)
05:08:09Z  [restart] GPU 1 회수 완료 (12s, 4 MiB)
05:08:38Z  [restart] ready after 25s
           총 41초
```

복구 확인은 프로세스 생존이 아니라 metric과 release 일치로 했다.

```
$ python3 scripts/rollback_release.py verify
declared release: 2026-08-09-baseline
served models   : ['Qwen/Qwen3-14B-AWQ']
VERIFY: OK — 선언된 release와 실제 서비스가 일치한다
```

| 지표 | 복구 후 (05:09:49Z) | baseline | 판정 |
|---|---:|---:|---|
| `up{job="vllm"}` | 1 | 1 | 일치 |
| GPU1 `FB_USED` | 22,362 MiB | 22,362 MiB | 일치 |
| GPU1 compute app | 1개 | 1개 | 고아 없음 |
| 발화 중 alert | 0건 | 0건 | 일치 |
| scrape target | 3/3 up | 3/3 up | 일치 |

증거: `07-alerts-after-clean-recovery.json`, `08-targets-after-recovery.json`,
`09-gpu1-compute-apps-after.csv`

## 8. 이 incident가 드러낸 것

**1. `up=1`은 복구의 증거가 아니다.**
1차 복구는 `up=1`을 만들었지만 VRAM이 두 배로 잡혀 있었다. 복구 판정에
`rollback_release.py verify`와 VRAM 확인을 함께 넣은 이유다.

**2. alert가 원인이 아니라 증상을 가리켰고, 그것으로 충분했다.**
`FinLLMGPUMemoryAboveProfileClass`는 "고아 프로세스가 있다"고 말해주지
않았다. "24GB class를 넘었다"고만 말했다. 그 신호에서 `nvidia-smi
--query-compute-apps`까지 가는 경로는 runbook이 담당했다. **alert는 짧고
runbook은 길어야 한다.**

**3. 탐지 시간의 근거를 처음으로 얻었다.**
35초 중 30초가 `for: 30s`다. scrape 자체는 3.6초에 이상을 잡았다.
`for`를 줄이면 탐지가 빨라지지만 순간적 scrape 실패에 취약해진다.
이 실험 1회로는 정할 수 없으므로 `PENDING_THRESHOLD_VALIDATION`을 유지한다.

**4. 관측 스택 분리가 값을 했다.**
서비스가 죽는 동안 Prometheus/Grafana/DCGM은 계속 살아 있었다.
`monitoring/compose.monitoring.yaml`을 A파트 deploy compose와 분리한
설계 판단이 이번에 검증됐다.

**5. 실험 스크립트 자체가 `pkill -f`에 당했다.**
1차 실행 때 `pkill -f "vllm serve"`가 **자기 자신을 실행 중인 셸**(argv에
스크립트 본문이 들어 있던)을 죽여 복구 단계가 중단됐다. `pkill -f`는 자기
자신도 후보에 넣는다. 이후 `[v]llm serve` 패턴으로 바꿨다.

## 9. Follow-up

- [ ] **`scripts/run_profile_a.sh`의 `stop_server()`에 같은 결함이 있다.**
      v0.1 파일이므로 B파트가 임의로 고치지 않는다.
      → cross-review finding으로 제출 (`ops/findings/`)
- [ ] `for: 30s`의 근거 확보 — incident 3회 이상 누적 후 결정
      (현재 `PENDING_THRESHOLD_VALIDATION`)
- [ ] A파트 배포 후 재실행: gateway `/ready`가 upstream 단절 시 실제로 503을
      반환하는지, `finllm_requests_in_flight`가 drain되는지 확인
- [ ] graceful shutdown(SIGTERM) 버전 실험 — 이번엔 SIGKILL만 했다
- [ ] `FinLLMGPUMemoryAboveProfileClass`가 `pending`에서 끝났다. `for: 1m`을
      넘기기 전에 복구해서 `firing`까지 가지 않았다. firing 경로는 미검증

## 10. 검증하지 못한 것

- **gateway 계층 전체.** A파트 미배포. `finllm_*` metric, `/ready` 전이,
  in-flight drain은 이번 실험에서 관측할 수 없었다 → `PENDING_VALIDATION`
- **`FinLLMGPUMemoryAboveProfileClass`의 firing 전이.** `pending`만 확인했다.
- **사용자 영향.** 실험 중 실제 요청 트래픽을 흘리지 않았다. 따라서
  "장애 중 오류율"은 `NOT_MEASURED`다.
- **알림 전달.** alertmanager를 두지 않았으므로 alert가 사람에게 도달하는
  경로는 검증 범위 밖이다.
- **이 결함이 2026-08-08c 측정을 오염시켰는지.** 정황상 아니지만
  (peak VRAM이 구성별로 뚜렷이 갈린다) 확정할 수 없다 → `NOT_MEASURED`
