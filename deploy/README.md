# FinLLM Lab v0.2 — A파트 배포

이 디렉터리는 기존 v0.1 RAG/evaluation semantics를 바꾸지 않고 단일 GPU
serving layer를 추가한다. 성능 결과는 생성하지 않으며, 이 문서의 상태 표에 실제
수행 범위를 명시한다.

## 구성

- `vllm`: 기존 권고 구성인 `Qwen/Qwen3-14B-AWQ`, immutable model/tokenizer
  revision, vLLM 0.9.2를 제공한다.
- `finllm-api`: 기존 `scripts/rag_index.py`의 `Retriever`와
  `scripts/rag_eval.py`의 `build_messages`를 adapter로 호출한다. retrieval 또는
  evaluation 구현을 복제하지 않는다.
- Prometheus/Grafana는 포함하지 않는다. Claude 파트가 같은 Compose network에서
  `finllm-api:8080/metrics`와 `vllm:8000/metrics`를 수집할 수 있다.

## 버전 및 CUDA 불변 조건

| 항목 | 고정 값 | 근거 |
|---|---:|---|
| system CUDA container line | 12.2.2 | 기존 환경의 CUDA 12.2를 유지한 digest-pinned NVIDIA base |
| Python | 3.10.12 | 기존 venv와 동일; source tarball SHA-256 검증 |
| vLLM | 0.9.2 | 기존 측정 환경 |
| PyTorch | 2.7.0 (`+cu126` runtime 계열) | 기존 측정 환경의 `2.7.0+cu126` |
| transformers | 4.53.2 | 기존 측정 환경 |
| model/tokenizer revision | `31c69efc29464b6bb0aee1398b5a7b50a99340c3` | `configs/model-candidates.json` |

`Dockerfile.vllm`은 `nvidia/cuda:12.2.2-devel-ubuntu22.04`를 digest로 고정한다.
호스트 CUDA toolkit과 NVIDIA driver를 설치하거나 변경하는 명령은 없다.
dependency lock에 있는 NVIDIA 12.6 패키지는 새로 선택한 system CUDA가 아니라,
기존 `torch 2.7.0+cu126` Python wheel runtime을 그대로 기록한 것이다. system CUDA,
driver, PyTorch wheel의 CUDA 표기를 같은 값으로 해석하지 않는다.

`devel` base를 사용한 이유는 vLLM/Triton의 runtime kernel compile 가능성을 보존하기
위해서다. API는 GPU code를 실행하지 않으므로 더 작은 Python slim image를 사용한다.
API dependency stage를 분리해 lock 변경 전에는 dependency layer를 재사용한다.

## 실행

전제 조건은 Docker, Docker Compose, NVIDIA Container Toolkit, 사용 가능한 단일 GPU,
그리고 model artifact 다운로드 권한이다. 선택한 GPU는 `FINLLM_GPU_DEVICE_ID`로만
지정하며 CUDA 설정을 변경하지 않는다.

```bash
cp deploy/.env.example deploy/.env
docker compose --env-file deploy/.env --file deploy/compose.yaml up --build
```

기본값을 그대로 쓸 때의 한 명령 wrapper는 다음과 같다.

```bash
scripts/deploy/up.sh
```

Hugging Face token이 필요한 환경에서는 token 문자열을 `.env`에 넣지 않고 읽기 전용
파일 경로만 지정한다.

```bash
FINLLM_HF_TOKEN_FILE=/absolute/path/to/hf-token scripts/deploy/up.sh
```

model cache와 생성된 lexical index는 각각 named volume에 저장한다. API container는
non-root, read-only root filesystem으로 실행하고 index volume만 쓸 수 있다.

## API 계약

- `GET /health`: API 프로세스가 요청을 처리할 수 있을 만큼 살아 있는지만 검사한다.
  GPU, retriever, model 상태를 검사하지 않으며 정상일 때 `200`이다.
- `GET /ready`: application, retriever, inference endpoint, configured model,
  request-admission 상태를 각각 반환한다. 하나라도 거짓이면 `503`이다.
- `GET /metrics`: Prometheus text exposition이다.
- `POST /v1/rag/chat/completions`: `question`과 `role` 문자열을 받는다.

예시 요청:

```bash
curl --fail --header 'Content-Type: application/json' \
  --data '{"question":"고액현금거래 보고 기한은?","role":"branch-staff"}' \
  http://127.0.0.1:8080/v1/rag/chat/completions
```

response에는 answer 외에 request ID, retrieved chunk IDs, retriever config hash,
model/tokenizer/prompt revision, corpus version을 포함한다. 더 자세한 안정 계약은
`docs/cross-review/v0.2-interface-contract.md`에 있다.

## 시작과 종료 정책

