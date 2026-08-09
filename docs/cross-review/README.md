# 교차 구현·교차 검토 (Codex ↔ Claude)

## 목적

남은 구현을 두 모델이 절반씩 맡고, 완성 후 **서로의 결과물을 검토**한다.
목적은 "누가 더 잘했나"를 가리는 것이 아니라, 한 모델이 놓친 결함을 다른
모델이 잡아내 프로젝트를 단단하게 만드는 것이다.

## 분담

| | 담당 | 산출물 |
|---|---|---|
| **A파트** | Codex | 인덱싱과 검색: `scripts/rag_index.py` — corpus 파싱, chunking, ACL 필터, BM25 검색, CLI |
| **B파트** | Claude | 생성과 채점: `scripts/rag_eval.py` — prompt 구성, 생성 호출, 규칙 기반 품질 채점, 집계 |

두 파트는 [`docs/cross-review/interface-contract.md`](interface-contract.md)의
계약으로만 연결된다. 계약을 바꾸려면 상대 파트에 먼저 알린다.

- A파트 지시서: [`codex-task.md`](codex-task.md)
- B파트 지시서: [`claude-task.md`](claude-task.md)
- 검토 기준: [`review-rubric.md`](review-rubric.md)

## 순서

1. Claude가 A파트의 **계약 테스트**(`tests/test_retrieval_contract.py`)를 먼저
   작성한다. Codex는 이 테스트를 통과시키는 것을 목표로 구현한다.
2. Codex가 A파트를, Claude가 B파트를 동시에 구현한다.
3. 두 파트를 연결해 `scripts/rag_eval.py`가 끝까지 실행되는지 확인한다.
4. **교차 검토**: Codex가 B파트를, Claude가 A파트를 `review-rubric.md`에 따라
   검토하고 각각 `work/review-<reviewer>-<target>.json`을 남긴다.
5. 검토에서 나온 blocker와 major를 고친 뒤 재검토한다.
6. 남은 이견은 [`decisions/`](../../decisions)에 ADR로 기록한다.

## 실행 방법

```bash
codex exec "$(cat docs/cross-review/codex-task.md)"
```

검토 단계에서는 대상 파일과 rubric을 함께 준다.

```bash
codex exec "$(cat docs/cross-review/review-rubric.md)

검토 대상: scripts/rag_eval.py, tests/test_rag_eval.py
검토자 이름: codex"
```

## 진행 기록

### Round 1 — Claude가 A파트(`scripts/rag_index.py`) 검토

전체 결과: [`work/review-claude-rag_index.json`](../../work/review-claude-rag_index.json)

발견한 것:

- **`config_hash`가 ACL 변화에 반응하지 않는다** (major). 감사문서 권한을
  넓히자 같은 질의의 결과가 `POL-*`에서 `AUD-*`로 완전히 바뀌었는데 해시는
  그대로였다. 이 값은 모든 결과 JSON에 기록되는 재현성 식별자다.
- **`top_k=5`가 multi_doc 문항의 점수 상한을 만들고 있었다** (major).
  이 결함의 출처는 구현이 아니라 내가 쓴 계약의 기본값이어서 내 쪽에서 고쳤다.

두 지적이 만난 지점이 이 라운드에서 가장 쓸모 있었다. Codex는 노트에
"`section_title`을 랭킹에 넣으면 `config_hash` 입력에 반영되지 않아 제목 변경이
결과를 몰래 바꾼다"고 적고 랭킹 입력을 본문으로 좁혔다. 내 finding 1과 같은
문제를 반대편에서 짚은 것이다. 해시를 넓히면 그 제약이 사라지고, 측정해보니
회수율이 올라간다.

| 랭킹 입력 | top_k=8 기대인용 전부 회수 | multi_doc |
|---|---:|---:|
| 본문만 | 38/40 | 8/10 |
| 본문 + 조항 제목 | 40/40 | 10/10 |

한쪽만 봤으면 `config_hash`는 "사소한 provenance 결함"으로 남고 검색 품질 손실과
연결되지 않았을 것이다.

**검토자가 틀린 것도 기록한다.** 나는 `work/a-part-notes.md`가 없다고
지적했으나, 이는 Codex가 아직 실행 중일 때의 파일 시스템 상태를 최종 결과로
착각한 것이었다. 파일은 존재했고 요구사항을 충족했다. 지적을 철회하고 점수를
정정했다. 교차 검토에서는 **상대 작업의 완료 여부를 먼저 확인**해야 한다.

## 이 방식의 한계

두 검토자 모두 LLM이므로 **같은 맹점을 공유할 수 있다**. 특히 "그럴듯한
한국어 규정 텍스트"나 "합리적으로 보이는 점수 가중치"처럼 사실 확인이 필요한
부분은 둘 다 통과시킬 가능성이 있다.

따라서 교차 검토는 최종 게이트가 아니다. 실제 게이트는 다음 세 가지다.

1. `tests/`의 결정적 테스트 통과
2. `scripts/finllm_profile.py validate-result`의 schema·evidence 검증 통과
3. A6000에서 실제로 측정한 숫자

검토자가 "괜찮아 보인다"고 말한 것은 근거가 아니다. rubric이 모든 지적에
**재현 명령 또는 실패 시나리오**를 요구하는 이유가 이것이다.
