# Runbook — Rollback

목표 복구시간: **15분** (`docs/project-brief.md` "모델 변경 rollback 목표시간").

되돌리는 단위는 파일 하나가 아니라 **release**다. 모델 revision, tokenizer
revision, prompt revision, corpus/eval 버전, retriever 설정, vLLM 실행 인자가
한 덩어리로 움직인다. 그 중 하나만 되돌리면 조합이 측정된 적 없는 상태가 되고,
그 상태의 품질은 아무도 모른다.

## 언제 되돌리는가

**원인을 다 밝히기 전에 되돌린다.** 판단 기준은 단순하다.

| 상황 | 조치 |
|---|---|
| 배포 직후 SLO 위반이 시작됨 | 즉시 rollback. 원인 규명은 그 다음 |
| 권한 위반(ACL) 1건이라도 관측 | 즉시 rollback. 품질 점수와 무관 |
| peak VRAM이 24GB class 초과 | 즉시 rollback |
| 오래된 잠재 결함이 이제 드러남 | rollback으로 안 고쳐진다. 원인 분석 우선 |

## 0. 되돌리기 전에 증거부터 수집한다

재기동은 증거를 지운다. 30초면 된다.

```bash
mkdir -p ops/evidence/incident-$(date +%Y%m%d-%H%M)
E=ops/evidence/incident-$(date +%Y%m%d-%H%M)

curl -s http://127.0.0.1:9090/api/v1/alerts            > $E/alerts.json
curl -s 'http://127.0.0.1:9090/api/v1/targets?state=active' > $E/targets.json
cp ops/release/current-release.json                      $E/release-at-incident.json
tail -200 work/v02/serve-baseline.log                  > $E/serve-tail.log
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv > $E/nvidia-smi.csv
```

## 1. 현재 상태 확인

```bash
python3 scripts/rollback_release.py current
python3 scripts/rollback_release.py list
```

`->` 표시가 active release다. `GATE` 열이 `pass`인 것 중 가장 최근 것이
되돌릴 대상이다.

## 2. 되돌린다

```bash
python3 scripts/rollback_release.py rollback \
  --to 2026-08-09-baseline \
  --reason "P95 TTFT SLO 초과가 배포 직후 시작" \
  --incident INC-002 \
  --exec
```

- `--reason`은 **필수**다. 이유 없는 rollback은 다음 사람에게 아무것도 알려주지 않는다.
- `--exec` 없이 실행하면 명령만 출력하고 아무것도 바꾸지 않는다(dry run처럼 쓸 수 있다).
- gate를 통과한 적 없는 release로는 되돌릴 수 없다. 그건 rollback이 아니라 새 배포다.
  정말 필요하면 `--allow-failed-gate`를 쓰고, 그 사실이 로그에 남는다.

## 3. 복구를 확인한다 — 프로세스가 아니라 metric으로

프로세스가 살아난 것은 복구가 아니다. 선언된 release와 실제 서비스가 같아야 한다.

```bash
python3 scripts/rollback_release.py verify
```

이 명령은 두 가지를 본다.

1. `/v1/models`가 manifest의 모델을 서비스 중인가
2. (A파트 배포 시) `finllm_build_info`의 revision 라벨이 manifest와 일치하는가

그다음 관측 지표가 정상으로 돌아왔는지 본다.

```bash
curl -s --data-urlencode 'query=up{job="vllm"}' http://127.0.0.1:9090/api/v1/query
curl -s http://127.0.0.1:9090/api/v1/alerts | python3 -c "
import json,sys
alerts=json.load(sys.stdin)['data']['alerts']
print('firing:', [a['labels']['alertname'] for a in alerts if a['state']=='firing'] or 'none')"
```

## 4. 되돌린 구성이 정말 known-good인지 확인한다

rollback 대상이 오래된 것이면 그 사이 corpus나 평가셋이 바뀌었을 수 있다.

```bash
python3 scripts/regression_gate.py --stage cpu
python3 scripts/regression_gate.py --stage gpu --base-url http://127.0.0.1:8000/v1
```

여기서 실패하면 **rollback 대상 자체가 더 이상 유효하지 않다.** 그 경우는
release가 아니라 데이터 쪽이 바뀐 것이므로 `retriever-config-hash`와
`eval-set-integrity` 결과를 먼저 본다.

## 5. 기록

```bash
cat ops/release/rollback-log.jsonl | tail -3
```

append-only 로그다. 편집하지 않는다. 이후 incident report에서 이 로그의
`at_utc`와 `elapsed_seconds`를 복구시간 근거로 인용한다.

incident report는 [INCIDENT-TEMPLATE.md](../incidents/INCIDENT-TEMPLATE.md)를 복사해 쓴다.

## 6. rollback으로 해결되지 않는 것

- **평가셋·corpus 변경** — release 단위 밖이다. `eval-set-integrity`,
  `retriever-config-hash` gate가 이것을 잡는다.
- **드라이버·CUDA 문제** — 이 프로젝트에서 드라이버 버전은 건드리지 않는다.
  모든 baseline이 driver 535.288.01 / CUDA 12.2 기준이므로 바꾸면 baseline이
  전부 무효가 된다. 설비 담당에게 넘긴다.
- **호스트 GPU 점유** — GPU 1에 남의 프로세스가 있으면 rollback해도 같은 증상이
  난다. `nvidia-smi --query-compute-apps`로 먼저 확인한다.
