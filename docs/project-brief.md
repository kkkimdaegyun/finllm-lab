# Project Brief

이 파일은 2026-08-08에 채워졌다. 여기 적힌 문제 정의, 사용자, 권한 모델,
합격 조건이 이후 모든 실험의 기준이 된다. 실험을 시작한 뒤 이 문서를 바꾸면
바꾼 이유와 시점을 ADR로 남긴다. CI가 채워지지 않은 항목이 남아 있는지 검사한다.

## 기본 정보

- 프로젝트 이름: FinLLM Lab
- 소유자/역할: kkkimdaegyun — 설계·구현·측정 전담
- 목표 직무: 온프레미스 GenAI/LLMOps 엔지니어
- 시작일: 2026-08-08
- 첫 공개 목표일: 2026-08-18

## 선택한 첫 시나리오

**은행 내부통제·준법감시팀용 Private RAG**

- 사용자: 내부통제·준법감시 담당자 10명
- 문제: 여러 규정과 업무 매뉴얼에서 근거 조항을 찾아 답변하는 시간이 길다.
- 제약: 외부 API로 원문 전송 금지, single GPU, P95 TTFT 2초
- 답변: 근거 문서와 조항을 인용하고, 근거가 없으면 답변을 유보한다.
- 데이터: 공개 금융 규정 구조를 참고해 **직접 작성한 합성 문서만** 사용한다.
- 제외: 실제 고객정보, 실제 내부문서, 투자·법률 판단 자동화

corpus에 실제 금융회사의 내부문서나 규정 원문을 복사하지 않는다. 모든 문서는
합성이며 각 파일의 `source_type` 필드에 그렇게 표시한다. 이는 실제 규정 원문의
저작권·정확성 문제를 피하면서 ACL과 인용 정확성을 그대로 시연하기 위한
선택이다.

## 사용자와 권한

| 역할 | 볼 수 있는 문서 | 볼 수 없는 문서 |
|---|---|---|
| `compliance-officer` (준법감시부) | 전행 공통규정 `POL-*`, 준법감시 지침 `CMP-*` | 감사 작업문서 `AUD-*`, 제보 사건파일 `WBL-*` |
| `internal-audit` (내부감사부) | 전행 공통규정 `POL-*`, 감사 작업문서 `AUD-*` | 준법감시 지침 `CMP-*`, 제보 사건파일 `WBL-*` |
| `whistleblow-admin` (제보 전담) | 전행 공통규정 `POL-*`, 제보 사건파일 `WBL-*` | 감사 작업문서 `AUD-*` |
| `branch-staff` (영업점 직원) | 전행 공통규정 `POL-*` | `CMP-*`, `AUD-*`, `WBL-*` 전부 |
| `vendor-contractor` (외부 위탁) | 공개 안내문 `PUB-*` | 그 외 전부 |

두 부서가 서로의 문서를 못 보게 한 것이 핵심이다. 준법감시와 내부감사는 둘 다
"본부 부서"이므로 직급 기반 권한으로는 분리되지 않고, 문서 소유 부서 기준의
ABAC filter가 있어야만 분리된다.

반드시 증명할 권한 테스트:

- `compliance-officer`는 `CMP-2026-004`(내부통제 점검주기)를 검색·인용할 수 있다.
- `branch-staff`가 같은 질문을 해도 `AUD-2026-002`(여신심사 감사 작업문서)는
  검색 결과에 나타나지 않고 인용되지도 않는다.
- `vendor-contractor`의 위탁계약이 종료되면 `PUB-*` 외 모든 문서에 대한 접근이
  사라지고, 종료 이전에 만든 응답 cache도 재사용되지 않는다.

## 성공 조건

- 동시 사용자: 10명
- P95 TTFT: 2,000ms 이하
- 오류율: 1% 이하
- OOM: 0회
- 품질: 90/100 이상
- 권한 위반 검색: 0건
- 근거 없는 질문의 올바른 답변 유보율: 90% 이상
- 모델 변경 rollback 목표시간: 15분

## 첫 모델과 데이터

- 8B baseline revision: `b968826d9c46dd6066d109eabc6255188de91218`
- 14B AWQ revision: `31c69efc29464b6bb0aee1398b5a7b50a99340c3`
- corpus version: `corpus-v0.1`
- evaluation set version: `eval-v0.1`
- prompt revision: `prompt-v0.1`

revision은 2026-08-08에 `git ls-remote ... refs/heads/main`으로 확인해 고정했다.
전체 후보 목록과 SHA는 [`configs/model-candidates.json`](../configs/model-candidates.json)에 있다.

## 내 프로젝트임을 보여주는 증거

- [x] 직접 만든 60개 평가 문항 (`datasets/eval-v0.1.jsonl`)
- [ ] 실제 A6000 측정 result JSON 3회분
- [ ] 통과한 후보와 탈락한 후보
- [ ] 최소 3개의 ADR
- [ ] 정상/과부하 상태 dashboard
- [x] 권한 우회와 prompt injection 실패 테스트 (`datasets/eval-v0.1.jsonl`의
      `unauthorized`, `injection` 유형)
- [ ] 모델 변경을 막은 CI 화면
- [ ] 실제 결론과 다음 개선안
