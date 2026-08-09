# ADR-0007: 변경 안전성 — regression gate와 release/rollback 단위

- 상태: Accepted
- 날짜: 2026-08-09
- 작성자: kkkimdaegyun (B파트 구현: Claude)
- 관련: [ADR-0003](0003-evaluation-scoring.md), [ADR-0004](0004-profile-a-model-revised.md), [ADR-0006](0006-observability.md)

## 맥락

v0.2의 질문 중 두 개가 이 ADR의 범위다. "잘못된 변경을 차단할 수 있는가",
"장애 시 안전하게 이전 상태로 돌아갈 수 있는가."

v0.1은 이미 필요한 재료를 거의 다 갖고 있었다.

| 재료 | v0.1 자산 |
|---|---|
| 평가셋 | `datasets/eval-v0.1.jsonl` 60문항 |
| 채점기 | `scripts/rag_eval.py` 규칙 기반 4축 |
| 결과 계약 | `schemas/run-result.schema.json` + `validate-result` |
| 합격 기준 | `configs/profiles.json` `benchmark_policy` |
| 결정적 테스트 | 84개 |

**따라서 새 평가 체계를 만들지 않는다.** gate는 위 재료를 호출하는 얇은 층이다.

## 결정 1 — gate는 기존 threshold를 재정의하지 않는다

`quality_score_min = 90`은 v0.1이 실험 전에 고정한 값이다. gate는 이 값을
`configs/profiles.json`에서 읽는다. 코드에 복사하지 않는다.

같은 규칙을 alert에도 적용했다. Prometheus rule 파일은 실행 시점에 JSON을
읽을 수 없으므로, 대신 gate의 `alert-threshold-consistency` 단계가 두 값이
같은지 강제한다. SLO 숫자가 두 벌 존재하면 반드시 갈라진다.

## 결정 2 — hard gate와 regression gate를 분리한다

둘은 다른 질문에 답한다.

| | 질문 | 기준 | 출처 |
|---|---|---|---|
| hard gate | 서비스해도 되는가 | quality ≥ 90 | `benchmark_policy` (실험 전 고정) |
| regression gate | 어제보다 나빠졌는가 | quality ≥ baseline − 허용편차 | `ops/baselines/…` (실측) |

90점은 넘지만 97.667 → 95.0으로 떨어진 변경은 **회귀다.** hard gate만
있으면 이런 변경이 통과하고, 품질은 90점까지 조용히 미끄러진다.

## 결정 3 — 허용편차를 추정하지 않고 측정했다

v0.1의 결과 레코드 3개는 quality가 전부 97.667로 동일하다. 그러나 이것은
분산이 0이라는 증거가 아니다 — `run_profile_a.sh`가 평가를 **한 번만 돌리고
그 값을 r1/r2/r3에 복사**했기 때문이다. 즉 v0.1에서 품질 분산은
`NOT_MEASURED`였다.

그래서 측정했다. 같은 호스트·같은 모델·frozen retrieval로 `rag_eval.py`를
3회 반복:

| | r1 | r2 | r3 | spread |
|---|---:|---:|---:|---:|
| quality | 97.667 | 97.667 | 97.667 | **0.000** |
| injection 성공 | 2 | 2 | 2 | 0 |
| ACL 위반 | 0 | 0 | 0 | 0 |

증거: `work/v02/eval-variance-r{1,2,3}.json`.

따라서 `quality_regression_tolerance = 0.0`으로 정했다. 이것은 관례값이
아니라 이 측정에서 나온 값이다.

**n=3은 작은 표본이다.** 다른 동시성·다른 배치 타이밍에서도 0이라는 뜻은
아니다. 이 값이 실제로 흔들리면 허용치를 임의로 늘리지 말고 반복 횟수를
늘려 재측정한다. 참고로 60문항 중 하나가 완전히 뒤집히면 quality는 1.667점
움직이므로, 편차 0은 "한 문항도 뒤집히지 않는다"는 뜻이다.

## 결정 4 — CPU 단계와 GPU 단계를 분리한다

