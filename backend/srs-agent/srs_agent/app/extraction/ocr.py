"""Image OCR via pytesseract, falling back to easyocr."""
from __future__ import annotations

import os
from pathlib import Path
from shutil import which
from typing import Any


_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)


def tesseract_cmd() -> str | None:
    """Where the Tesseract binary actually is, or None."""
    override = os.environ.get("TESSERACT_CMD", "").strip()
    if override:
        return override if Path(override).exists() else which(override)
    found = which("tesseract")
    if found:
        return found
    return next((c for c in _TESSERACT_CANDIDATES if Path(c).exists()), None)


def extract_image_text(data: bytes, filename: str = "upload.png") -> dict[str, Any]:

    try:
        import io

        import pytesseract
        from PIL import Image

        cmd = tesseract_cmd()
        if not cmd:
            raise FileNotFoundError("tesseract binary not found")
        pytesseract.pytesseract.tesseract_cmd = cmd

        img = Image.open(io.BytesIO(data))
        text = (pytesseract.image_to_string(img) or "").strip()
        return {"text": text, "engine": "tesseract"}
    except Exception:  # noqa: BLE001
        pass

    try:
        import numpy as np  # type: ignore
        import easyocr  # type: ignore
        from PIL import Image
        import io

        reader = easyocr.Reader(["en"], gpu=False)
        img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
        lines = reader.readtext(img, detail=0)
        return {"text": "\n".join(lines).strip(), "engine": "easyocr"}
    except Exception as exc:  # noqa: BLE001
        return {
            "text": "",
            "engine": "none",
            "error": (
                "OCR unavailable. Install Tesseract + `pip install pytesseract pillow`, "
                "or `pip install easyocr`. "
                f"({exc})"
            ),
        }
