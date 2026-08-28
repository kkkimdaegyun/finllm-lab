#!/usr/bin/env python3
"""Build and raster-check the editable Markdown final report."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pymupdf
import markdown
from PIL import Image, ImageDraw
from weasyprint import HTML


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "final-report" / "final-report.md"
ARTIFACT = ROOT / "artifacts" / "FinLLM-Lab-v0.2-Final-Technical-Report.pdf"
PREVIEW = ROOT / "portfolio" / "assets" / "previews" / "report"
CONTACT = ROOT / "portfolio" / "assets" / "previews" / "report-contact-sheet.png"


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:marker].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, text[marker + 5 :]


CSS = """
@font-face { font-family: NotoKR; src: local('Noto Sans CJK KR'); }
@page {
  size: A4; margin: 18mm 17mm 19mm;
  @bottom-left { content: 'FinLLM Lab v0.2 · Evidence-locked final report'; color:#65758a; font:8.5pt NotoKR; }
  @bottom-right { content: counter(page) ' / ' counter(pages); color:#65758a; font:8.5pt NotoKR; }
}
* { box-sizing:border-box; }
body { font-family:NotoKR,'Noto Sans CJK KR',sans-serif; color:#172536; font-size:9.7pt; line-height:1.62; }
.cover { page:cover; height:242mm; display:flex; flex-direction:column; justify-content:center; padding:22mm 13mm; color:#f5f8fc; background:#07111f; border-left:8px solid #25b9aa; }
.cover .eyebrow { color:#39d6c1; letter-spacing:.17em; font-size:9pt; font-weight:800; }
.cover h1 { font-size:32pt; line-height:1.18; margin:10mm 0 5mm; color:white; border:0; }
.cover h2 { font-size:16pt; color:#c9d6e5; font-weight:500; margin:0; border:0; }
.cover .meta { margin-top:25mm; color:#a8bbcf; }
.cover .verdict { display:inline-block; margin-top:8mm; padding:4mm 6mm; border:1px solid #6ee7a8; color:#6ee7a8; font-weight:800; }
.toc { page-break-after:always; padding-top:8mm; }
.toc h2 { border:0; }
.toc ol { columns:2; column-gap:12mm; padding-left:6mm; }
.toc li { margin:0 0 2.2mm; break-inside:avoid; }
h1 { page-break-before:always; color:#0b5966; font-size:20pt; line-height:1.25; padding-bottom:3mm; border-bottom:1px solid #b8d3d8; }
h1:first-of-type { page-break-before:auto; }
h2 { color:#174564; font-size:14pt; margin-top:7mm; page-break-after:avoid; }
h3 { color:#275472; font-size:11.5pt; page-break-after:avoid; }
p, li { orphans:3; widows:3; }
blockquote { margin:5mm 0; padding:4mm 5mm; background:#edf7f6; border-left:4px solid #25b9aa; color:#173e45; }
code { font-family:'DejaVu Sans Mono',monospace; font-size:8.5pt; background:#eef2f6; padding:.2mm .8mm; border-radius:2px; overflow-wrap:anywhere; }
pre { background:#0d1d2e; color:#eaf2f9; padding:4mm; border-radius:3mm; white-space:pre-wrap; word-break:break-word; font-size:8.1pt; line-height:1.45; break-inside:avoid; }
pre code { background:transparent; padding:0; }
table { width:100%; border-collapse:collapse; margin:4mm 0 5mm; font-size:8.1pt; line-height:1.35; }
thead { display:table-header-group; }
tr { break-inside:avoid; }
th { background:#14334d; color:white; text-align:left; padding:2.3mm 2mm; }
td { border-bottom:.3mm solid #d4dfe8; padding:2.2mm 2mm; vertical-align:top; }
tbody tr:nth-child(even) td { background:#f5f8fb; }
a { color:#0f7682; text-decoration:none; }
hr { border:0; border-top:1px solid #ccd8e2; }
.boundary { margin-top:7mm; padding:4mm; background:#fff4dc; border:1px solid #edc774; }
"""


def build_html(meta: dict[str, str], body_md: str) -> str:
    body = markdown.markdown(
        body_md,
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )
    headings = re.findall(r"^# (.+)$", body_md, flags=re.MULTILINE)
    toc = "".join(f"<li>{html.escape(title)}</li>" for title in headings)
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><style>{CSS}</style></head><body>
    <section class='cover'>
      <div class='eyebrow'>PRIVATE RAG · INFERENCE · PRODUCTION LLMOPS</div>
      <h1>{html.escape(meta.get('title','FinLLM Lab v0.2'))}</h1>
      <h2>{html.escape(meta.get('subtitle',''))}</h2>
      <div class='verdict'>FINAL RELEASE · PASS</div>
      <div class='meta'>{html.escape(meta.get('author',''))} · {html.escape(meta.get('date',''))}<br>
      Actual RTX A6000 integrated rehearsal · Native 24GB validation: NOT_EXECUTED</div>
    </section>
    <section class='toc'><h2>Contents</h2><ol>{toc}</ol></section>
    {body}</body></html>"""


def render_preview() -> int:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    for stale in PREVIEW.glob("page-*.png"):
        stale.unlink()
    document = pymupdf.open(ARTIFACT)
    thumbs: list[Image.Image] = []
    for index, page in enumerate(document):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False)
        target = PREVIEW / f"page-{index + 1:02d}.png"
        pix.save(target)
        image = Image.open(target).convert("RGB")
        image.thumbnail((300, 424))
        thumbs.append(image.copy())
    cols = 4
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 324, rows * 460), "#162335")
    draw = ImageDraw.Draw(sheet)
    for index, thumb in enumerate(thumbs):
        x = (index % cols) * 324 + 12
        y = (index // cols) * 460 + 25
        sheet.paste(thumb, (x, y))
        draw.text((x, 5 + (index // cols) * 460), f"Page {index + 1}", fill="white")
    CONTACT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(CONTACT)
    document.close()
    return len(thumbs)


def main() -> int:
    meta, body = split_frontmatter(SOURCE.read_text(encoding="utf-8"))
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=build_html(meta, body), base_url=str(ROOT)).write_pdf(ARTIFACT)
    pages = render_preview()
    if pages < 10:
        raise SystemExit(f"unexpectedly short report: {pages} pages")
    print(f"PDF: {ARTIFACT} ({pages} pages, {ARTIFACT.stat().st_size} bytes)")
    print(f"rendered preview: {CONTACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
