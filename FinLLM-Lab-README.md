# FinLLM Lab v0.1

> 금융 RAG 서비스를 GPU 한 장으로 운영한다면, 최소 어느 정도의 하드웨어에서
> 필요한 품질과 응답 성능을 얻을 수 있는가?

이 저장소는 가장 큰 모델을 실행하는 데모가 아니라, 제한된 GPU 예산 안에서
재현 가능한 근거로 배포 구성을 선택하는 포트폴리오 프로젝트다.

주 대상은 은행·회계법인·법무법인처럼 기밀 문서를 외부 서비스로 보내기 어려운
조직의 **온프레미스 Private RAG**다. 대표 GPU 이름은 비교를 돕기 위한 예시이며,
실제 운영 장비는 ECC, 공급사 지원, 서버 폼팩터, 전력·냉각, 조달 정책을 포함해
별도로 선정한다.

## 현재 상태

현재 저장소는 **실험 설계와 실행 골격이 준비된 상태**다. 아직 실제 GPU
benchmark 숫자나 품질 결과는 없으므로 완성 사례처럼 주장하지 않는다.

- 완료: 24/32/48GB profile, vLLM command builder, streaming load test
- 완료: 결과 schema, 실험 protocol, 폐쇄망 보안 architecture, 기본 CI
- 다음 작업: 본인 문제 정의, 모델 revision 고정, 평가셋 작성, 첫 실측

## 처음 시작할 때

가장 먼저 Kubernetes나 dashboard를 만들지 않는다. 아래 순서로 **Profile A
첫 비교표 한 줄**을 만드는 것이 첫 목표다.

1. [`docs/project-brief.md`](docs/project-brief.md)의 `[OWNER]` 항목을 본인
   선택으로 채운다.
2. A6000 환경 정보를 캡처한다.
3. `Qwen3-8B BF16`을 Profile A에서 실행해 smoke test를 통과시킨다.
4. `Qwen3-14B-AWQ`를 같은 조건에서 실행한다.
5. 두 모델의 peak VRAM, P95 TTFT, 오류율을 실제 값으로 기록한다.
6. 50–60문항의 본인 평가셋으로 품질 차이를 측정한다.
7. “어떤 모델을 왜 선택했는가”를 ADR로 작성한다.

상세한 첫 실행 순서는 [`docs/start-here.md`](docs/start-here.md)에 있다.

## 시작 모델 후보

첫 배치는 같은 계열 안에서 크기와 양자화 효과를 보기 위해 Qwen3 dense 모델로
통제한다. Qwen3는 8B/14B/32B dense 모델과 공식 AWQ 변형이 있고, Apache 2.0
라이선스 및 한국어 지원이 명시되어 있다. 이들은 **시작 후보**이지 미리 정한
승자가 아니다.

| 순서 | 모델 ID | 용도 | 첫 profile |
|---:|---|---|---|
| 1 | `Qwen/Qwen3-8B` | BF16 기준선 | Profile A |
| 2 | `Qwen/Qwen3-14B-AWQ` | 24GB best-value 후보 | Profile A |
| 3 | `Qwen/Qwen3-32B-AWQ` | 큰 모델 stretch 후보 | Profile B |
| 4 | `Qwen/Qwen3-14B` | BF16 대비 양자화 품질 비교 | Reference |

모델 목록은 [`configs/model-candidates.json`](configs/model-candidates.json)에
있다. 정식 실행 전 `main` 대신 immutable commit SHA를 확인해 기록한다.

```bash
git ls-remote https://huggingface.co/Qwen/Qwen3-8B refs/heads/main
git ls-remote https://huggingface.co/Qwen/Qwen3-14B-AWQ refs/heads/main
```

낮은 TTFT가 목표인 첫 RAG 실험은 Qwen3 non-thinking mode로 고정한다. thinking
mode는 별도 실험으로 분리해야 결과가 섞이지 않는다.

## 배포 프로파일

| 프로파일 | 물리 VRAM 등급 | 대표 장비 예시 | A6000 실험값 | 실제 executor 예산 | 주된 질문 |
|---|---:|---|---:|---:|---|
| Production Profile A | 24GB single GPU | RTX 4090 | `0.50` | 약 24.0GiB | 8B BF16과 14B 4-bit 중 무엇이 최적 가치인가? |
| Production Profile B | 32GB single GPU | RTX 5090 | `0.67` | 약 32.2GiB | 32B 4-bit가 동시 사용자 환경에서 실용적인가? |
| Reference Profile | 48GB single GPU | RTX A6000 | `0.92` | 약 44.2GiB | 단일 프로페셔널 GPU에서 얻는 품질 상한은 어디인가? |

`2×A6000`은 네 번째 배포 등급이 아니다. 더 큰 모델로 달성 가능한 품질 상한을
재는 **Quality Reference**이며, 비용 대비 품질 손실을 설명할 때만 사용한다.

