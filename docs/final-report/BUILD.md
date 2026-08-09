# Final artifacts build

## Requirements

- Python 3.10+
- `markdown`, `jinja2`, `weasyprint`, `python-pptx`, `PyMuPDF`, `Pillow`
- Google Chrome/Chromium for HTML rendering checks
- Korean font: Noto Sans CJK KR or Noto Sans KR

이 빌드 도구는 CUDA, NVIDIA driver 또는 프로젝트 runtime dependency를 변경하지 않는다.

## Commands

Canonical repository root에서 실행한다.

```bash
python3 scripts/build_final_report.py
python3 scripts/build_portfolio.py
python3 scripts/validate_final_artifacts.py
```

생성물:

```text
artifacts/FinLLM-Lab-v0.2-Final-Technical-Report.pdf
artifacts/FinLLM-Lab-v0.2-Developer-Portfolio.pptx
portfolio/assets/previews/report-contact-sheet.png
portfolio/assets/previews/html-contact-sheet.png
```

HTML은 다음 명령으로 로컬에서 확인한다.

```bash
python3 -m http.server 8765 --directory portfolio
# http://127.0.0.1:8765/index.html
```

PPTX는 `slide-data.json`의 text/table/shape를 `python-pptx` 객체로 생성한다. 슬라이드 전체를 이미지로 붙이지 않는다. LibreOffice/PowerPoint renderer가 설치된 환경에서는 다음 검증을 추가한다.

```bash
libreoffice --headless --convert-to pdf --outdir artifacts/rendered \
  artifacts/FinLLM-Lab-v0.2-Developer-Portfolio.pptx
```

현재 host에는 LibreOffice/PowerPoint renderer가 없으면 PPTX visual rendering은 `PENDING_VALIDATION`으로 남긴다. 이 경우 validator는 OOXML 구조, slide size, editable object count, text clipping heuristic과 HTML 동등성을 검사하지만 실제 PowerPoint layout engine의 결과를 대체하지 않는다.

## Editing

- 보고서 문장: `docs/final-report/final-report.md`
- 슬라이드 문장/숫자/순서: `portfolio/slide-data.json`
- HTML layout: `portfolio/styles.css`, `portfolio/index.html`
- PPTX layout: `scripts/build_portfolio.py`

숫자를 수정할 때는 `results/*.json` 원본과 `scripts/validate_final_artifacts.py`의 provenance check를 함께 갱신한다. native GPU evidence가 생기기 전에는 `memory-budget-emulation` 표기를 제거하지 않는다.
