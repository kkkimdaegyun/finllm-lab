# Runbook — 서비스가 준비되지 않음 / upstream 단절

대응 alert: `FinLLMServiceNotReady`, `FinLLMGatewayDown`, `FinLLMVLLMUpstreamDown`

이 runbook은 실제 장애 실험 [INC-001](../incidents/INC-001-vllm-upstream-outage.md)에서
검증했다. 아래 명령과 관측값은 그 실험에서 나온 것이다.

## Symptom

- Grafana "vLLM upstream up" 타일이 `UP` → `DOWN`으로 바뀐다.
- 또는 "gateway ready" 타일이 `NOT READY`가 된다.
- 사용자는 요청이 실패하거나 응답이 오지 않는다고 말한다.

## 1. Check — 어느 층이 끊겼는가

세 층을 위에서부터 순서대로 확인한다. **먼저 어느 층인지 확정하기 전에는
아무것도 재시작하지 마라.** 재시작은 증거를 지운다.

```bash
# (1) Prometheus가 각 target을 어떻게 보고 있는가 — 한 번에 전체 그림
curl -s 'http://127.0.0.1:9090/api/v1/targets?state=active' \
  | python3 -c "
import json,sys
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels'].get('job'):16} {t['health']:8} {t.get('lastError','')[:60]}\")"

# (2) 발화 중인 alert
curl -s http://127.0.0.1:9090/api/v1/alerts \
  | python3 -c "
import json,sys
for a in json.load(sys.stdin)['data']['alerts']:
    print(a['state'], a['labels']['alertname'], a['activeAt'])"

# (3) 각 층 직접 확인
curl -s -m 3 http://127.0.0.1:8080/ready    ; echo " <- gateway ready (A파트)"
curl -s -m 3 http://127.0.0.1:8080/health   ; echo " <- gateway health (A파트)"
curl -s -m 3 http://127.0.0.1:8000/health   ; echo " <- vLLM"
```

## 2. Diagnosis

| gateway /health | gateway /ready | vLLM up | 해석 | 이동 |
|---|---|---|---|---|
| 200 | 503 | 0 | **upstream 단절.** gateway는 정상이고 정직하게 503을 낸다 | 3-A |
| 200 | 503 | 1 | 기동 중이거나 index 로드 미완 | 3-B |
| 200 | 503 | 1 | graceful shutdown 진행 중 (`finllm_shutdown_in_progress=1`) | 3-C |
| 연결 거부 | 연결 거부 | 1 | gateway 프로세스 사망 | 3-D |
| 연결 거부 | 연결 거부 | 0 | 스택 전체 다운 | 3-D → 3-A |

`/health`는 200인데 `/ready`가 503인 것은 **정상 동작**이다. 계약상
`/health`는 프로세스 생존만, `/ready`는 트래픽 수용 가능 여부를 뜻한다
([interface-contract-v0.2.md](../../docs/cross-review/interface-contract-v0.2.md) 1.1절).
이 둘이 같이 움직이면 그것 자체가 결함이므로 A파트에 finding으로 남긴다.

## 3. Mitigation

**3-A vLLM upstream 단절**

INC-001에서 관측한 로그 신호를 먼저 찾는다.

```bash
tail -50 work/v02/serve-baseline.log
grep -iE "out of memory|CUDA error|Traceback|No available memory" work/v02/serve-baseline.log | tail
nvidia-smi --id=1 --query-gpu=memory.used,utilization.gpu --format=csv
```

- OOM 흔적이 있으면 → [gpu-oom.md](gpu-oom.md)
- 흔적 없이 죽었으면 → 재기동. GPU 메모리가 실제로 회수됐는지 먼저 본다.

```bash
# 재기동 전: GPU가 비었는지 확인. 안 비었으면 좀비 프로세스가 남은 것이다.
nvidia-smi --id=1 --query-gpu=memory.used --format=csv,noheader

# pin된 구성 그대로 재기동한다. 인자를 손으로 바꾸지 않는다.
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a --model Qwen/Qwen3-14B-AWQ \
  --revision 31c69efc29464b6bb0aee1398b5a7b50a99340c3 \
  --quantization awq --budget-mode deployment-matched --enforce-eager
```

**복구 확인은 프로세스가 살아난 것이 아니라 metric이 돌아온 것으로 한다.**

```bash
# up{job="vllm"} 이 1로 돌아왔는가
curl -s --data-urlencode 'query=up{job="vllm"}' http://127.0.0.1:9090/api/v1/query \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['result'])"
```

**3-B 기동 중**

정상 기동 시간은 이 구성에서 약 60–90초다(14B AWQ, `--enforce-eager`,
weight 9.37GiB). 이보다 오래 걸리면 로그에서 모델 다운로드 시도를 확인한다.
폐쇄망 전제이므로 런타임 다운로드는 그 자체가 결함이다.

**3-C graceful shutdown 중**

정상이다. in-flight 요청이 끝날 때까지 기다린다. `/ready`가 503,
`/health`가 200, `finllm_requests_in_flight`가 0으로 수렴하는지 본다.
0으로 수렴하지 않고 멈춰 있으면 drain이 걸린 것이므로 A파트 finding이다.

**3-D 프로세스 사망**

```bash
docker compose -f monitoring/compose.monitoring.yaml ps   # 관측 스택 (B파트)
docker compose ps                                         # 서비스 스택 (A파트)
```

관측 스택과 서비스 스택은 분리돼 있다. 관측 스택을 재시작해도 서비스는 죽지
않는다. 반대도 마찬가지다.

## 4. Rollback

기동 실패가 **최근 배포 직후**라면 원인 규명보다 복구가 먼저다.

```bash
python3 scripts/rollback_release.py list
python3 scripts/rollback_release.py rollback --to <previous-release-id> \
  --reason "배포 후 기동 실패"
```

기동조차 못 하는 구성은 그 자체가 결과다. `docs/runbook-profile-a.md`의
규칙대로 **설정을 조용히 바꿔 성공시킨 뒤 같은 조건이었던 것처럼 비교하지
않는다.** 바꿨다면 바꾼 값과 이유를 기록하고 별도 실행으로 남긴다.

## 5. Evidence to collect

- [ ] `curl -s http://127.0.0.1:9090/api/v1/alerts` — 어떤 alert가 언제 발화했는가
- [ ] `query_range`로 `up{job="vllm"}`의 1 → 0 → 1 전이 구간
- [ ] 장애 탐지 시간: 프로세스 종료 시각 → alert `firing` 시각
- [ ] 서버 로그 마지막 50줄 (종료 직전)
- [ ] `nvidia-smi` 메모리 회수 여부
- [ ] `ops/release/current-release.json`
- [ ] 복구 확인: `up`이 1로 돌아온 시각과 첫 성공 요청

탐지 시간은 alert의 `for:` 지속시간에 좌우된다. 현재 값은 측정 근거가 없어
`PENDING_THRESHOLD_VALIDATION`이므로, incident마다 실제 탐지 시간을 기록해
두면 그것이 threshold를 확정할 근거가 된다.
