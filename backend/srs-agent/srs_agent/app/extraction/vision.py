"""Reading an image with the model, with OCR alongside it."""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

from ..llm import LLMUnavailable, get_llm
from .ocr import extract_image_text

_SYS = (
    "You are reading an image a non-technical customer attached while "
    "describing an app they want built. Reply in plain text, no markdown.\n"
    "1. Transcribe every piece of text in the image, exactly, keeping the "
    "line order and any prices, codes or units.\n"
    "2. Then, in one or two sentences, say what the image shows and what it "
    "tells you about their business.\n"
    "If the image has no text at all, skip straight to the description."
)


MIN_OCR_WORDS = 4
MIN_OCR_LETTERS = 12


def ocr_is_usable(text: str) -> bool:
    letters = len(re.findall(r"[^\W\d_]", text or "", flags=re.UNICODE))
    words = len(re.findall(r"[^\W\d_]{2,}", text or "", flags=re.UNICODE))
    return letters >= MIN_OCR_LETTERS and words >= MIN_OCR_WORDS


async def describe_image(data: bytes, filename: str = "image.png") -> dict[str, Any]:
    """Ask the model to read the image."""
    try:
        text = await get_llm().complete_text(
            system=_SYS,
            user=f"The attached image is called {filename}. Read it now.",
            images=[base64.b64encode(data).decode("ascii")],
            label="vision_read",
        )
    except LLMUnavailable as exc:
        return {"text": "", "engine": "none",
                "error": f"Vision model unavailable. ({exc})"}
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "engine": "none",
                "error": f"Vision read failed. ({exc})"}
    return {"text": (text or "").strip(), "engine": "vision"}


async def _ocr(data: bytes, filename: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(extract_image_text, data, filename)
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "engine": "none", "error": f"OCR crashed. ({exc})"}


async def read_image(data: bytes, filename: str = "image.png") -> dict[str, Any]:
    """Show the image to the model, keeping OCR's exact reading alongside it."""

    seen, ocr = await asyncio.gather(describe_image(data, filename),
                                     _ocr(data, filename))

    described = (seen.get("text") or "").strip()
    read = (ocr.get("text") or "").strip()

    if described:
        if ocr_is_usable(read):
            return {"text": f"{described}\n\nText read from the image:\n{read}",
                    "engine": "vision+tesseract"}
        return {"text": described, "engine": "vision"}

    if read:
        return {**ocr,
                "warning": "Read by OCR only — the vision model did not answer.",
                "model_error": seen.get("error") or ""}

    return {"text": "", "engine": "none",
            "error": seen.get("error") or ocr.get("error") or "Image could not be read."}


__all__ = ["read_image", "describe_image", "ocr_is_usable"]
