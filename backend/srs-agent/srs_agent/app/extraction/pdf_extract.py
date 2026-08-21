"""Reading a PDF: its text layer where it has one, the model where it does not."""
from __future__ import annotations

import asyncio
import io
from typing import Any

MAX_MODEL_PAGES = 12
RENDER_DPI = 144

# Read PDFs in small page batches.
VISION_AT_ONCE = 3


def extract_pdf_text(data: bytes, filename: str = "upload.pdf") -> dict[str, Any]:

    try:
        import pdfplumber

        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        text = "\n\n".join(p for p in text_parts if p).strip()
        if text:
            return {"text": text, "engine": "pdfplumber", "pages": len(text_parts),
                    "page_texts": text_parts}
    except Exception:  # noqa: BLE001 - try the next engine
        pass

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
        return {"text": "\n\n".join(parts).strip(), "engine": "pypdf",
                "pages": len(parts), "page_texts": parts}
    except Exception as exc:  # noqa: BLE001
        return {
            "text": "",
            "engine": "none",
            "error": (
                "PDF text extraction unavailable. Install extras: "
                "`pip install pdfplumber pypdf`. "
                f"({exc})"
            ),
        }


def render_pages(data: bytes, indexes: list[int], dpi: int = RENDER_DPI) -> dict[int, bytes]:
    """Rasterise the given page indexes to PNG."""
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        try:
            import fitz as pymupdf  # Older name
        except Exception:  # noqa: BLE001
            return {}

    out: dict[int, bytes] = {}
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for i in indexes:
                if 0 <= i < doc.page_count:
                    pix = doc.load_page(i).get_pixmap(dpi=dpi)
                    out[i] = pix.tobytes("png")
    except Exception:  # noqa: BLE001
        return out
    return out


def page_count(data: bytes) -> int:
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        try:
            import fitz as pymupdf
        except Exception:  # noqa: BLE001
            return 0
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001
        return 0


async def read_pdf(data: bytes, filename: str = "upload.pdf") -> dict[str, Any]:
    """Extract what the PDF says, showing the model any page text cannot reach."""
    from .vision import describe_image, ocr_is_usable

    found = await asyncio.to_thread(extract_pdf_text, data, filename)

    texts: list[str] = list(found.get("page_texts") or [])
    if not texts and found.get("text"):
        texts = [found["text"]]

    total = found.get("pages") or len(texts) or page_count(data)
    # Fall back when the text layer is empty.
    blank = [i for i in range(total) if not ocr_is_usable(texts[i] if i < len(texts) else "")]

    if not blank:
        return {k: v for k, v in found.items() if k != "page_texts"}

    looked_at = blank[:MAX_MODEL_PAGES]
    images = await asyncio.to_thread(render_pages, data, looked_at)

    if not images:
        note = ("Some pages are scans and could not be read — install PyMuPDF "
                "(`pip install pymupdf`) to have the model read them.")
        out = {k: v for k, v in found.items() if k != "page_texts"}
        out["warning"] = out.get("error") or note
        return out

    gate = asyncio.Semaphore(VISION_AT_ONCE)

    async def one(index: int, png: bytes):
        async with gate:
            return index, await describe_image(png, f"{filename} page {index + 1}")

    seen = await asyncio.gather(*[one(i, png) for i, png in sorted(images.items())])

    by_page = {i: (r.get("text") or "").strip() for i, r in seen}
    errors = [r.get("error") for _, r in seen if r.get("error")]

    wanted = set(blank)
    merged: list[str] = []
    unread: list[int] = []
    for i in range(total):
        own = (texts[i] if i < len(texts) else "").strip()
        read = by_page.get(i, "")
        if read:
            merged.append(f"[page {i + 1}]\n{read}" + (f"\n{own}" if own else ""))
        elif own:
            merged.append(f"[page {i + 1}]\n{own}")
        elif i in wanted:
            # Both readers returned no text.
            unread.append(i + 1)

    text = "\n\n".join(merged).strip()
    if not text:
        return {"text": "", "engine": "none", "pages": total,
                "error": errors[0] if errors else "The PDF could not be read."}

    engine = found.get("engine") if found.get("text") else None
    result = {"text": text, "pages": total,
              "engine": f"{engine}+vision" if engine else "vision",
              "pages_read_by_model": len([1 for v in by_page.values() if v])}

    if unread:
        shown = ", ".join(str(p) for p in unread[:10])
        more = f" and {len(unread) - 10} more" if len(unread) > 10 else ""
        why = (f"only the first {MAX_MODEL_PAGES} unreadable pages are sent to the model"
               if len(blank) > len(looked_at)
               else (errors[0] if errors else "the model returned nothing for them"))
        result["warning"] = (f"{len(unread)} of {total} pages could not be read "
                             f"({shown}{more}) — {why}.")
        result["unread_pages"] = unread
    return result
