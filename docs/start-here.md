# Start Here

목표는 10일 안에 거대한 플랫폼을 만드는 것이 아니다. 본인 데이터와 실제
측정값이 들어간 **Profile A 첫 의사결정**을 만드는 것이다.

## 완료 정의

첫 단계는 아래 문장을 실제 숫자로 말할 수 있을 때 끝난다.

> A6000에서 24GB class ceiling과 22GB deployment-matched budget으로
> 8B BF16과 14B AWQ를 비교했다. 동일한 평가셋과 동시성 10 조건에서
> `[선택 모델]`이 `[근거]` 때문에 Profile A 후보로 선정되었다.

## Day 1 — 범위를 본인 것으로 바꾸기

1. `docs/project-brief.md`의 `[OWNER]`를 모두 채운다.
2. 은행·회계·법무 중 하나만 첫 도메인으로 고른다.
3. 프로젝트 이름을 정한다.
4. 저장소를 Git으로 관리하고 첫 commit을 만든다.
5. 공개 저장소에 실제 고객·회사·사건 정보가 없음을 확인한다.

첫 도메인으로는 **은행 내부통제·준법감시 규정 RAG**를 권장한다. 공개된 공식
문서를 구하기 쉽고, 합성 내부 규정을 추가해 권한 격리도 시연할 수 있기 때문이다.

## Day 2 — 장비와 버전 고정

GPU 서버에서 실행한다.

```bash
python3 scripts/capture_environment.py --output work/environment.json
python3 scripts/finllm_profile.py list
```

후보 모델의 현재 commit SHA를 확인해 `configs/model-candidates.json`과
`docs/project-brief.md`에 기록한다.

```bash
git ls-remote https://huggingface.co/Qwen/Qwen3-8B refs/heads/main
git ls-remote https://huggingface.co/Qwen/Qwen3-14B-AWQ refs/heads/main
```

`main`이나 `latest`를 정식 결과의 revision으로 쓰지 않는다.

## Day 3 — 8B BF16 기준선

1. Profile A `class-ceiling` 명령을 생성한다.
2. 생성된 명령으로 vLLM을 실행한다.
3. `datasets/smoke.jsonl`로 동시성 1, 다음에 10을 시험한다.
4. peak VRAM과 서버 로그를 보존한다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model Qwen/Qwen3-8B \
  --revision PINNED_COMMIT_SHA \
  --max-model-len 8192 \
  --max-num-seqs 10
```

첫 실행에서는 RAG 응답 속도를 보기 위해 non-thinking mode를 유지한다.

## Day 4 — 14B AWQ 비교

8B와 정확히 같은 context length, prompt, request set, concurrency를 사용한다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model Qwen/Qwen3-14B-AWQ \
  --revision PINNED_COMMIT_SHA \
  --quantization awq \
  --max-model-len 8192 \
  --max-num-seqs 10
```

OOM이나 시작 실패도 결과다. 설정을 몰래 바꿔 성공시킨 뒤 같은 조건인 것처럼
비교하지 않는다.

## Day 5 — 보수적인 budget 재검증

`class-ceiling`을 통과한 모델을 실제 24GB 카드의 0.92 executor budget에
가까운 조건으로 다시 실행한다.

```bash
python3 scripts/finllm_profile.py serve-command \
  --profile profile-a \
  --model Qwen/Qwen3-14B-AWQ \
  --revision PINNED_COMMIT_SHA \
  --quantization awq \
  --budget-mode deployment-matched
```

이 검증을 통과해야 “24GB class 배포 후보”라고 표현한다.

## Day 6–7 — 본인 평가셋 만들기

최소 50–60문항:

| 유형 | 권장 개수 | 평가 내용 |
|---|---:|---|
| 근거가 있는 질문 | 25 | 정답과 인용 정확성 |
| 여러 문서 조합 | 10 | retrieval와 종합 능력 |
| 근거가 없는 질문 | 10 | 올바른 답변 유보 |
| 권한이 없는 문서 | 10 | ACL 우회 방지 |
| 악성 지시가 있는 문서 | 5 | prompt injection 대응 |

정답뿐 아니라 허용되는 근거 문서 ID, 금지 문서 ID, 답변 유보 여부를 기록한다.
자동 평가 결과 중 20% 이상을 직접 블라인드 검토한다.

## Day 8 — 결과와 실패 기록

각 반복을 별도 result JSON으로 남기고 validator를 통과시킨다.

```bash
python3 scripts/finllm_profile.py new-result \
  --profile profile-a \
  --model Qwen/Qwen3-14B-AWQ \
  --revision PINNED_COMMIT_SHA \
  --quantization awq \
  --evidence memory-budget-emulation \
  --output results/DATE-profile-a-qwen3-14b-awq-r1.json
```

성공한 결과만 남기지 않는다. OOM, 품질 미달, 긴 TTFT는 탈락 근거로 보존한다.

## Day 9 — 본인의 결정문 작성

`decisions/0001-profile-a-model.md`를 복사해 실제 값으로 채운다.

결정문에는 다음 질문에 답한다.

- 왜 이 모델 두 개를 골랐는가?
- 무엇을 동일하게 통제했는가?
- 어느 후보가 어떤 gate에서 탈락했는가?
- 품질 몇 점을 위해 지연시간·비용을 얼마나 추가로 쓸 가치가 있는가?
- A6000 결과로 주장할 수 없는 것은 무엇인가?

## Day 10 — 공개 가능한 첫 버전

- README의 “현재 상태”를 실제 진행 상태로 갱신
- 결과 비교표 추가
- 명령, 환경, revision, 평가셋 version 확인
- 실제 기밀정보와 token/secret이 없는지 검사
- CI 통과
- 3–5분 데모 영상 또는 화면 캡처

그다음에 Docker, Prometheus/Grafana, ACL, CI regression, Kubernetes 순서로
확장한다. Profile B와 32B는 Profile A의 평가 파이프라인을 재사용할 수 있을 때
시작한다.

