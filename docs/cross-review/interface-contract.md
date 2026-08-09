# 인터페이스 계약 (A파트 ↔ B파트)

이 문서가 두 파트를 연결하는 유일한 접점이다. 여기 적힌 이름, 인자, 반환값,
파일 경로는 상대 파트의 동의 없이 바꾸지 않는다.

## 데이터 구조

### Chunk

corpus 문서의 `## 제N조 (제목)` 절 하나가 chunk 하나다.

```python
{
    "chunk_id": "POL-2026-001#제3조",   # f"{doc_id}#{section}"
    "doc_id": "POL-2026-001",
    "doc_title": "자금세탁방지 업무규정",
    "section": "제3조",                  # 조 번호만. 괄호 제목은 제외
    "section_title": "고액현금거래보고",   # 괄호 안 제목. 없으면 ""
    "text": "동일인이 1거래일 동안 ...",  # 절 본문. heading 줄은 제외
    "owner_department": "준법감시부",
    "classification": "internal",        # public | internal | confidential
    "acl_roles": ["compliance-officer", "..."],
    "contains_injection": false,          # frontmatter에 없으면 false
    "corpus_version": "v0.1"
}
```

### Hit

```python
{
    "chunk": <Chunk>,
    "score": 12.34,   # float, 높을수록 관련성 높음
    "rank": 1          # 1부터 시작
}
```

## A파트가 제공하는 것 — `scripts/rag_index.py`

```python
def load_corpus(corpus_dir: Path) -> list[dict]:
    """corpus 디렉터리의 .md를 읽어 Chunk 목록을 반환한다."""

class Retriever:
    def __init__(self, chunks: list[dict]) -> None: ...

    @classmethod
    def from_index_file(cls, path: Path) -> "Retriever": ...

    def search(self, query: str, role: str, top_k: int = 5) -> list[dict]:
        """role이 볼 수 있는 chunk만 대상으로 검색해 Hit 목록을 반환한다."""

    def config_hash(self) -> str:
        """retriever 설정과 corpus 내용의 12자리 hex 해시.

        검색 결과를 바꾸는 입력이 바뀌면 해시도 바뀌어야 한다. 여기에는
        chunk의 text뿐 아니라 **acl_roles도 포함된다**. 권한 모델이 다르면
        같은 질의의 결과가 달라지므로, 같은 해시로 기록되면 안 된다.
        """

def save_index(chunks: list[dict], path: Path) -> None: ...
```

### CLI

```bash
python3 scripts/rag_index.py build --corpus corpus/v0.1 --output work/index-v0.1.json
python3 scripts/rag_index.py search --index work/index-v0.1.json \
  --role compliance-officer --query "고액현금거래 보고 기한" --top-k 5
python3 scripts/rag_index.py config-hash --index work/index-v0.1.json
```

`search`는 사람이 읽을 수 있는 표를 stdout에 출력한다. `config-hash`는 해시
한 줄만 출력한다(결과 JSON의 `rag.retriever_config_hash`에 그대로 들어간다).

## B파트가 제공하는 것 — `scripts/rag_eval.py`

```python
def build_messages(question: str, hits: list[dict], prompt_revision: str) -> list[dict]:
    """OpenAI chat 형식의 messages를 만든다."""

def score_case(case: dict, answer: str, hits: list[dict]) -> dict:
    """평가 문항 하나를 채점해 하위 점수와 판정 근거를 반환한다."""

def aggregate(case_scores: list[dict]) -> dict:
    """품질 점수와 하위 축 점수를 집계한다."""
```

### CLI

```bash
python3 scripts/rag_eval.py \
  --index work/index-v0.1.json \
  --dataset datasets/eval-v0.1.jsonl \
  --base-url http://127.0.0.1:8000/v1 \
  --model SERVED_MODEL_NAME \
  --output work/eval-<model>-r1.json
```

`--frozen-retrieval PATH`를 주면 저장된 retrieval 결과를 재사용해
generation-only 평가를 한다(experiment-protocol Stage C).

## 불변 규칙

이 규칙을 어기면 어느 쪽이든 blocker다.

1. **ACL은 검색 이전에 적용한다.** `search()`는 `role`이 `acl_roles`에 없는
   chunk를 어떤 점수로도 반환하지 않는다. 모델에게 권한 판단을 맡기지 않는다.
2. **인용 단위는 `chunk_id`다.** 생성 프롬프트에 노출하는 근거 식별자와 채점에
   쓰는 식별자가 같아야 한다.
3. **injection 문서를 인덱스에서 빼지 않는다.** `contains_injection: true`
   문서도 정상 검색 대상이다. 방어는 프롬프트와 채점에서 한다.
4. **새 pip 의존성을 추가하지 않는다.** 표준 라이브러리와 이미 선언된
   `httpx`, `jsonschema`만 쓴다. 폐쇄망 설치 전제이기 때문이다.
5. **결정적으로 동작한다.** 같은 입력에 같은 출력. 검색 점수 동점일 때는
   `chunk_id` 사전순으로 정렬한다.
