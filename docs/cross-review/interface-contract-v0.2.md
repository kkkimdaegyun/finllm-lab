# FinLLM Lab v0.2 integrated interface contract

Status: `ACCEPTED_FOR_RELEASE_REHEARSAL`
Scope: canonical tree `/home/dgkim/dgkim/finllm-lab`

두 개로 갈라졌던 A/B 계약을 이 문서로 대체한다. `service/`, `deploy/`,
`monitoring/`, regression과 rollback은 이 계약을 함께 사용한다.

## HTTP endpoints

| Endpoint | 의미 | Success |
|---|---|---|
| `GET /health` | API process liveness만 확인 | `200 {"status":"alive"}` |
| `GET /ready` | app, retriever, inference endpoint, model engine, admission 확인 | 모든 check가 true일 때만 200, 그 외 503 |
| `GET /metrics` | Prometheus text exposition 0.0.4 | 200 |
| `POST /v1/rag/chat/completions` | ACL retrieval + vLLM generation | 200 또는 명시적 4xx/5xx |

generation failure는 model readiness를 내리고, background recovery probe가 실제
generation path 성공을 확인할 때만 복구한다. graceful shutdown 중 listener는
accepted 요청이 drain되는 동안 살아 있어 `/health=200`, `/ready=503`, 신규
POST=503을 명시적으로 반환한다.

## Application metrics

Label이 없는 single-service contract다. cardinality를 만들지 않으며 error ratio는
별도 error counter로 계산한다.

| Metric | Type |
|---|---|
| `finllm_requests_total` | counter |
| `finllm_request_errors_total` | counter |
| `finllm_requests_in_flight` | gauge |
| `finllm_request_duration_seconds` | histogram |
| `finllm_retrieval_duration_seconds` | histogram |
| `finllm_generation_duration_seconds` | histogram |
| `finllm_ready` | gauge |
| `finllm_shutdown_in_progress` | gauge |
| `finllm_build_info` | gauge with immutable provenance labels |

Histogram bucket은 다음과 같다.

```text
0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 2.5,
5, 10, 30, 60, 180, +Inf
```

`le=2` 경계는 존재하지만 application histogram은 TTFT가 아니다. 이 서비스는
non-streaming이므로 application TTFT를 측정했다고 주장하지 않는다. TTFT dashboard와
alert는 실제 vLLM 0.9.2 raw histogram을 사용하며, vLLM bucket에 `le=2`가 없어
보간값이라는 caveat를 유지한다.

Error ratio contract:

```promql
sum(rate(finllm_request_errors_total[1m]))
  / sum(rate(finllm_requests_total[1m]))
```

`finllm_build_info` labels:

```text
git_sha, image_digest, model_id, model_revision, tokenizer_revision,
prompt_revision, corpus_version, eval_set_version, retriever_config_hash
```

## Docker integration

- API Compose service/DNS: `finllm-api:8080`
- inference Compose service/DNS: `vllm:8000`
- shared external network: `finllm-net`
- Prometheus job: `finllm-gateway`, target `finllm-api:8080`
- vLLM Prometheus target: `vllm:8000`
- DCGM exporter exposes only `FINLLM_GPU_DEVICE_ID` (host default for this
  rehearsal: GPU 1)

`scripts/deploy/up.sh -d` creates the shared network and starts monitoring plus
serving. The model cache must already contain the pinned revision; runtime sets
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.

## Release and rollback

A promotable release requires:

- 40-character Git SHA
- non-null `sha256:` container image digest
- gate report whose internal overall is pass
- at least one executed stage; `stage=all` requires every CPU and GPU stage
- model/tokenizer revision, prompt, eval set and retriever hash provenance

Rollback is a transaction: restart succeeds → `/health` succeeds → `/ready`
succeeds → model/build provenance matches → only then current release state is
updated. Dry-run and failed restart never mutate release state.

## Evidence boundary

- Existing performance results are A6000 `memory-budget-emulation`.
- Integrated A6000 rehearsal evidence is recorded separately.
- Actual 24GB GPU native validation remains `NOT_EXECUTED`.
