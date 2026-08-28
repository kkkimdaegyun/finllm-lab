# Quantization Autopsy

> 양자화 모델이 느렸다는 관측을 "4-bit라서"라고 단정하지 않고, 실행 경로·메모리
> 예산·KV cache·사용자 지연을 분해해 원인을 좁힌 재현 가능한 GPU 성능 분석 프로젝트.

## 한 줄 결론

RTX A6000에서 Qwen3-14B-AWQ의 낮은 처리량은 AWQ 자체의 일반적 특성으로 확정되지
않았다. 같은 모델·예산에서 CUDA graph를 끄자 처리량이 약 5배 회복됐고, 8B BF16은
거의 변하지 않았다. 따라서 관측 범위는 `vLLM 0.9.2 + Ampere + 해당 AWQ 모델`의
graph-enabled 실행 경로로 제한한다.

## 개발자 관점에서 보여주는 것

- 18개 immutable result JSON의 provenance와 반복 수를 검증한다.
- 서버 TTFT와 사용자 TTFT를 분리해 queueing을 숨기지 않는다.
- weights, KV cache, CUDA graph, peak VRAM을 같은 표에서 비교한다.
- 평균뿐 아니라 표본 표준편차를 보존한다.
- 서로 다른 모델 크기 비교로는 순수 양자화 효과를 식별할 수 없음을 자동 보고한다.
- 정적 HTML 대시보드와 기계 판독 가능한 JSON/CSV/Markdown을 한 명령으로 생성한다.

## 실행

추가 패키지가 필요 없는 Python 표준 라이브러리 프로젝트다.

```bash
cd projects/quantization-autopsy
python src/autopsy.py build
python -m unittest discover -s tests -v
python src/autopsy.py check
```

생성물:

- `artifacts/summary.json`: 집계값, 비교값, 증거 경계
- `artifacts/benchmark.csv`: 6개 구성의 평균·표준편차
- `artifacts/REPORT.md`: 면접과 코드 리뷰에 적합한 분석 기록
- `portfolio/index.html`: Noto Sans KR 기반 인터랙티브 대시보드

## 실험 매트릭스

| 축 | 값 |
|---|---|
| GPU | NVIDIA RTX A6000 1장 |
| 모델 | Qwen3-8B BF16, Qwen3-14B-AWQ |
| 런타임 | vLLM 0.9.2 |
| 메모리 모드 | 24GB class ceiling, deployment matched |
| 실행 경로 | CUDA graph, `--enforce-eager` |
| 반복 | 구성별 3회 |
| 부하 | 동시성 10, 30요청 |

## 증거 경계

모든 수치는 A6000에서 24GB executor budget을 모사한
`memory-budget-emulation`이다. 실제 RTX 4090/24GB 카드 성능이라고 주장하지 않는다.
또한 8B BF16과 14B AWQ는 파라미터 수가 달라 순수한 BF16↔AWQ 품질 효과를 분리하지
못한다. 이 프로젝트가 확정한 것은 "실행 경로가 양자화 모델의 성능 결론을 뒤집을 수
있다"는 점이다.

원천 실험과 서비스 운영 루프는 FinLLM Lab v0.2에 있으며, 이 디렉터리는 분석과
시각화를 독립된 포트폴리오 단위로 제공한다.