`gpu_memory_utilization`은 vLLM model executor가 사용할 수 있는 GPU 메모리의
비율이다. 48GB A6000에서 위 설정은 프로파일별 메모리 적합성을 비교하기 위한
실험 장치다. 실제 24GB/32GB 카드의 연산 성능, 메모리 대역폭, 커널 동작을
재현하지는 않는다.

또한 `0.50/0.67`은 각각 24.0/32.2GiB의 **명목 class ceiling**을 주는 값이다.
대상 카드에서도 `0.92`를 사용할 계획이라면 실제 executor 예산은 24GB 카드에서
약 22.1GiB, 32GB 카드에서 약 29.4GiB다. 최종 후보는 A6000에서 각각 약
`0.46/0.61`로 한 번 더 실행하는 `deployment-matched` 검증을 통과시킨다.

## 반드시 분리하는 두 종류의 증거

1. **Memory-budget evidence**
   A6000을 `0.50` 또는 `0.67`로 제한해 모델 로딩, KV cache 여유, OOM 여부,
   동시성 한계를 확인한다. 이때 측정된 속도는 “A6000에서 관측된 성능”이다.
2. **Native-GPU performance evidence**
   최종 후보만 실제 4090/5090급 장비에서 다시 실행해 TTFT, 처리량, 전력과
   안정성을 확인한다. 이 결과만 대상 GPU의 성능 주장에 사용할 수 있다.

A6000은 Ampere, RTX 4090은 Ada Lovelace, RTX 5090은 Blackwell이다. 공식
사양상 메모리 대역폭도 각각 768, 1,008, 1,792GB/s로 다르다. 따라서 A6000의
tokens/s를 다른 카드의 수치처럼 옮겨 쓰지 않는다.

## 모델 실험 레인

정확한 모델 ID와 리비전은 실험을 시작하는 날 고정한다.

| 레인 | 정밀도 | 목적 |
|---|---|---|
| 7–9B | BF16 | 24GB에서도 양자화 없이 가능한 기준선 |
| 14B | BF16, INT8, AWQ/GPTQ 4-bit | 양자화가 24GB 적합성과 금융 QA 품질에 주는 영향 |
| 30–32B | 4-bit | 32GB 단일 GPU에서 한 단계 큰 모델의 실용성 |
| Large | 2×A6000 | 배포 후보가 아닌 Quality Reference |

단순 weight 크기는 `파라미터 수 × bit / 8`일 뿐이다. 실제 VRAM에는 weights
외에도 KV cache, activation, CUDA graph와 런타임 여유가 필요하다.

A6000 기반 24/32GB 예산 검증에서는 AWQ/GPTQ를 우선 사용한다. 현재 vLLM
호환표에서 AWQ, GPTQ, INT8 W8A8은 Ampere와 Ada를 지원하지만 일반 FP8 W8A8은
Ampere를 지원하지 않는다. FP8 후보는 Ada/Blackwell 장비에서 별도로 검증한다.

## 합격 조건

기본 업무 시나리오는 금융 내부문서 RAG, 동시 사용자 10명, 단일 GPU다.

- P95 TTFT(응답 첫 토큰): 2,000ms 이하
- 요청 오류율: 1% 이하
- OOM: 0회
- 품질 점수: 90/100 이상
- 동일 corpus, chunking, retriever, prompt, generation 설정 사용
- 후보별 최소 1회 warm-up 후 3회 반복 측정

품질 점수는 answer correctness 40%, groundedness 30%, citation accuracy 20%,
abstention/safety 10%의 고정 가중치로 계산한다. 실제 평가셋과 판정 rubrics는
버전으로 고정하고, 모델마다 바꾸지 않는다.

최종 선택 규칙은 단순하다.

1. 합격 조건을 통과한 구성만 남긴다.
2. 통과 구성 중 월간 총비용이 가장 낮은 것을 기본 권고안으로 선택한다.
3. 품질 우선안은 기본안 대비 품질 상승폭과 추가 비용을 함께 제시한다.

## 빠른 시작

환경 정보를 기록한다.

```bash
python3 scripts/capture_environment.py \
  --output work/environment.json
```

프로파일을 확인한다.

```bash
python3 scripts/finllm_profile.py list
python3 scripts/finllm_profile.py show profile-a
```

특정 모델의 vLLM 실행 명령을 만든다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model MODEL_ID \
  --revision COMMIT_SHA \
  --quantization awq \
  --max-model-len 8192 \
  --max-num-seqs 10
```

`class-ceiling`을 통과한 최종 후보는 보수적인 executor 예산으로 다시 확인한다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model MODEL_ID \
  --revision COMMIT_SHA \
  --quantization awq \
  --budget-mode deployment-matched
```

