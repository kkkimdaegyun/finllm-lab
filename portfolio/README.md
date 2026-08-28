# FinLLM Lab v0.2 Developer Portfolio

HTML과 PPTX는 `slide-data.json`을 공통 source로 사용한다. 모든 본문과 도형은
PowerPoint에서 직접 수정할 수 있으며, HTML과 PPTX 모두 `Noto Sans KR`을 사용한다.

## 직접 수정하는 순서

1. 문장·수치·순서를 `portfolio/slide-data.json`에서 바꾼다.
2. 아래 Preview 명령으로 HTML을 보면서 확인한다.
3. `python3 scripts/build_portfolio.py`로 editable PPTX를 다시 만든다.
4. PowerPoint 또는 LibreOffice에서 문장과 도형을 직접 미세 조정한다.

## Preview

```bash
python3 -m http.server 8765 --directory portfolio
```

브라우저에서 `http://127.0.0.1:8765/index.html`을 연다. `?slide=6`을 붙이면 한 장만 표시한다.

## Build

```bash
python3 scripts/build_portfolio.py
```

결과:

- `artifacts/FinLLM-Lab-v0.2-Developer-Portfolio.html` (서버 없이 바로 열 수 있는 단일 HTML)
- `artifacts/FinLLM-Lab-v0.2-Developer-Portfolio.pptx`
- `portfolio/assets/previews/html/slide-*.png`
- `portfolio/assets/previews/html-contact-sheet.png`

PPTX는 PowerPoint/LibreOffice layout engine으로 별도 렌더링한다.

```bash
python3 scripts/render_pptx_preview.py
```

결과는 `artifacts/rendered/FinLLM-Lab-v0.2-Developer-Portfolio.pdf`와
`portfolio/assets/previews/pptx-contact-sheet.png`이다.

## Editing contract

문장, 숫자, 순서는 `slide-data.json`에서 바꾼다. `index.html`은 renderer이고 `styles.css`는 presentation style이다. PPTX builder도 같은 JSON을 읽어 text/table/shape object를 만든다.

모든 측정 숫자는 evidence path를 가져야 한다. `memory-budget-emulation`과
`NOT_EXECUTED` 경계는 새 repository evidence 없이 바꾸지 않는다.
