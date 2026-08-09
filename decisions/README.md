# Architecture Decision Records

이 디렉터리에는 프로젝트 소유자가 내린 중요한 결정을 기록한다.

현재 상태:

| ADR | 주제 | 상태 |
|---|---|---|
| `0001-profile-a-model.md` | 8B BF16과 14B AWQ 중 Profile A 선택 | **Superseded by 0004** |
| `0002-retrieval-design.md` | chunking, 어휘 검색, ACL 적용 시점 | Accepted |
| `0003-evaluation-scoring.md` | 규칙 기반 채점, LLM judge 배제 | Accepted |
| `0004-profile-a-model-revised.md` | CUDA graph 오진 정정, 14B AWQ + enforce-eager 선택 | Accepted |
| `0005-on-prem-packaging.md` | offline model/container 반입 방식 | 미작성 |
| `0006-observability.md` | metric, SLO, alert 선택 | 미작성 |

`0003`은 원래 계획에 없던 ADR이다. 채점 방식이 합격 조건 90점의 의미를 좌우하는데
어디에도 기록되어 있지 않아 추가했다.

`0001`은 삭제하지 않았다. 처리량 열세의 원인을 양자화로 오진했고 그 진단이
그럴듯했기 때문에 더 남길 가치가 있다. 무엇을 어떤 근거로 잘못 판단했는지가
결론만큼 중요하다.

ADR은 나중에 결론이 바뀌어도 삭제하지 않는다. `Superseded` 상태로 바꾸고 새
ADR을 연결한다. 이 변경 이력이 본인의 판단과 학습을 보여준다.

