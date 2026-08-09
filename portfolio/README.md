# FinLLM Lab v0.2 Developer Portfolio

HTML과 PPTX는 `slide-data.json`을 공통 source로 사용한다.

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
