# 교차 검토 지시서 (LLM as a judge)

너는 다른 모델이 구현한 코드를 검토한다. 목표는 점수를 매기는 것이 아니라
**아직 아무도 발견하지 못한 결함을 찾는 것**이다.

## 검토 전에 읽을 것

- `docs/cross-review/interface-contract.md` — 판정 기준이 되는 계약
- 상대 파트의 지시서 (`codex-task.md` 또는 `claude-task.md`)
- 상대가 남긴 `work/a-part-notes.md` 또는 `work/b-part-notes.md`
- `docs/experiment-protocol.md` — 이 프로젝트가 지키려는 증거 규칙

## 지적의 요건

**모든 지적은 재현 가능해야 한다.** 다음 중 하나가 없는 지적은 제출하지 마라.

- 실제로 실행해서 실패를 확인한 명령과 그 출력
- 구체적 입력 → 잘못된 출력을 보여주는 실패 시나리오 (파일:줄 번호 포함)

금지되는 지적:

- "가독성이 떨어진다", "더 pythonic하게 쓸 수 있다" 같은 취향 문제
- "성능이 느릴 수 있다" — 측정하지 않았다면 쓰지 마라
- "테스트를 더 추가하면 좋겠다" — 어떤 케이스가 왜 빠졌는지 특정하지 않으면 무효
- 계약에 없는 요구를 새로 만들어 위반이라고 주장하는 것

지적할 것이 없으면 findings를 빈 배열로 두고 그렇게 말하라. 억지로 채우지 마라.

## 반드시 확인할 것

두 파트 공통:

1. 계약 위반 — 함수 이름, 인자, 반환 구조, CLI 인자, 출력 형식
2. `interface-contract.md`의 불변 규칙 5개 위반
3. 새 pip 의존성이 들어갔는지 (`pyproject.toml` diff 확인)
4. 결정성 — 같은 입력을 두 번 실행해 같은 출력이 나오는지 직접 실행해 확인
5. 기존 테스트를 깨뜨렸는지 (`python3 -m unittest discover -s tests`)

A파트(검색)를 검토할 때 특히:

- `role`이 볼 수 없는 chunk가 어떤 경로로든 반환될 수 있는가?
  `datasets/eval-v0.1.jsonl`의 `unauthorized` 문항 10개를 모두 실행해
  `forbidden_doc_ids`가 결과에 나오는지 직접 확인하라.
- 빈 결과, 존재하지 않는 role, 빈 질의에서 예외가 나는가?
- `config_hash`가 corpus를 한 글자 바꿨을 때 실제로 바뀌는가?

B파트(채점)를 검토할 때 특히:

- 채점 규칙이 **틀린 답에 점수를 주는** 경우를 찾아라. 예를 들어
  `required_facts`의 문자열이 질문 자체에 포함되어 있으면, 질문을 그대로
  되풀이한 답변이 만점을 받는다.
- 유보 판정 정규식이 "근거가 없지는 않습니다" 같은 문장을 유보로 오판하는가?
- 권한 위반 카운트가 실제로 전체 실패로 이어지는가?
- 자기 자신을 judge로 쓰는 코드 경로가 남아 있는가?

## 출력 형식

`work/review-<reviewer>-<target>.json`에 아래 JSON만 저장한다.

```json
{
  "reviewer": "codex",
  "target": "scripts/rag_eval.py",
  "reviewed_at_utc": "2026-08-08T00:00:00Z",
  "commands_run": ["python3 -m unittest discover -s tests"],
  "findings": [
    {
      "severity": "blocker",
      "category": "contract-violation",
      "file": "scripts/rag_eval.py",
      "line": 42,
      "claim": "한 문장으로 결함을 진술",
      "reproduction": "실행한 명령 또는 구체적 입력",
      "observed": "실제로 나온 잘못된 결과",
      "expected": "계약상 나와야 하는 결과",
      "suggested_fix": "고치는 방법"
    }
  ],
  "verdict": "accept | accept-with-changes | reject",
  "scores": {
    "contract_compliance": 5,
    "correctness": 4,
    "honesty": 5,
    "test_quality": 3,
    "simplicity": 4
  },
  "what_i_could_not_verify": ["GPU가 점유되어 실측은 확인하지 못함"]
}
```

`severity`는 `blocker`(계약 위반·잘못된 결과·권한 누출), `major`(특정 조건에서
틀림), `minor`(문서·에러 메시지) 중 하나다.

`scores`는 0–5. `honesty`는 "측정하지 않은 것을 측정한 것처럼 쓰지 않았는가"를
본다. 이 프로젝트에서 가장 중요한 축이다.

`what_i_could_not_verify`를 비워두지 마라. 확인하지 못한 것을 확인한 척하는
것이 이 검토에서 가장 큰 실패다.
