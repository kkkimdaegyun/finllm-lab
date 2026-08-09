# 실험 프로토콜

## 1. 질문과 사전 등록

각 실험 배치 전에 아래 항목을 변경 불가능한 manifest로 기록한다.

- 업무 질문: 금융 내부문서 RAG를 동시 사용자 10명에게 제공할 때의 최소 비용
- 하드 게이트: P95 TTFT ≤ 2초, 오류율 ≤ 1%, OOM 0회, 품질 ≥ 90
- corpus와 평가셋 버전
- chunking, embedding, retriever, reranker 설정
- system prompt와 generation 설정
- 모델 ID, immutable revision, tokenizer revision
- vLLM, CUDA, driver, PyTorch 버전
- 물리 GPU 모델·개수와 적용한 memory utilization

실험 후 결과를 보고 임계값이나 평가 문항을 바꾸면 별도 실험 배치로 기록한다.

## 2. 단계별 평가

### Stage A — 정적 적합성

weight의 이론적 하한을 계산하고 예상되는 runtime/KV cache 여유를 검토한다.
이 계산만으로 “적합” 판정을 내리지 않는다.

### Stage B — A6000 메모리 예산 검증

동일한 A6000 한 장에서 `gpu_memory_utilization`만 프로파일에 맞게 바꾼다.

- Profile A: `0.50`
- Profile B: `0.67`
- Reference: `0.92`

이 값은 24/32/48GB class의 명목 상한을 비교하는 1차 probe다. 대상 24GB/32GB
카드에서 `0.92`를 쓸 계획이라면 executor 예산을 정확히 맞춘 보수적 2차
probe도 실행한다.

- Profile A deployment-matched: `24 × 0.92 ÷ 48 = 0.46`
- Profile B deployment-matched: `32 × 0.92 ÷ 48 ≈ 0.613` (`0.61` 사용)
- Reference deployment-matched: `0.92`

결과에는 `class-ceiling`과 `deployment-matched` 중 어느 방식인지 기록한다.

모델 로딩 성공, warm-up 성공, peak VRAM, KV cache capacity, 최대 context,
동시 요청 10개의 성공 여부를 기록한다.

이 단계의 `latency`와 `tokens/s`에는 반드시
`memory-budget-emulation` evidence label을 붙인다. 해당 수치는 A6000 관측값이며
4090/5090 성능 예측값이 아니다.

### Stage C — 품질 평가

모든 후보가 같은 retrieval 결과를 받도록 retrieval output을 동결한
`generation-only` 평가와, 실제 end-to-end RAG 평가를 둘 다 수행한다.

품질 점수:

```text
quality =
  answer_correctness × 0.40 +
  groundedness       × 0.30 +
  citation_accuracy  × 0.20 +
  abstention_safety  × 0.10
```

각 하위 점수는 0–100이다. 자동 평가를 쓰더라도 표본을 사람이 블라인드
재검토하고, 평가 모델과 prompt revision을 함께 저장한다.

### Stage D — 부하 시험

1. 서버 시작 후 health check
2. 평가와 분리된 요청으로 warm-up
3. 동시성 1에서 기준선 측정
4. 동시성 10에서 최소 30개 요청
5. 동일 조건으로 3회 반복
6. P50/P95 TTFT, P95 E2E, aggregate output tokens/s, 오류율, OOM 기록

질문 길이와 출력 길이 분포를 실제 업무와 비슷하게 고정한다. 단순한 짧은 질문만
반복해 얻은 높은 처리량은 정식 결과로 사용하지 않는다.

### Stage E — 대상 GPU 실측

Stage B–D를 통과한 최소 후보만 실제 24GB/32GB 대상 장비에서 다시 측정한다.
이 단계의 결과에만 `native-gpu-validation` label을 붙일 수 있다.

대상 장비 실측 전에는 다음과 같이 표현한다.

> 이 구성은 A6000에서 24GB executor budget 안에 적합했다. 성능 수치는
> A6000 관측값이며 RTX 4090 성능을 나타내지 않는다.

실측 후에는 GPU SKU, 보드 전력 제한, driver, CUDA, 냉각 상태까지 기록한다.

## 3. 양자화 매트릭스

| 실험 호스트 | AWQ | GPTQ | INT8 W8A8 | 일반 FP8 W8A8 |
|---|---:|---:|---:|---:|
| A6000 / Ampere | 가능 | 가능 | 가능 | 지원 안 함 |
| 4090 / Ada | 가능 | 가능 | 가능 | 가능 |

표는 실험 시점의 vLLM 공식 호환표를 다시 확인한다. Blackwell은 사용하는 vLLM
버전과 quantization kernel의 실제 지원 여부를 대상 장비에서 preflight한다.

## 4. 결과 판정

한 번이라도 OOM이 발생하거나 요청 오류율이 1%를 넘으면 실패다. 반복별 P95
TTFT를 모두 기록하고, 중앙값만 남기지 않는다.

권고안은 다음 순서로 고른다.

1. 모든 hard gate를 통과한 후보
2. 단일 GPU 조건을 만족한 후보
3. 총비용이 가장 낮은 후보
4. 비용 차이가 작으면 품질이 높은 후보

32B가 3점 높은 품질을 내더라도 P95 TTFT 기준을 넘으면 기본 권고안에서
제외한다. 2×A6000 결과는 품질 격차를 설명하는 기준선이지 이 순위의 후보가
아니다.

## 5. 보고서 문장 규칙

좋은 문장:

> 24GB memory budget에서 14B AWQ가 품질 92점과 P95 TTFT 1.6초를 기록해
> 서비스 기준을 통과했다. 이 속도는 A6000 관측값이며, 최종 처리량은 4090에서
> 별도 검증한다.

피해야 할 문장:

> A6000을 절반만 썼으므로 RTX 4090에서도 동일하게 72 tok/s가 나온다.

제품명은 대표 장비 예시로만 쓰고, 결론의 제목은 `24GB VRAM class`,
`32GB VRAM class`, `48GB enterprise/workstation class`로 쓴다.
