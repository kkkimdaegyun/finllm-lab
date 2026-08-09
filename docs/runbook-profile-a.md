# Runbook — Profile A 실측

`docs/experiment-protocol.md` Stage B–D를 이 서버에서 실행하는 구체적 순서다.
결과 해석 규칙은 protocol을, 여기서는 명령만 다룬다.

## 사전 조건

- vLLM은 `.venv` 안에만 설치한다. 시스템 CUDA·드라이버·전역 torch는 건드리지
  않는다.
- **GPU 1을 쓴다.** GPU 0에는 다른 프로세스가 상주하고 있어 peak VRAM 측정이
  오염된다. `CUDA_VISIBLE_DEVICES=1`을 모든 실행에 붙인다.
- 모델은 pin된 revision으로 미리 받아둔다.

```bash
nvidia-smi --query-gpu=index,memory.used --format=csv
```

GPU 1의 `memory.used`가 수십 MiB 수준인지 확인하고 시작한다.

## 1. 환경 기록

```bash
.venv/bin/python scripts/capture_environment.py --output work/environment.json
```

## 2. 서버 실행 명령 생성

명령을 손으로 쓰지 않는다. 프로파일에서 생성해야 `gpu_memory_utilization`이
프로파일 정의와 어긋나지 않는다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model Qwen/Qwen3-8B \
  --revision b968826d9c46dd6066d109eabc6255188de91218 \
  --max-model-len 8192 \
  --max-num-seqs 10
```

출력된 명령 앞에 `CUDA_VISIBLE_DEVICES=1`과 venv 경로를 붙여 실행한다.
서버 로그는 반드시 파일로 남긴다.

```bash
CUDA_VISIBLE_DEVICES=1 .venv/bin/vllm serve ... > work/serve-8b-bf16.log 2>&1
```

## 3. 헬스 체크와 warm-up

```bash
curl -s http://127.0.0.1:8000/v1/models | head -c 400
```

warm-up 요청은 평가·측정과 분리한다.

```bash
.venv/bin/python scripts/load_test.py \
  --model Qwen/Qwen3-8B --dataset datasets/smoke.jsonl \
  --concurrency 1 --requests 3 --output work/warmup-8b.json
```

## 4. 부하 시험 (동시성 1 → 10, 3회 반복)

peak VRAM 샘플러를 먼저 띄우고, 부하 시험이 끝나면 멈춘다.

```bash
python3 scripts/gpu_watch.py --gpu-index 1 --interval 0.5 \
  --output work/vram-8b-c10-r1.json &
WATCH=$!

.venv/bin/python scripts/load_test.py \
  --model Qwen/Qwen3-8B --dataset datasets/smoke.jsonl \
  --concurrency 10 --requests 30 --output work/load-8b-c10-r1.json

kill -TERM $WATCH
```

`--concurrency 1`로 기준선을 먼저 잡고, 동시성 10을 **r1/r2/r3** 세 번 반복한다.
결과 파일 이름에 반복 회차를 남긴다.

`load_test.py`는 두 종류의 TTFT를 보고한다.

- `p95_ttft_ms` — 요청 전송부터 첫 토큰까지. 서버 관점.
- `p95_user_ttft_ms` — 요청 도착부터 첫 토큰까지. 클라이언트 대기(`p95_client_queue_ms`)를
  포함한 사용자 관점.

합격 판정에는 `p95_ttft_ms`를 쓰고, 두 값을 결과 JSON에 모두 남긴다. 어느 쪽을
썼는지 밝히지 않은 TTFT 수치는 비교에 쓸 수 없다.

## 5. 품질 평가

부하 시험과 섞지 않는다. 서버는 그대로 두고 별도로 실행한다.

```bash
python3 scripts/rag_index.py build --corpus corpus/v0.1 --output work/index-v0.1.json

.venv/bin/python scripts/rag_eval.py \
  --index work/index-v0.1.json \
  --model Qwen/Qwen3-8B \
  --save-retrieval work/retrieval-v0.1.json \
  --output work/eval-8b-bf16.json
```

두 번째 후보부터는 **동일한 retrieval**을 재사용해 generation-only 비교를 만든다.

```bash
.venv/bin/python scripts/rag_eval.py \
  --frozen-retrieval work/retrieval-v0.1.json \
  --model Qwen/Qwen3-14B-AWQ \
  --output work/eval-14b-awq-frozen.json
```

`overall_status`가 `fail`이면 권한 누출이 있었다는 뜻이다. 품질 점수와 무관하게
그 실행은 후보에서 탈락시키고 원인을 기록한다.

## 6. 14B AWQ 동일 조건 반복

8B와 **같은** `--max-model-len`, `--max-num-seqs`, 요청 집합, 동시성을 쓴다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model Qwen/Qwen3-14B-AWQ \
  --revision 31c69efc29464b6bb0aee1398b5a7b50a99340c3 \
  --quantization awq \
  --max-model-len 8192 \
  --max-num-seqs 10
```

기동에 실패하거나 OOM이 나면 **그것이 결과다.** 설정을 조용히 바꿔 성공시킨 뒤
같은 조건이었던 것처럼 비교하지 않는다. 바꿨다면 바꾼 값과 이유를 결과 JSON의
`notes`에 남기고 별도 실행으로 기록한다.

## 7. deployment-matched 재검증

class-ceiling을 통과한 후보만 보수적 예산으로 다시 실행한다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model Qwen/Qwen3-14B-AWQ \
  --revision 31c69efc29464b6bb0aee1398b5a7b50a99340c3 \
  --quantization awq \
  --budget-mode deployment-matched
```

## 8. 결과 기록

```bash
python3 scripts/finllm_profile.py new-result \
  --profile profile-a \
  --model Qwen/Qwen3-14B-AWQ \
  --revision 31c69efc29464b6bb0aee1398b5a7b50a99340c3 \
  --quantization awq \
  --evidence memory-budget-emulation \
  --budget-mode class-ceiling \
  --output results/2026-08-08-profile-a-qwen3-14b-awq-r1.json
```

측정값을 채운 뒤 검증한다. schema 위반과 evidence 라벨 오용을 여기서 잡는다.

```bash
python3 scripts/finllm_profile.py validate-result \
  results/2026-08-08-profile-a-qwen3-14b-awq-r1.json
```

이 실행들은 전부 A6000에서 이루어지므로 `evidence_type`은 반드시
`memory-budget-emulation`이다. `native-gpu-validation`은 실제 24GB 카드에서
다시 측정하기 전까지 쓸 수 없고, validator가 이를 막는다.