weight만을 기준으로 한 하한을 계산한다.

```bash
python3 scripts/finllm_profile.py estimate --params-billions 14 --bits 4
```

서버 실행 후 OpenAI-compatible endpoint에 부하를 건다.

```bash
python3 -m pip install -e .
python3 scripts/load_test.py \
  --base-url http://127.0.0.1:8000/v1 \
  --model SERVED_MODEL_NAME \
  --dataset datasets/smoke.jsonl \
  --concurrency 10 \
  --requests 30 \
  --output work/load-test.json
```

이 smoke 데이터는 배관 검증용이며 품질 평가용이 아니다. 실제 내부문서 대신
비식별·합성된 corpus와 별도 gold set을 사용한다.

## 결과 기록

각 정식 결과는 [`schemas/run-result.schema.json`](schemas/run-result.schema.json)을
따라 `results/`에 저장한다. 빈 기록 파일은 다음처럼 만든다.

```bash
python3 scripts/finllm_profile.py new-result \
  --profile profile-a \
  --model MODEL_ID \
  --revision COMMIT_SHA \
  --quantization awq \
  --evidence memory-budget-emulation \
  --output results/profile-a-14b-awq.json
```

측정값을 채운 다음 검증한다.

```bash
python3 scripts/finllm_profile.py validate-result \
  results/profile-a-14b-awq.json
```

정식 비교표에는 최소한 profile, model revision, quantization, quality, P95 TTFT,
aggregate throughput, peak VRAM, 오류율, OOM, evidence type을 표시한다.
가상 예시 숫자는 정식 결과 디렉터리에 넣지 않는다.

## 이 저장소를 본인 프로젝트로 만드는 법

다음 항목은 반드시 본인이 만들거나 선택해야 한다.

- **문제:** 은행 내부규정, 감사 작업문서, 계약 matter 중 하나만 먼저 선택
- **사용자:** 누가 어떤 문서를 볼 수 있는지 역할과 ACL 직접 설계
- **데이터:** 공개 문서와 직접 작성한 합성 내부문서로 corpus 구성
- **평가셋:** 정답 가능·답변 불가·권한 거부·prompt injection 문항 직접 작성
- **결정:** chunking, retriever, model, quantization의 선택 이유를 ADR로 기록
- **실패:** OOM, 느린 후보, 품질 회귀도 삭제하지 말고 원인과 함께 공개
- **결론:** 실제 측정 결과를 근거로 본인의 권고안을 작성

반대로 저장소의 설명 문구와 가상 숫자만 바꾸거나, 실행하지 않은 benchmark를
넣으면 본인 프로젝트가 되지 않는다. 면접에서 가장 가치 있는 부분은 코드를
외운 설명이 아니라 “이 선택을 했고, 이 실험에서 실패해서 이렇게 바꿨다”는
기록이다.

## 저장소 구조

```text
configs/profiles.json          세 배포 프로파일과 별도 품질 기준선
configs/model-candidates.json  첫 실험 후보와 revision 고정 상태
datasets/smoke.jsonl           서버 배관 검증용 합성 질문
decisions/                     본인이 내린 기술 결정과 근거
docs/experiment-protocol.md    재현 가능한 실험 순서와 보고 규칙
docs/on-prem-architecture.md   폐쇄망 배포·권한·감사·반출 통제 설계
docs/portfolio-roadmap.md      LLMOps 포트폴리오 완성 순서
docs/project-brief.md          본인 문제·사용자·범위를 고정하는 문서
docs/start-here.md             첫 결과를 만드는 실행 순서
schemas/run-result.schema.json 결과 계약
scripts/capture_environment.py GPU·software 환경 기록
scripts/finllm_profile.py      프로파일 조회, 명령 생성, 결과 검증
scripts/load_test.py           동시 요청/TTFT/처리량 측정
results/README.md              정식 결과 저장 규칙
```

## 공식 근거

- [vLLM serve: `--gpu-memory-utilization`](https://docs.vllm.ai/en/latest/cli/serve/)
- [vLLM quantization hardware compatibility](https://docs.vllm.ai/en/latest/features/quantization/)
- [NVIDIA RTX A6000 datasheet](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/proviz-print-nvidia-rtx-a6000-datasheet-us-nvidia-1454980-r9-web%20%281%29.pdf)
- [NVIDIA GeForce RTX 4090 specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/)
- [NVIDIA GeForce RTX 5090 specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)
- [Qwen3 official release and model family](https://qwenlm.github.io/blog/qwen3/)
- [Qwen3-8B model card](https://huggingface.co/Qwen/Qwen3-8B)
- [Qwen3-14B-AWQ model card](https://huggingface.co/Qwen/Qwen3-14B-AWQ)
- [Qwen3-32B-AWQ model card](https://huggingface.co/Qwen/Qwen3-32B-AWQ)