`--stage cpu`는 GPU도 서비스도 모델도 필요 없다. GPU 없는 CI runner에서
GPU 단계는 **`skipped`로 보고되며 절대 `pass`로 세지 않는다.** CI가
리포트를 직접 검사해 이를 강제한다.

CPU 단계에 **권한 검사(`retrieval-acl`)를 넣은 것이 의도적**이다. ACL은
retrieval 이전 데이터 층에서 강제되므로 모델 없이 검증할 수 있다. 이 검사가
GPU 없이 도는다는 사실 자체가 "권한을 모델에게 맡기지 않았다"는 주장의
증거다. 실제로 프롬프트를 망가뜨렸을 때 quality는 97.667 → 73.733으로
무너졌지만 **ACL 위반은 0건을 유지했다.**

## 결정 5 — rollback 단위는 파일이 아니라 release다

모델 revision, tokenizer revision, prompt revision, corpus/eval 버전,
retriever 해시, vLLM 실행 인자가 한 덩어리로 움직인다
(`schemas/release-manifest.schema.json`).

하나만 되돌리면 측정된 적 없는 조합이 되고, 그 조합의 품질은 아무도 모른다.
ADR-0004가 보여준 대로 이 프로젝트에서는 인자 하나(`--enforce-eager`)가
결론을 뒤집는다.

강제하는 것 두 가지:

1. **gate를 통과하지 않은 release는 promote할 수 없다.** 장애 대응 중이라면
   `--allow-failed-gate`를 쓸 수 있고, 그 사실이 로그에 남는다.
2. **모든 rollback은 이유와 함께 append-only 로그에 기록된다.**

## 검증

| 검증 | 방법 | 결과 |
|---|---|---|
| 정상 저장소에서 통과 | `--stage cpu` / `--stage gpu` | exit 0 |
| 프롬프트 회귀 차단 | 인용 규칙 제거 후 `--stage gpu` | **exit 1**, quality 73.733 |
| gate 미통과 release promote 거부 | `promote --manifest <fail>` | **exit 1** |
| GPU 단계가 CPU에서 pass로 안 세짐 | 리포트 검사 | skipped 확인 |
| 실제 rollback | `rollback --to … --exec` | `ops/evidence/rollback-demo/` |

## 이 설계가 잡지 못하는 것

**regression gate는 VRAM 회귀를 잡지 못한다.** gate는 평가셋 결과(품질·ACL·
injection)만 본다. peak VRAM은 부하시험에서 나오는 값이라 gate 경로에 없다.

이것은 rollback 시연에서 드러났다. `--enforce-eager`를 뺀 구성은 **품질
gate를 통과하지만** peak VRAM이 24GB class를 넘어 Profile A의 전제를 잃는다.
그 변경을 잡은 것은 gate가 아니라 `FinLLMGPUMemoryAboveProfileClass` alert였다.

이 분업 자체는 합리적이다 — gate는 배포 전, alert는 배포 후를 본다.
다만 **"gate를 통과했으니 안전하다"고 말할 수 없다**는 뜻이고, 이 한계를
명시해 둔다.

→ Follow-up: gate에 부하시험 기반 VRAM 단계를 추가할지 검토.
   추가한다면 GPU 단계에만 넣고, 3회 반복 규칙과 `gpu_watch.py`를 재사용한다.

## 포기한 것과 위험

- **LLM judge를 쓰지 않는다.** ADR-0003의 결정을 그대로 따른다. 평가 대상
  모델이 평가자가 되면 측정 대상이 측정에 섞인다.
- **canary / blue-green을 하지 않는다.** 단일 GPU 노드에 두 버전을 동시에
  띄울 메모리가 없다. rollback은 재기동 방식이고, 그동안 서비스는 중단된다.
  `docs/on-prem-architecture.md`가 이미 "단일 GPU는 HA를 제공하지 않는다"고
  분리해 둔 것과 일관된다.
- **gate가 60문항 합성 평가셋에 의존한다.** 이 평가셋이 놓치는 회귀는 gate도
  놓친다. injection 방어가 2/5로 뚫린 상태를 baseline으로 고정한 것도
  같은 성격의 한계다 — gate는 그것을 고치지 않고 악화만 막는다.
