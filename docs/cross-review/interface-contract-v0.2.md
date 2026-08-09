# 인터페이스 계약 v0.2 (A파트 Codex ↔ B파트 Claude)

v0.1의 [`interface-contract.md`](interface-contract.md)는 그대로 유효하다. 이
문서는 v0.2에서 새로 생기는 접점만 다룬다.

| | 담당 | 소유 |
|---|---|---|
| **A파트** | Codex | `Dockerfile`, deploy compose, service core, `/health`·`/ready` 구현 |
| **B파트** | Claude | `monitoring/`, `ops/`, `scripts/regression*`, `scripts/rollback*`, `tests/test_regression*`, `tests/test_monitoring*`, CI |

B파트는 A파트 파일을 수정하지 않는다. 문제를 발견하면 cross-review finding으로
남긴다.

## 1. B파트가 A파트에 요구하는 것

### 1.1 endpoint

| 경로 | 포트 | 의미 |
|---|---:|---|
| `/health` | 8080 | 프로세스 살아 있음. 의존성 상태를 보지 않는다 |
| `/ready` | 8080 | 트래픽 수용 가능. vLLM upstream과 index 로드가 끝났을 때만 200 |
| `/metrics` | 8080 | Prometheus text exposition format 0.0.4 |

`/ready`는 **graceful shutdown이 시작되면 즉시 503을 반환**해야 한다. drain 중
`/health`는 200을 유지한다. 이 둘이 갈라지지 않으면 in-flight drain을 관측할
방법이 없다.

`/metrics`는 인증 없이 접근 가능해야 한다. Prometheus 컨테이너가 같은 docker
network 안에서 scrape한다.

### 1.2 metric

이름·타입·라벨은 아래를 그대로 쓴다. B파트의 alert rule과 dashboard가 이
이름에 직접 의존한다.

```
# --- request ---
finllm_requests_total{route,method,status}            counter
finllm_request_duration_seconds{route}                histogram
finllm_requests_in_flight{route}                      gauge

# --- retrieval ---
finllm_retrieval_duration_seconds                     histogram
finllm_retrieval_results                              histogram
finllm_retrieval_empty_total                          counter
finllm_retrieval_acl_filtered_total                   counter

# --- generation ---
finllm_generation_duration_seconds                    histogram
finllm_generation_ttft_seconds                        histogram
finllm_generation_tokens_total{kind}                  counter   kind=prompt|completion
finllm_generation_errors_total{reason}                counter

# --- lifecycle ---
finllm_ready                                          gauge     1|0
finllm_shutdown_in_progress                           gauge     1|0

# --- provenance ---
finllm_build_info{git_sha,image_digest,model_id,model_revision,
                  tokenizer_revision,prompt_revision,corpus_version,
                  eval_set_version,retriever_config_hash}   gauge  항상 1
```

**histogram bucket은 다음으로 고정한다.**

```
0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10
```

**`le=2` 경계는 선택이 아니라 요구사항이다.** 이 프로젝트의 hard gate는
`p95_ttft_ms ≤ 2000`이고(`configs/profiles.json`의 `benchmark_policy`),
`histogram_quantile`은 bucket 안에서 선형보간한다. 2초가 경계가 아니면 P95
alert가 gate와 다른 값을 보게 된다.

이건 가정이 아니라 실측이다. 실행 중인 vLLM 0.9.2의 `/metrics`에서 확인한
`vllm:time_to_first_token_seconds`의 bucket 경계는 다음과 같다
(`ops/evidence/vllm-ttft-buckets.txt`).

```
0.001 0.005 0.01 0.02 0.04 0.06 0.08 0.1 0.25 0.5 0.75 1.0 2.5 5.0 …
```

`1.0` 다음이 `2.5`다. **`2.0`이 없다.** 따라서 vLLM 자체 histogram으로 계산한
P95는 `[1.0, 2.5]` 구간의 선형보간값이며 2초 gate 판정에 쓸 수 없다.
gateway histogram이 `le=2`를 가져야 하는 이유가 이것이다.
`tests/test_monitoring_config.py`가 이 사실을 회귀 테스트로 고정해 둔다.

`status`는 HTTP status code 문자열(`"200"`, `"503"`)이다. `outcome` 같은 별도
분류를 추가하지 마라 — alert 식이 `status=~"5.."`를 쓴다.

### 1.3 provenance

`finllm_build_info`의 라벨 값은 `ops/release/current-release.json`(B파트 소유)의
같은 이름 필드와 **일치해야 한다.** rollback 이후 두 값이 어긋나면 어떤 버전이
실제로 돌고 있는지 알 수 없다.

A파트는 이 파일을 읽기만 하고 쓰지 않는다. 형식은 [3절](#3-release-manifest)에 있다.

### 1.4 docker network

deploy compose는 외부 network `finllm-net`에 붙는다. monitoring stack이 같은
network로 들어와 서비스 이름으로 scrape한다.

```yaml
networks:
  finllm-net:
    external: true
```

서비스 이름은 `finllm-gateway`, `vllm`으로 고정한다. Prometheus의 scrape target이
이 이름을 쓴다.

## 2. A파트가 B파트에 요구할 수 있는 것

- alert rule 추가·threshold 조정 → `monitoring/prometheus/rules/`에 요청
- 신규 metric이 필요하면 이 문서에 먼저 이름을 추가하고 양쪽이 합의한다

## 3. Release manifest

`ops/release/current-release.json`. rollback과 provenance의 단일 기준점이다.

```json
{
  "schema_version": "1.0.0",
  "release_id": "2026-08-09-a",
  "promoted_at_utc": "2026-08-09T00:00:00Z",
  "git_sha": "…",
  "image_digest": "sha256:…",
  "model_id": "Qwen/Qwen3-14B-AWQ",
  "model_revision": "31c69efc29464b6bb0aee1398b5a7b50a99340c3",
  "tokenizer_revision": "31c69efc29464b6bb0aee1398b5a7b50a99340c3",
  "prompt_revision": "prompt-v0.1",
  "corpus_version": "corpus-v0.1",
  "eval_set_version": "eval-v0.1",
  "retriever_config_hash": "11d1f8cfeb42",
  "vllm_args": { "...": "serve-command와 동일" },
  "regression_gate": { "status": "pass", "evidence": "work/v02/…" }
}
```

`schemas/release-manifest.schema.json`이 이 구조를 강제한다.

## 4. 불변 규칙 (v0.2 추가분)

v0.1의 5개 규칙에 이어진다.

6. **regression gate를 통과하지 않은 release manifest를 promote하지 않는다.**
   `scripts/rollback_release.py promote`가 이를 강제한다.
7. **`configs/profiles.json`의 `benchmark_policy` 값을 코드에 복사하지 않는다.**
   alert threshold와 gate threshold는 전부 이 파일에서 읽는다. 두 벌이 되면
   반드시 어긋난다.
8. **GPU가 없는 환경에서 GPU 단계를 통과한 것처럼 보고하지 않는다.**
   regression gate는 `--stage cpu`와 `--stage gpu`를 분리하고, CI의 CPU job은
   GPU 단계를 `skipped`로 명시한다.
