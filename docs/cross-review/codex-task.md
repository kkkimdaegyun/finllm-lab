# A파트 지시서 (Codex)

너는 FinLLM Lab 저장소의 **인덱싱·검색 파트**를 구현한다. 작업 디렉터리는
`/home/dgkim/dgkim/new_project`이고, 저장소는 이미 존재한다.

## 먼저 읽을 것

1. `docs/cross-review/interface-contract.md` — 지켜야 할 계약. 이름과 반환값을
   임의로 바꾸지 않는다.
2. `corpus/README.md` — 문서 형식과 ACL 규칙
3. `corpus/v0.1/POL-2026-001.md` 한 개 — frontmatter 실제 형태 확인
4. `tests/test_retrieval_contract.py` — **이 테스트를 통과시키는 것이 완료 조건이다**
5. `docs/experiment-protocol.md` 2절 Stage C — 왜 retrieval을 동결해야 하는지

## 만들 것

`scripts/rag_index.py` 하나. 계약에 정의된 `load_corpus`, `Retriever`,
`save_index`와 `build` / `search` / `config-hash` CLI를 구현한다.

### 구현 사양

**frontmatter 파싱**: 파일은 `---\n{JSON}\n---\n본문` 형태다. `pyyaml`을 쓰지
말고 `json.loads`로 읽는다. `contains_injection`이 없으면 `False`로 채운다.

**chunking**: `## 제N조 (제목)` 줄을 경계로 자른다. `section`은 `"제3조"`,
`section_title`은 괄호 안 문자열, `text`는 heading 다음 줄부터 다음 heading
직전까지의 본문(앞뒤 공백 제거)이다. 표(`|`)와 목록도 본문에 그대로 포함한다.

**토크나이저**: 한국어라 공백 분리만으로는 부족하다. 다음을 결정적으로 조합한다.

- 한글·영숫자 연속을 단어 토큰으로 추출 (정규식 `[0-9A-Za-z가-힣]+`)
- 길이 2 이상인 한글 단어는 문자 bigram으로도 분해해 추가
  (`"보고기한"` → `보고`, `보고기`가 아니라 `보고`, `고기`, `기한`)
- 영문은 소문자로 정규화

**랭킹**: BM25, `k1=1.2`, `b=0.75`. 문서 길이는 토큰 수 기준. 점수가 같으면
`chunk_id` 사전순으로 정렬한다.

**ACL**: `role not in chunk["acl_roles"]`인 chunk는 **점수 계산 전에 제외**한다.
필터링 후 상위 `top_k`를 반환한다. 필터 결과가 비면 빈 리스트를 반환한다.

**config_hash**: retriever 설정(`k1`, `b`, `top_k` 기본값, 토크나이저 버전
문자열)과 전체 chunk의 `chunk_id`+`text`를 정렬해 이어붙인 뒤 SHA-256의 앞
12자리 hex를 반환한다. 같은 corpus·같은 설정이면 항상 같은 값이어야 한다.

**인덱스 파일**: `save_index`는 `{"corpus_version": ..., "built_at_utc": ...,
"chunks": [...]}` 형태의 JSON을 쓴다. `Retriever.from_index_file`이 이를 읽는다.

### 금지 사항

- 새 pip 의존성 추가 금지 (표준 라이브러리 + `httpx`, `jsonschema`만)
- `contains_injection: true` 문서를 인덱스에서 제외하지 말 것
- ACL 필터를 검색 후에 적용하지 말 것
- `scripts/rag_eval.py`, `scripts/finllm_profile.py`, `scripts/load_test.py`,
  `tests/test_profiles.py`, `tests/test_result_contract.py`,
  `tests/test_retrieval_contract.py`, `corpus/`, `datasets/` 수정 금지
  (B파트와 계약 테스트의 영역이다)

## 완료 조건

```bash
cd /home/dgkim/dgkim/new_project
python3 -m unittest discover -s tests -v
python3 scripts/rag_index.py build --corpus corpus/v0.1 --output work/index-v0.1.json
python3 scripts/rag_index.py search --index work/index-v0.1.json \
  --role branch-staff --query "고액현금거래 보고 기한" --top-k 3
python3 scripts/rag_index.py config-hash --index work/index-v0.1.json
```

전부 성공하고, 기존 테스트가 하나도 깨지지 않아야 한다.

## 추가로 남길 것

`work/a-part-notes.md`에 다음을 적는다. 교차 검토에서 이 문서를 근거로 쓴다.

- 토크나이저와 BM25 파라미터를 그렇게 정한 이유
- 직접 확인한 검색 실패 사례 1개 이상 (어떤 질문이 왜 원하는 조항을 못 찾았는지)
- 계약에서 애매했던 부분과 어떻게 해석했는지

실패 사례를 "없음"으로 적지 마라. 60문항 중 검색이 빗나가는 문항은 반드시
있고, 그것을 찾아 적는 것이 이 파트의 핵심 산출물이다.
