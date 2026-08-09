# 관측 스택 (B파트)

Prometheus + Grafana + DCGM exporter. A파트(Codex)의 deploy compose와 **분리된
스택**이다. 관측 스택을 재시작해도 서비스는 죽지 않고, 반대도 마찬가지다.
INC-001에서 서비스가 죽는 동안 관측 스택이 살아남아 장애를 기록한 것이 이
분리의 근거다.

## 실행

```bash
docker network create finllm-net          # 최초 1회. A파트 compose도 이 network에 붙는다
docker compose -f monitoring/compose.monitoring.yaml up -d
```

| | 주소 | 비고 |
|---|---|---|
| Prometheus | http://127.0.0.1:9090 | localhost 바인딩 |
| Grafana | http://127.0.0.1:3000 | `GRAFANA_ADMIN_PASSWORD` 환경변수로 비밀번호 지정 |
| DCGM exporter | http://127.0.0.1:9400/metrics | |

익명 접근은 꺼져 있다. 폐쇄망 금융 RAG를 전제하는 프로젝트이고, 대시보드에도
문서 ID와 사용률이 드러난다.

## scrape 대상

| job | 출처 | 상태 |
|---|---|---|
| `finllm-gateway` | A파트 gateway `:8080/metrics` | **미배포.** `targets/gateway.json`이 빈 배열 |
| `vllm` | vLLM `:8000/metrics` | 검증 완료 |
| `dcgm` | DCGM exporter `:9400` | 검증 완료 |

`finllm-gateway`의 target 목록만 `file_sd_configs`로 분리했다. A파트가 아직
없을 때 존재하지 않는 서비스에 대해 `ServiceNotReady`를 울리면 거짓 신호이고,
거짓 신호는 alert 전체의 신뢰를 깎기 때문이다.

A파트가 배포되면 두 파일을 고친다.

```bash
# monitoring/prometheus/targets/gateway.json
[{"targets": ["finllm-gateway:8080"]}]

# monitoring/prometheus/targets/vllm.json   (호스트 검증용 -> 컨테이너)
[{"targets": ["vllm:8000"]}]
```

Prometheus는 `--web.enable-lifecycle`로 떠 있으므로 재시작 없이 반영된다.

```bash
curl -X POST http://127.0.0.1:9090/-/reload
```

## metric 이름은 추정하지 않았다

`vllm:*`과 `DCGM_*` 이름은 실행 중인 endpoint에서 직접 받아 기록했다.

- `ops/evidence/vllm-0.9.2-metric-names.txt` (67개)
- `ops/evidence/dcgm-metric-names.txt` (9개)
- `ops/evidence/vllm-ttft-buckets.txt`

`finllm_*`은 아직 존재하지 않는다. A파트가 노출하기로
[계약](../docs/cross-review/interface-contract-v0.2.md)한 이름이다.

`tests/test_monitoring_config.py`가 대시보드와 alert가 참조하는 모든 metric이
위 둘 중 하나에 속하는지 검사한다. 오타로 만들어진 빈 패널은 건강한 서비스와
구분되지 않기 때문이다.

## 설정하면서 실제로 부딪힌 것

**1. DCGM 기본 collect-interval(30s)이 이 프로젝트의 부하를 놓친다.**
부하시험이 2초 버스트라, endpoint가 `GPU_UTIL=91`을 보고하는 동안 Prometheus에는
`0`이 저장되어 있었다. `-c 2000`으로 낮춰 scrape 간격(5s)보다 짧게 만들었다.
수정 후 부하 중 64 → 89 → 90 → 92로 추적하는 것을 확인했다.

**2. DCGM 기본 field set에 `FB_TOTAL`이 없다.**
"GPU memory total"을 수집하려면 명시적으로 추가해야 한다.
`dcgm/finllm-counters.csv`에서 필요한 9개만 골랐다. 각 줄이 대시보드 질문
하나 또는 alert 하나에 대응한다.

**3. vLLM TTFT histogram에 `le=2.0` 경계가 없다.**
bucket이 `… 1.0, 2.5, 5.0 …`이라 이 프로젝트의 2,000ms SLO를 정확히 판정할 수
없다. 그래서 gateway histogram에 `le=2`를 계약으로 요구하고, vLLM 쪽 alert는
조기 신호(warning)로만 쓴다.

**4. `vllm:gpu_cache_usage_perc`는 0–1 fraction이다.**
100분율로 착각하고 threshold를 90으로 두면 영원히 울리지 않는다.

## threshold 출처

근거 있는 값은 전부 `configs/profiles.json`에서 온다. rule 파일에 복사하지
않고, `regression_gate.py`의 `alert-threshold-consistency` 단계가 두 값이
같은지 강제한다.

근거 **없는** 값은 annotation에 `PENDING_THRESHOLD_VALIDATION`으로 표시했다.
모든 `for:` 지속시간, 모든 rate window, KV cache 0.9가 여기 해당한다.
자세한 내용은 [ADR-0006](../decisions/0006-observability.md).

## 검증 명령

```bash
# rule/config 문법
docker exec finllm-prometheus promtool check rules /etc/prometheus/rules/finllm-alerts.yml
docker exec finllm-prometheus promtool check config /etc/prometheus/prometheus.yml

# target이 실제로 붙었는가
curl -s 'http://127.0.0.1:9090/api/v1/targets?state=active' | python3 -c "
import json,sys
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels'].get('job'):16} {t['health']}\")"

# 지금 발화 중인 alert
curl -s http://127.0.0.1:9090/api/v1/alerts | python3 -c "
import json,sys
a=json.load(sys.stdin)['data']['alerts']
print('none' if not a else '')
for x in a: print(x['state'], x['labels']['alertname'], x['activeAt'])"

# 설정 정적 검사 (GPU·docker 불필요)
python3 -m unittest tests.test_monitoring_config -v
```

## 아직 검증하지 못한 것

- `finllm_*` metric 전부 — A파트 미배포 → `PENDING_VALIDATION`
- retrieval / generation 병목 분해 패널 — 위와 같은 이유로 빈 패널이다
- `for:` 지속시간의 타당성 — INC-001에서 탐지 35초를 측정했으나 1회뿐이다
- alert가 사람에게 도달하는 경로 — alertmanager를 두지 않았다
