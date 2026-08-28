# Quantization Autopsy — Evidence Report

> 18 runs · 6 configurations · 3 repetitions · NVIDIA RTX A6000

## 결론

- 14B AWQ는 CUDA graph를 끄자 처리량이 **5.47×** 회복됐다.
- 같은 변경에서 8B BF16 처리량 변화는 **-3.05%**였다.
- 14B AWQ의 사용자 P95 TTFT는 **80.56%** 감소했다.
- AWQ 모델 가중치는 8B BF16보다 **5.90GiB** 작았고, KV cache는 **5.58GiB** 더 확보했다.

따라서 초기의 ‘AWQ라서 느리다’는 설명은 기각한다. 확인된 범위는 vLLM 0.9.2 + Ampere + 해당 AWQ 모델에서 graph-enabled path와 성능 저하가 함께 나타났다는 것까지다.

## 구성별 집계

| 구성 | 품질 | 사용자 P95 TTFT | 처리량 | Peak VRAM | 최대 동시성 |
|---|---:|---:|---:|---:|---:|
| 8B BF16 · class ceiling · cuda graph | 95.259 | 1345.1ms | 296.0 tok/s | 26.14GiB | 7.31 |
| 8B BF16 · class ceiling · eager | 95.926 | 1344.7ms | 287.0 tok/s | 24.01GiB | 7.31 |
| 15B AWQ 4-bit · class ceiling · cuda graph | 98.333 | 6740.6ms | 57.2 tok/s | 27.55GiB | 11.03 |
| 15B AWQ 4-bit · class ceiling · eager | 97.667 | 1310.6ms | 313.2 tok/s | 23.84GiB | 11.05 |
| 15B AWQ 4-bit · deployment matched · cuda graph | 98.333 | 6784.9ms | 56.8 tok/s | 25.67GiB | 9.51 |
| 15B AWQ 4-bit · deployment matched · eager | 97.667 | 1273.4ms | 315.3 tok/s | 21.96GiB | 9.53 |

## 권고

**15B AWQ 4-bit · deployment matched · eager**

24GB executor budget에서 품질·사용자 TTFT·처리량 gate를 만족하고 A6000 관측 peak 기준 약 2GiB 여유를 남긴 구성.

## 증거 경계

- A6000 관측값이며 실제 RTX 4090/24GB 성능이 아니다.
- 8B BF16과 14B AWQ는 모델 크기가 달라 순수 양자화 효과를 식별할 수 없다.
- CUDA graph의 정확한 kernel root cause는 프로파일러로 측정하지 않아 NOT_MEASURED다.
- 실제 24GB Ada/Blackwell 카드 검증은 NOT_EXECUTED다.
- 금융 QA 60문항 합성 평가셋 결과를 production traffic으로 일반화하지 않는다.
