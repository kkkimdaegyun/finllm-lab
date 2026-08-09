# Claude final review prompt

당신의 역할은 **Technical Portfolio Editor + Independent Evidence Reviewer**다.

먼저 다음 파일을 모두 읽는다.

- `docs/final-review/final-release-review.json`
- `docs/final-review/CLAUDE-HANDOFF.md`
- `docs/final-report/final-report.md`
- `docs/final-report/resume-interview-summary.md`
- `portfolio/slide-data.json`
- `portfolio/index.html`
- `portfolio/styles.css`
- `scripts/build_final_report.py`
- `scripts/build_portfolio.py`
- `scripts/validate_final_artifacts.py`

숫자는 반드시 `results/*.json`, `ops/evidence/final-rehearsal/*`, config, code와
대조한다.

## Review objectives

1. 기술적 과장을 제거한다.
2. problem → constraint → measurement → unexpected result → investigation → decision →
   deployment → observability → incident → regression → recovery 흐름을 선명하게 한다.
3. 기능 목록보다 trade-off, debugging, evidence discipline을 앞세운다.
4. A6000 memory-budget-emulation을 actual 24GB/4090/5090 native 결과로 오해할 문장을
   찾는다.
5. PPT 12장이 5~10분 면접 설명에 맞는지 검토한다.

## Modification boundary

`CLAUDE-HANDOFF.md`의 `LOCKED_FACT`는 repository evidence 없이 바꾸지 않는다.
새 evidence를 직접 실행하지 않았다면 숫자, PASS, verification status를 바꾸지 않는다.
수정 대상은 `EDITABLE_NARRATIVE`다.

특히 다음을 유지한다.

- PASS의 범위는 single-A6000 v0.2 reference project
- actual 24GB native validation은 `NOT_EXECUTED`
- alert windows는 `PENDING_THRESHOLD_VALIDATION`
- prompt injection success 1/5는 known limitation
- A6000 v0.1 throughput을 v0.2 API end-to-end 수치로 바꾸지 않음

## Required output

```json
{
  "verdict_on_narrative": "",
  "evidence_overclaims": [],
  "story_gaps": [],
  "slide_specific_changes": [],
  "report_specific_changes": [],
  "locked_fact_conflicts": [],
  "recommended_edits": []
}
```

사용자가 수정을 요청하면 `EDITABLE_NARRATIVE`만 수정한 뒤 다음을 모두 재실행한다.

```bash
python3 scripts/build_final_report.py
python3 scripts/build_portfolio.py
python3 scripts/validate_final_artifacts.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
