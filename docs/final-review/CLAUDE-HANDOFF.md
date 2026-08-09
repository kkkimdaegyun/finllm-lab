# Claude handoff — FinLLM Lab v0.2 final artifacts

## 1. 최종 프로젝트 요약과 verdict

v0.1의 evaluation/measurement stack을 pinned serving, observability, fail-closed gate,
incident, immutable rollback으로 확장했다. canonical repository의 actual RTX A6000 GPU 1
통합 rehearsal을 기준으로 최종 판정은 **PASS · single-A6000 v0.2 scope**다.

제품 판정과 artifact build 판정은 별개다. 제품 근거는
`docs/final-review/final-release-review.json`, 실행 증거는
`ops/evidence/final-rehearsal/`에 있다.

## 2. 아직 논쟁 가능한 판단

- PASS는 single-A6000 Compose reference 범위다. production traffic, HA, actual 24GB
  native fit까지 확대하면 안 된다.
- AWQ diagnosis는 graph-enabled path와의 연관까지만 사실이다. kernel root cause는
  `NOT_MEASURED`다.
- alert `for:`와 rate window는 실제 장기 traffic 근거가 없어
  `PENDING_THRESHOLD_VALIDATION`이다.
- injection success 1/5는 회귀되지 않았지만 해결된 문제가 아니다.

## 3. 수정 가능한 source와 build

| Artifact | Source | Build command |
|---|---|---|
| PDF | `docs/final-report/final-report.md` | `python3 scripts/build_final_report.py` |
| HTML/PPTX | `portfolio/slide-data.json`, `index.html`, `styles.css` | `python3 scripts/build_portfolio.py` |
| validation | 위 모든 source | `python3 scripts/validate_final_artifacts.py` |

PPTX의 text, table, shape는 python-pptx object다. HTML screenshot은 preview용이며
PPTX slide background로 사용하지 않는다.

## 4. slide-data 구조

각 slide는 `number`, `title`, `eyebrow`, `layout`, `content`, `footer`, `evidence`를
가진다. layout별 content는 cards/table/flow/timeline/steps 등 구조화된 값이다. 문장,
순서, 표 값은 JSON에서 수정해야 HTML과 PPTX가 같은 source를 유지한다.

## 5. Actual benchmark/evidence source

- v0.1 성능: `results/2026-08-08c-*.json`
- schema/policy: `schemas/run-result.schema.json`, `configs/profiles.json`
- v0.2 gate: `ops/evidence/final-rehearsal/gate-all.json`
- actual service/metrics/GPU: `ops/evidence/final-rehearsal/`
- incident: `ops/incidents/INC-003-api-outage-container-rollback.md`
- release: `ops/release/history/2026-08-09-v02-container-good.json`

## 6. LOCKED_FACT

- Release source Git SHA: `95dd24deba5669919e12b8535dbaf3128646ae5e`.
- API image: `sha256:16986083cba5b7c775ad40cf0cee17dd6680fe5b00b03d3be0a7c14e99270feb`.
- Model/tokenizer revision: `31c69efc29464b6bb0aee1398b5a7b50a99340c3`.
- Actual A6000 all-stage gate: 153 tests, 27 results, 11/11 stages, quality 98.333,
  ACL 0, injection success 1.
- Actual drain: 20/20 client bodies complete, failed 0.
- Actual alert detection: 43.765s from SIGTERM to alert activeAt.
- Successful rollback command: 6.332s and post-readiness/build-info verify PASS.
- v0.1 14B AWQ 0.46 eager n=3 mean: quality 97.667, server P95 129.995ms,
  user P95 1,273.402ms, output 315.331 tok/s, peak 21.961GiB.
- 모든 v0.1 24GB-class 수치는 A6000 `memory-budget-emulation`이다.
- actual 24GB native validation과 remote GPU CI run은 `NOT_EXECUTED`다.

LOCKED_FACT를 변경하려면 source JSON/command output과 새 독립 재현을 먼저 추가한다.

## 7. EDITABLE_NARRATIVE

- slide title, 한 줄 요약, 설명 순서
- 같은 사실을 전달하는 table/flow 시각 구조
- 면접용 문장의 톤과 길이
- 색상, spacing, typography

표현을 수정해도 evidence scope를 확대하거나 `NOT_EXECUTED`를 지울 수 없다.

## 8. PENDING_VALIDATION / limitations

- actual 24GB native GPU
- production corpus/traffic
- long-duration alert window calibration
- remote self-hosted A6000 GitHub CI execution
- high availability / zero-downtime model swap

수정 후 PDF, HTML, PPTX를 모두 rebuild하고 validator와 test suite를 다시 실행한다.
