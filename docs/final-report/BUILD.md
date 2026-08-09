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
python3 scripts/render_pptx_preview.py
python3 scripts/validate_final_artifacts.py
```

생성물:

```text
artifacts/FinLLM-Lab-v0.2-Final-Technical-Report.pdf
artifacts/FinLLM-Lab-v0.2-Developer-Portfolio.pptx
portfolio/assets/previews/report-contact-sheet.png
portfolio/assets/previews/html-contact-sheet.png
portfolio/assets/previews/pptx-contact-sheet.png
artifacts/rendered/FinLLM-Lab-v0.2-Developer-Portfolio.pdf
```

HTML은 다음 명령으로 로컬에서 확인한다.

```bash
python3 -m http.server 8765 --directory portfolio
# http://127.0.0.1:8765/index.html
```

PPTX는 `slide-data.json`의 text/table/shape를 `python-pptx` 객체로 생성한다. 슬라이드 전체를 이미지로 붙이지 않는다. LibreOffice/PowerPoint renderer로 다음 검증을 추가한다.

```bash
python3 scripts/render_pptx_preview.py
```

`FINLLM_SOFFICE`로 LibreOffice executable 경로를 지정할 수도 있다. 최종 audit에서는
LibreOffice 7.3.7 Impress로 12쪽 PDF를 실제 생성하고 contact sheet와 개별 slide를
검토했다. validator는 이 PDF의 12쪽 여부와 OOXML의 editable object도 함께 검사한다.

## Editing

- 보고서 문장: `docs/final-report/final-report.md`
- 슬라이드 문장/숫자/순서: `portfolio/slide-data.json`
- HTML layout: `portfolio/styles.css`, `portfolio/index.html`
- PPTX layout: `scripts/build_portfolio.py`

숫자를 수정할 때는 `results/*.json` 원본과 `scripts/validate_final_artifacts.py`의 provenance check를 함께 갱신한다. native GPU evidence가 생기기 전에는 `memory-budget-emulation` 표기를 제거하지 않는다.