API entrypoint는 매 시작마다 기존 deterministic index builder로 corpus를 읽어 index를
생성한다. 그 다음 config/corpus/index/API-key file/model ID와 revision/prompt revision을
검증하고, vLLM `/v1/models`에 configured model이 나타나는지 확인한다. 조건을 만족하지
않으면 프로세스는 exit code 2로 종료한다. silent fallback model이나 prompt는 없다.

`SIGTERM` 또는 `SIGINT`를 받으면 다음 순서로 동작한다.

1. admission gate를 닫아 새 RAG 요청에 `503`을 반환한다.
2. `/ready`를 즉시 not-ready로 바꾸고 HTTP accept loop를 중단한다.
3. 이미 admission을 통과한 요청은 configurable timeout 동안 완료할 수 있다.
4. resource와 server socket을 닫는다. timeout을 넘긴 handler는 프로세스 종료를
   무한정 막지 않는다.

기본 drain timeout 30초와 Compose stop grace 40초는 측정된 최적값이 아니라 안전한
초기 운영 정책이며 변경 가능하다. `stop_grace_period`는 항상 API drain timeout보다
길어야 한다. 이 값들의 A6000 부하 적정성은 `PENDING_VALIDATION`이다.

health와 readiness를 나눈 이유는 dependency 장애 때 orchestrator가 살아 있는
프로세스를 재시작 루프로 보내는 대신 트래픽만 차단할 수 있게 하기 위해서다.
readiness dependency probe는 background interval에만 수행하고 `/ready`는 cached 상태를
읽으므로 probe가 요청 latency에 직접 추가되지 않는다. metric update는 짧은 in-process
lock만 사용하고 exposition rendering은 scrape 시점에만 한다.

## 검증 명령

GPU가 필요 없는 검증:

```bash
python -m unittest \
  tests.test_service_endpoints \
  tests.test_service_inference \
  tests.test_service_startup \
  tests.test_service_shutdown \
  tests.test_deploy_contract
scripts/deploy/validate.sh
```

실제 stack을 먼저 시작한 뒤 수행하는 opt-in A6000 검증:

```bash
FINLLM_RUN_GPU_INTEGRATION=1 \
python -m unittest tests.test_deploy_gpu_integration
scripts/deploy/smoke.sh
```

GPU integration test는 A6000 이름, API readiness/metrics, vLLM model listing만
검사한다. benchmark 숫자를 만들거나 기존 memory-budget-emulation을
native-gpu-validation으로 바꾸지 않는다.

## 현재 검증 상태 (2026-08-09)

| 항목 | 상태 | 증거 |
|---|---|---|
| service/deploy deterministic tests 24개 | `VERIFIED` | 로컬 `python -m unittest ...` 성공 |
| 기존/신규 전체 회귀 111개 | `VERIFIED` | 기존 고정 venv에서 성공; GPU opt-in 3개 skip |
| Compose schema/render | `VERIFIED` | `docker compose ... config --quiet` 성공 |
| Python compile / shell syntax / JSON parse | `VERIFIED` | 로컬 정적 명령 성공 |
| API image build | `VERIFIED` | final local image ID `sha256:05a55cc5…6483c8d3` |
| vLLM GPU image build | `VERIFIED` | final local image ID `sha256:ec36cea2…fd99a3d`; GPU/model 미사용 |
| API container + stub inference smoke | `VERIFIED` | health/ready/RAG/metrics/SIGTERM 확인; GPU 증거 아님 |
| 기존 result schema/evidence validation | `VERIFIED` | `results/*.json` 27개 전수 성공 |
| `docker compose up` 실제 기동 | `NOT_EXECUTED` | model/GPU service 미기동 |
| real vLLM을 연결한 endpoint container smoke | `NOT_EXECUTED` | model service 미기동 |
| 실제 A6000 native validation | `PENDING_VALIDATION` | opt-in integration 미수행 |
| latency/throughput/VRAM | `NOT_MEASURED` | 이번 구현 단계에서 benchmark 미수행 |

## 알려진 제한

- local image ID는 기록했지만 registry push digest와 SBOM은 아직 생성하지 않았다.
- model revision은 immutable SHA로 pin했지만 다운로드된 개별 artifact hash manifest는
  아직 생성하지 않았다.
- Python package는 정확한 version으로 lock했지만 wheel hash lock은 아직 없다.
- CUDA/Python/core package version과 base digest는 고정했지만 Ubuntu APT repository를
  snapshot date로 고정하지 않았다. 따라서 보안 package revision에 따라 재빌드의 local
  image ID가 달라질 수 있다.
- shutdown 기본값, shared-memory 크기, readiness interval은 운영 초기값이며 실제 부하
  검증 전에는 적정하다고 주장할 수 없다.
- TLS, authentication, rate limiting은 이 single-host A파트 범위에 포함하지 않았다.
  host port는 loopback에만 bind한다.
- vLLM/GPU metric naming과 dashboard/alert는 Claude 소유 영역이다.
