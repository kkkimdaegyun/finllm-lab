# Claude handoff — FinLLM Lab v0.2 final artifacts

## 1. 최종 프로젝트 요약

v0.1의 평가·측정 stack을 serving과 operational safety로 확장했다. A serving/deployment와 B observability/reliability에는 각각 검증 가능한 구현이 있으나, 서로 다른 directory와 contract에 남아 한 release로 통합되지 않았다. final report와 portfolio는 이 상태를 성공으로 포장하지 않고, 측정·오진 수정·negative-path review를 핵심 기술 사례로 구성한다.

## 2. Final Judge verdict

`FAIL / DO_NOT_RELEASE`

근거는 `docs/final-review/final-release-review.json`에 구조화돼 있다. 핵심 blocker는 A/B 미통합, A metric과 B PromQL 불일치, stale evaluation fail-open, zero-stage pass, rollback state mutation이다.

## 3. 논쟁 가능한 판단

- Gate 5 Incident를 pass로 두되 caveat를 붙였다. 실제 host-vLLM incident/alert/rollback evidence가 있기 때문이다. 이것을 integrated release incident pass로 확대하면 안 된다.
- A6000 0.46 AWQ를 “다음 native 검증 후보”로 설명했다. “24GB 배포 적합”으로 바꾸면 evidence boundary를 넘는다.
- AWQ diagnosis는 graph-enabled path와의 연관까지만 locked fact다. 정확한 kernel root cause는 `NOT_MEASURED`다.

## 4. PDF source

- `docs/final-report/final-report.md`
- `docs/final-report/BUILD.md`
- builder: `scripts/build_final_report.py`

## 5. PPT/HTML source

- shared content: `portfolio/slide-data.json`
- HTML shell: `portfolio/index.html`
- style: `portfolio/styles.css`
- PPTX/preview builder: `scripts/build_portfolio.py`

## 6. slide-data 구조

각 slide는 `id`, `number`, `title`, `eyebrow`, `layout`, `content`, `footer`, `evidence`를 가진다. `content`에는 layout에 따라 `headline`, `bullets`, `stats`, `table`, `flow`, `timeline`, `columns`, `tags`가 들어간다. HTML과 PPTX는 같은 JSON을 읽는다. 문장/순서/표 숫자는 JSON에서만 바꾸는 것이 원칙이다.

## 7. PPTX 생성

```bash
python3 scripts/build_portfolio.py
```

텍스트·표·도형은 PowerPoint object다. HTML screenshot을 PPTX slide background로 쓰지 않는다.

## 8. PDF 생성

```bash
python3 scripts/build_final_report.py
```

## 9. Actual benchmark sources

- `results/2026-08-08c-profile-a-qwen3-8b-bf16-classceiling-eager-r{1,2,3}.json`
- `results/2026-08-08c-profile-a-qwen3-14b-awq-classceiling-eager-r{1,2,3}.json`
- `results/2026-08-08c-profile-a-qwen3-14b-awq-deploymentmatched-eager-r{1,2,3}.json`
- schema: `schemas/run-result.schema.json`
- policy: `configs/profiles.json`

## 10. LOCKED_FACT

- Canonical B Git commit reviewed: `47cbc5a01320fb203a537392c7b209834225e05a`.
- A는 `/home/dgkim/dgkim/FinLLM:0.2`, B canonical Git은 `/home/dgkim/dgkim/FinLLM-0.2`에 있고 통합되지 않았다.
- A audit run: 111 tests, 3 opt-in native GPU tests skipped.
- B audit run: 119 tests pass.
- 각 tree의 27 result JSON이 schema validator를 통과했다.
- 모든 2026-08-08c performance result는 A6000 `memory-budget-emulation`이다.
- 14B AWQ 0.46 eager n=3 평균: quality 97.667, server P95 TTFT 129.995ms, user P95 TTFT 1273.402ms, output 315.331 tok/s, peak 21.961GiB.
- prompt injection success 2/5, ACL violations 0.
- B gateway target file is empty and A/B metric contract is incompatible.
- regression and rollback fail-open reproductions in final review are release blockers.

LOCKED_FACT를 바꾸려면 source JSON/command output과 새 독립 재현을 먼저 추가한다.

## 11. EDITABLE_NARRATIVE

- slide title과 한 줄 요약
- 기술 스토리의 순서
- 표를 설명하는 문장
- 면접용 요약의 톤과 길이
- 색상, spacing, typography

Narrative를 바꿔도 FAIL을 PASS로 바꾸거나 evidence scope를 확대할 수 없다.

## 12. PENDING_VALIDATION / limitations

- 실제 A6000 A+B Compose end-to-end
- real A service Prometheus scrape and alerts
- in-flight drain under real traffic
- fail-closed regression/promotion fixes
- immutable image digest rollback + `/ready`
- actual 24GB native GPU validation
- PPTX의 PowerPoint/LibreOffice 실제 렌더링(해당 renderer가 없는 host에서는 structural validation만 가능)

수정 후 다음을 모두 다시 실행한다.

```bash
python3 scripts/build_final_report.py
python3 scripts/build_portfolio.py
python3 scripts/validate_final_artifacts.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
