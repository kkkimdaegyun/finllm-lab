#!/usr/bin/env python3
"""Render the generated PPTX with LibreOffice and create a visual contact sheet."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
PPTX = ROOT / "artifacts/FinLLM-Lab-v0.2-Developer-Portfolio.pptx"
OUT = ROOT / "artifacts/rendered"
PDF = OUT / "FinLLM-Lab-v0.2-Developer-Portfolio.pdf"
CONTACT = ROOT / "portfolio/assets/previews/pptx-contact-sheet.png"


def main() -> int:
    soffice = os.environ.get("FINLLM_SOFFICE") or shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        raise SystemExit("LibreOffice renderer not found; set FINLLM_SOFFICE")
    OUT.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="finllm-lo-profile-"))
    subprocess.run(
        [soffice, "--headless", f"-env:UserInstallation={profile.as_uri()}",
         "--convert-to", "pdf", "--outdir", str(OUT), str(PPTX)],
        check=True,
    )
    document = pymupdf.open(PDF)
    thumbs=[]
    for index,page in enumerate(document):
        pix=page.get_pixmap(matrix=pymupdf.Matrix(1.25,1.25),alpha=False)
        target=OUT/f"slide-{index+1:02d}.png"; pix.save(target)
        image=Image.open(target).convert("RGB"); image.thumbnail((384,216)); thumbs.append(image.copy())
    document.close()
    cols=3; rows=(len(thumbs)+cols-1)//cols
    sheet=Image.new("RGB",(cols*410,rows*250),"#101b2a"); draw=ImageDraw.Draw(sheet)
    for index,image in enumerate(thumbs):
        x=(index%cols)*410+13; y=(index//cols)*250+27; sheet.paste(image,(x,y)); draw.text((x,y-19),f"Slide {index+1}",fill="white")
    CONTACT.parent.mkdir(parents=True,exist_ok=True); sheet.save(CONTACT)
    print(f"PPTX render: {PDF} ({len(thumbs)} pages)")
    print(f"contact sheet: {CONTACT}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
