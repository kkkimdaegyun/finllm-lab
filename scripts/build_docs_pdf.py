#!/usr/bin/env python3
"""Bundle the repository's written output into one readable PDF.

The evidence for this project lives in Markdown across several directories.
Reading it means opening a dozen files in order, so this renders them as a
single printable document with a table of contents.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# (path, part title, section title). Order follows how the work actually ran.
SECTIONS: list[tuple[str, str, str]] = [
    ("docs/portfolio-brief.md", "요약", "양자화·금융 관점 기술 보고서"),
    ("README.md", "저장소", "README — 프로젝트 개요와 결과"),
    ("docs/project-brief.md", "1. 문제 정의", "문제·사용자·권한 모델·합격 조건"),
    ("docs/experiment-protocol.md", "2. 실험 설계", "실험 프로토콜"),
    ("docs/on-prem-architecture.md", "2. 실험 설계", "폐쇄망 배포 아키텍처"),
    ("corpus/README.md", "3. 평가 데이터", "corpus — 합성 문서와 ACL"),
    ("datasets/README.md", "3. 평가 데이터", "평가셋 60문항"),
    ("docs/cross-review/README.md", "4. 교차 검토", "Codex ↔ Claude 절차와 기록"),
    ("docs/cross-review/interface-contract.md", "4. 교차 검토", "인터페이스 계약"),
    ("docs/cross-review/codex-task.md", "4. 교차 검토", "A파트 지시서"),
    ("docs/cross-review/claude-task.md", "4. 교차 검토", "B파트 지시서"),
    ("docs/cross-review/review-rubric.md", "4. 교차 검토", "검토 기준"),
    ("work/a-part-notes.md", "4. 교차 검토", "A파트 구현 노트"),
    ("work/b-part-notes.md", "4. 교차 검토", "B파트 구현 노트"),
    ("docs/runbook-profile-a.md", "5. 측정", "Profile A 실측 runbook"),
    ("decisions/README.md", "6. 결정", "ADR 목록"),
    (
        "decisions/0004-profile-a-model-revised.md",
        "6. 결정",
        "ADR-0004 — Profile A 모델 선택 (개정, 유효)",
    ),
    (
        "decisions/0001-profile-a-model.md",
        "6. 결정",
        "ADR-0001 — Profile A 모델 선택 (Superseded, 오진 기록)",
    ),
    ("decisions/0002-retrieval-design.md", "6. 결정", "ADR-0002 — Retrieval 설계"),
    ("decisions/0003-evaluation-scoring.md", "6. 결정", "ADR-0003 — 품질 채점 방식"),
    ("results/README.md", "7. 결과", "결과 기록 규칙"),
    ("docs/portfolio-roadmap.md", "8. 다음 단계", "LLMOps 포트폴리오 로드맵"),
    ("docs/start-here.md", "부록", "원본 Start Here (인수받은 문서)"),
]

REVIEW_FILES = [
    ("work/review-claude-rag_index.json", "교차 검토 1라운드 — A파트"),
    ("work/review-claude-rag_index-round2.json", "교차 검토 2라운드 — A파트"),
]

CSS = """
@page {
  size: A4;
  margin: 20mm 18mm 18mm 18mm;
  @bottom-center { content: counter(page); font-size: 8pt; color: #888; }
}
@page :first { @bottom-center { content: ""; } }

body {
  font-family: "Noto Sans KR", "DejaVu Sans", sans-serif;
  font-size: 9.5pt; line-height: 1.62; color: #1a1a1a;
  background: #ffffff;
  /* Korean has no spaces inside a word, so the default break rule splits
     "서버" across lines. keep-all breaks at spaces only. */
  word-break: keep-all;
}
code, pre, .mono {
  font-family: "DejaVu Sans Mono", "Noto Sans Mono CJK KR", monospace;
  font-size: 8.4pt;
}
code { background: #f6f6f6; padding: 0 2px; border-radius: 2px; }
pre {
  background: #fafafa; border: 1px solid #e6e6e6; border-radius: 2px;
  padding: 8px 10px; overflow-wrap: break-word; white-space: pre-wrap;
  line-height: 1.45;
}
pre code { background: none; padding: 0; }

h1, h2, h3, h4 { font-weight: 600; line-height: 1.32; color: #111; }
h1 { font-size: 17pt; margin: 0 0 10pt; }
h2 { font-size: 12.5pt; margin: 18pt 0 7pt; padding-bottom: 3pt;
     border-bottom: 1px solid #dcdcdc; }
h3 { font-size: 10.5pt; margin: 13pt 0 5pt; }
h4 { font-size: 9.8pt; margin: 11pt 0 4pt; color: #333; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 7pt; padding-left: 17px; }
li { margin-bottom: 2.5pt; }

table { border-collapse: collapse; width: 100%; margin: 8pt 0 11pt;
        font-size: 8.6pt; }
th, td { border: none; border-bottom: 1px solid #e2e2e2;
         padding: 4.5pt 7pt; text-align: left; vertical-align: top;
         word-break: keep-all; overflow-wrap: break-word; }
thead th { border-bottom: 1.4px solid #999; font-weight: 600; color: #111; }
tbody tr:last-child td { border-bottom: 1px solid #ccc; }

blockquote { margin: 8pt 0; padding: 6pt 11pt; border-left: 2.5px solid #0b5cad;
             background: #f7fafd; color: #333; }
blockquote p:last-child { margin-bottom: 0; }
a { color: #0b5cad; text-decoration: none; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 14pt 0; }

.cover { text-align: left; padding-top: 55mm; }
.cover h1 { font-size: 25pt; margin-bottom: 5pt; letter-spacing: -0.3px; }
.cover .sub { font-size: 11.5pt; color: #555; margin-bottom: 26pt; }
.cover dl { font-size: 9.5pt; color: #333; }
.cover dt { float: left; width: 92px; color: #777; clear: left; }
.cover dd { margin: 0 0 4pt 92px; }
.cover .note { margin-top: 24pt; font-size: 8.6pt; color: #666;
               border-top: 1px solid #ddd; padding-top: 9pt; }

.cover { page-break-after: always; }
.toc { page-break-before: always; page-break-after: always; }
.toc h2 { border-bottom: 1.4px solid #999; }
.toc ol { list-style: none; padding-left: 0; font-size: 9pt; }
.toc .part { margin-top: 9pt; font-weight: 600; color: #111; }
.toc .item { padding-left: 13px; color: #333; }

.part-label { font-size: 8pt; letter-spacing: 1.1px; text-transform: uppercase;
              color: #0b5cad; margin-bottom: 2pt; font-weight: 600; }
section.doc { page-break-before: always; }
section.doc > h1 { font-size: 15pt; border-bottom: 2px solid #111;
                   padding-bottom: 5pt; margin-bottom: 12pt; }
.srcpath { font-size: 7.8pt; color: #888; margin: -8pt 0 12pt; }
"""


def render_markdown(text: str) -> str:
    import markdown

    return markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )


def build_html(
    sections: list[tuple[str, str, str]], front_matter: bool = True
) -> str:
    import datetime

    parts: list[str] = []
    toc: list[str] = []
    seen_parts: set[str] = set()

    for rel, part, title in sections:
        path = ROOT / rel
        if not path.exists():
            print(f"WARNING: missing {rel}", file=sys.stderr)
            continue
        if part not in seen_parts:
            toc.append(f'<li class="part">{html.escape(part)}</li>')
            seen_parts.add(part)
        toc.append(f'<li class="item">{html.escape(title)}</li>')
        body = render_markdown(path.read_text(encoding="utf-8"))
        parts.append(
            f'<section class="doc">'
            f'<div class="part-label">{html.escape(part)}</div>'
            f"<h1>{html.escape(title)}</h1>"
            f'<div class="srcpath">{html.escape(rel)}</div>'
            f"{body}</section>"
        )

    for rel, title in REVIEW_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        if "부록" not in seen_parts:
            toc.append('<li class="part">부록</li>')
            seen_parts.add("부록")
        toc.append(f'<li class="item">{html.escape(title)}</li>')
        payload = json.dumps(
            json.loads(path.read_text(encoding="utf-8")),
            ensure_ascii=False,
            indent=2,
        )
        parts.append(
            f'<section class="doc"><div class="part-label">부록</div>'
            f"<h1>{html.escape(title)}</h1>"
            f'<div class="srcpath">{html.escape(rel)}</div>'
            f"<pre><code>{html.escape(payload)}</code></pre></section>"
        )

    generated = datetime.date.today().isoformat()
    if not front_matter:
        return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>FinLLM Lab — 양자화·금융 관점 기술 보고서</title>
<style>{CSS} section.doc {{ page-break-before: auto; }}</style></head><body>
{''.join(parts)}
</body></html>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>FinLLM Lab — 산출 문서 모음</title><style>{CSS}</style></head><body>
<div class="cover">
  <h1>FinLLM Lab</h1>
  <div class="sub">금융 내부문서 RAG의 단일 GPU 배포 구성 선택 — 산출 문서 모음</div>
  <dl>
    <dt>측정일</dt><dd>2026-08-08</dd>
    <dt>하드웨어</dt><dd>NVIDIA RTX A6000 48GB 1장 (Ampere), driver 535.288.01, CUDA 12.2</dd>
    <dt>소프트웨어</dt><dd>vLLM 0.9.2, PyTorch 2.7.0+cu126, transformers 4.53.2</dd>
    <dt>후보 모델</dt><dd>Qwen3-8B BF16 / Qwen3-14B-AWQ W4A16</dd>
    <dt>문서 생성</dt><dd>{generated}</dd>
  </dl>
  <div class="note">
    이 문서에 실린 지연시간과 처리량은 모두 RTX A6000에서 관측한 값이며
    RTX 4090 성능이 아니다. 모든 결과 레코드의 증거 유형은
    <code>memory-budget-emulation</code>이고, 대상 카드 실측
    (<code>native-gpu-validation</code>)은 수행하지 않았다.
  </div>
</div>
<div class="toc"><h2>목차</h2><ol>{''.join(toc)}</ol></div>
{''.join(parts)}
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "report" / "finllm-lab-documents.pdf")
    parser.add_argument("--keep-html", action="store_true")
    parser.add_argument(
        "--brief-only",
        action="store_true",
        help="Render just docs/portfolio-brief.md, without the cover and contents",
    )
    args = parser.parse_args()

    if args.brief_only:
        # A single-document report needs no cover or contents page, and a stale
        # contents page is worse than none.
        sections = [s for s in SECTIONS if s[0] == "docs/portfolio-brief.md"]
        document = build_html(sections, front_matter=False)
    else:
        document = build_html(SECTIONS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    html_path = args.output.with_suffix(".html")
    html_path.write_text(document, encoding="utf-8")

    from weasyprint import HTML

    HTML(string=document, base_url=str(ROOT)).write_pdf(args.output)
    if not args.keep_html:
        html_path.unlink()

    size_mb = args.output.stat().st_size / 1024 / 1024
    print(f"{args.output}  ({size_mb:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
