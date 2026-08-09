# LLMOps 이직 포트폴리오 로드맵

## 판단

이 프로젝트의 출발점은 좋다. “큰 모델을 띄웠다”가 아니라 업무 SLO, 품질,
GPU 예산, 양자화, 비용을 함께 놓고 배포 결정을 내리기 때문이다.

하지만 현재 구현 범위는 **Inference Engineering + Evaluation의 기반**이다.
이를 LLMOps 포트폴리오로 완성하려면 반복 가능한 배포, 관측, 변경 통제,
장애 대응의 증거가 더 필요하다.

## 현재 보여주는 역량

| 역량 | 현재 증거 | 상태 |
|---|---|---|
| GPU capacity planning | 24/32/48GB executor budget | 있음 |
| Inference serving | 재현 가능한 vLLM 명령 생성 | 기반 있음 |
| Quantization 판단 | Ampere/Ada 호환성 구분 | 있음 |
| 성능 평가 | 동시성 10, streaming TTFT harness | 기반 있음 |
| 품질 평가 | 고정 평가축·가중치·gate 정의 | 설계 있음 |
| 증거의 정직성 | memory emulation과 native benchmark 분리 | 강점 |
| 모델/데이터 버전 관리 | immutable revision/result schema | 기반 있음 |
| 컨테이너 배포 | 아직 없음 | 필요 |
| CI/CD | 정적 검증 CI만 있음 | 확장 필요 |
| 관측성·알림 | 아직 없음 | 필요 |
| 장애 대응·롤백 | 아직 없음 | 필요 |
| 보안·감사 | 아직 없음 | 필요 |

## 권장 구현 순서

### Milestone 1 — 측정 가능한 단일 노드 서비스

- 실제 후보 모델 2개와 immutable revision 고정
- 합성 금융 corpus, gold QA, retrieval 결과 고정
- 품질 평가기와 부하 시험을 한 명령으로 실행
- GPU utilization, peak VRAM, TTFT, inter-token latency, queue time 수집
- 세 번 반복한 실제 결과와 실패 결과도 함께 공개

완료 증거:

- Profile A와 B의 실제 result JSON
- 자동 생성된 비교표
- 어떤 후보가 왜 탈락했는지 적은 ADR

### Milestone 2 — 운영 가능한 패키징

- 버전이 고정된 GPU container image
- health/startup/readiness endpoint
- 설정·secret 분리
- 정상 종료와 in-flight request drain
- 한 명령으로 시작하는 로컬 단일 GPU 배포

완료 증거:

- 깨끗한 호스트에서 재현한 설치 로그
- cold start 시간과 model download/cache 정책
- 이미지 SBOM과 취약점 스캔 결과

### Milestone 3 — 관측성과 SLO

- Prometheus metrics와 Grafana dashboard
- 요청 수, 오류율, P50/P95 TTFT, E2E, output tok/s
- GPU memory/utilization/power, vLLM queue와 KV cache 지표
- retrieval latency, no-hit rate, citation/groundedness 표본 평가
- SLO burn-rate 또는 최소한 P95·오류율 alert

완료 증거:

- 정상/과부하/OOM 직전 세 장면의 dashboard
- alert가 발생하고 원인을 찾은 짧은 incident report

### Milestone 4 — 변경 안전성

- PR마다 smoke evaluation과 schema validation
- 모델·prompt·retriever 변경 시 regression gate
- image build와 staging 배포
- 실패 시 이전 모델/설정으로 rollback
- 결과와 artifact의 provenance 기록

완료 증거:

- 일부러 품질을 떨어뜨린 변경이 CI에서 차단되는 화면
- canary 또는 blue/green 전환과 rollback 기록

### Milestone 5 — 금융/온프레미스 운영성

- 문서 접근권한이 retrieval 결과까지 전파되는지 테스트
- PII 마스킹, audit log, encryption, secret rotation
- prompt injection과 data exfiltration 위협 모델
- 모델·문서·응답 보존 정책
- 외부 API 없이 동작하는 폐쇄망 설치 절차

완료 증거:

- threat model과 abuse test
- 사용자 A가 사용자 B의 문서를 검색하지 못하는 자동 테스트
- 누가 어떤 문서 버전으로 어떤 답을 받았는지 추적 가능한 audit record

## 면접에서의 한 문장

> 금융 RAG의 품질·지연시간 SLO를 먼저 정의하고, 24/32/48GB 단일 GPU
> 프로파일별로 모델과 양자화를 평가했습니다. A6000 메모리 제한 실험과 대상
> GPU 실측을 분리했으며, 평가 regression gate와 운영 지표를 통해 가장 저렴한
> 합격 구성을 선택했습니다.

## 피해야 할 범위 확장

- 첫 버전부터 거대한 multi-cluster 플랫폼 만들기
- 모델을 많이 나열하고 평가셋은 약하게 만들기
- LangChain, Kubernetes, MLflow 같은 도구 이름만 늘리기
- 실제로 재현하지 않은 4090/5090 처리량 쓰기
- 평균 latency만 제시하고 TTFT 분포와 오류를 숨기기

포트폴리오의 중심은 도구 개수가 아니라 **요구사항 → 측정 → 선택 → 배포 →
관측 → 안전한 변경**의 닫힌 고리다.

