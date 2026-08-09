# Runbook — GPU 메모리 압박 / OOM

대응 alert: `FinLLMGPUMemoryAboveProfileClass`, `FinLLMPreemptionObserved`,
`FinLLMKVCacheHighUtilization`, `FinLLMGPUXidError`

## Symptom

- Grafana "VRAM이 위험 수준인가?" 패널이 24,576 MiB 점선을 넘는다.
- 또는 preemption이 0에서 올라간다.
- 또는 서버 로그에 `out of memory` / `No available memory`가 찍힌다.

## 0. 이 프로젝트에서 24GB 선이 무엇을 뜻하는가

점선 24,576 MiB는 `configs/profiles.json`의
`deployment_profiles.profile-a.vram_class_gib` = 24에서 온 값이다.

**이 선을 넘는다는 것은 "지금 죽는다"가 아니라 "Profile A의 대상 카드
(24GB class)에 더 이상 들어가지 않는다"는 뜻이다.** 실험 호스트는 A6000
48GB이므로 넘어도 당장은 돌아간다. 그러나 그 순간 이 구성은 프로젝트가
증명하려던 결론을 잃는다.

v0.1 실측 baseline: peak **21.96 GiB**(deployment-matched 0.46, `--enforce-eager`).
여유 약 2.0 GiB.

## 1. Check

```bash
# 지금 실제 사용량 (DCGM, GPU별)
curl -s --data-urlencode 'query=DCGM_FI_DEV_FB_USED' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool

# KV cache 사용률 (0-1 fraction. 100분율이 아니다 — 실측으로 확인함)
curl -s --data-urlencode 'query=vllm:gpu_cache_usage_perc' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool

# preemption
curl -s --data-urlencode 'query=increase(vllm:num_preemptions_total[10m])' \
  http://127.0.0.1:9090/api/v1/query | python3 -m json.tool

# 서버 기동 시 메모리 분해 — 무엇이 얼마를 먹었는지가 여기 다 있다
grep -E "Model loading took|Available KV cache memory|GPU KV cache size|Maximum concurrency|Graph capturing" \
  work/v02/serve-baseline.log
```

## 2. Diagnosis

| 관측 | 원인 | 조치 |
|---|---|---|
| `Graph capturing ... took N GiB` 가 로그에 있음 | **CUDA graph가 켜졌다** | 3-A |
| KV cache 사용률 높음 + preemption > 0 | 동시성/컨텍스트가 KV 용량 초과 | 3-B |
| `Model loading took` 이 baseline(9.37GiB)보다 큼 | 모델·양자화가 바뀌었다 | 3-C |
| GPU에 다른 프로세스가 있음 | 측정·용량이 오염됨 | 3-D |
| `XID_ERRORS > 0` | 하드웨어/드라이버 오류 | 3-E |

### 3-A CUDA graph — 이 프로젝트에서 가장 중요한 항목

v0.1에서 실측한 값이다. graph는 **executor 예산 바깥에서** 2.2–3.5 GiB를 쓴다.

| 구성 | graph 켬 | graph 끔(`--enforce-eager`) |
|---|---:|---:|
| 8B BF16 peak | 26.14 GiB | 24.01 GiB |
| 14B AWQ peak | 25.67 GiB | **21.96 GiB** |

**`gpu_memory_utilization`을 낮추는 것으로는 이 문제가 해결되지 않는다.**
가장 보수적인 설정(executor 22.08GiB)에서도 graph를 켠 총 사용량은
25.67GiB로 24GB 카드를 넘었다. "예산 안에 들어갔다"는 "카드에 들어간다"가
아니다.

확인:
```bash
grep -c "Graph capturing" work/v02/serve-baseline.log   # 0 이어야 정상
grep -- "--enforce-eager" ops/release/current-release.json
```

조치: `--enforce-eager`를 붙인 pin된 구성으로 되돌린다.
근거는 [ADR-0004](../../decisions/0004-profile-a-model-revised.md).

**단, Ampere 한정 판단일 수 있다.** ADR-0004는 이 CUDA graph 병리가
Ampere 고유일 가능성을 명시했다. Ada/Blackwell에서는 graph를 켠 쪽이 빠를
수 있고, 그 경우 이 결정은 대상 장비에서 다시 판단해야 한다.

### 3-B KV cache 부족

```bash
grep -E "GPU KV cache size|Maximum concurrency" work/v02/serve-baseline.log
```

baseline: KV cache 78,048 토큰, 8,192 토큰 기준 최대 동시성 **9.53**.

설계 동시성은 10인데 최대 동시성이 9.53이라는 점을 정확히 이해해야 한다.
이 값은 *모든* 요청이 8,192 토큰을 꽉 채울 때의 수치다. 실제 RAG 요청은
그보다 훨씬 짧아 동시성 10에서 preemption 없이 돌았다. 따라서:

- 짧은 요청에서 preemption이 보이면 → 요청 길이 분포가 변했다. 입력을 확인한다.
- 긴 컨텍스트 요청이 늘었으면 → 용량 재산정 대상이다.

`max_model_len`이나 `max_num_seqs`를 임시로 낮춰 넘기지 마라. 그것은 측정
조건 변경이므로 새 result 레코드와 새 baseline이 필요하다.

### 3-C 모델이 바뀜

```bash
python3 scripts/regression_gate.py --stage cpu
grep -E "model_revision|image_digest" ops/release/current-release.json
```

pin된 revision이 아니면 rollback한다(4번).

### 3-D 다른 프로세스

```bash
nvidia-smi --query-compute-apps=pid,used_memory,gpu_uuid --format=csv
```

이 호스트는 GPU 0에 상시 프로세스가 있다. 서비스는 **GPU 1**을 쓴다
(`docs/runbook-profile-a.md`). GPU 1에 남의 프로세스가 있으면 peak VRAM
수치는 오염된 것이므로 그 측정은 버린다.

### 3-E XID 오류

드라이버·CUDA는 **건드리지 않는다.** XID 코드와 시각을 기록해 설비 담당에
넘긴다. 이 프로젝트에서 드라이버 버전을 바꾸는 것은 모든 baseline을
무효화하는 행위다.

## 4. Rollback

```bash
python3 scripts/rollback_release.py list
python3 scripts/rollback_release.py rollback --to <previous-release-id> \
  --reason "peak VRAM이 24GB class를 초과"
```

## 5. Evidence to collect

- [ ] `DCGM_FI_DEV_FB_USED` 시계열 (장애 구간)
- [ ] 서버 로그의 메모리 분해 4줄: `Model loading took`, `Available KV cache
      memory`, `GPU KV cache size`, `Maximum concurrency`
- [ ] `Graph capturing` 라인 존재 여부 — 있으면 그것이 원인일 가능성이 가장 높다
- [ ] preemption 카운터 증가분
- [ ] `gpu_watch.py` 샘플링 결과 (peak는 종료 시점 값이 아니라 최대값이어야 한다)
- [ ] `nvidia-smi` compute apps — GPU 1에 다른 프로세스가 없었는가
- [ ] `ops/release/current-release.json`

```bash
python3 scripts/gpu_watch.py --gpu-index 1 --interval 0.3 --output work/v02/vram-incident.json
```
