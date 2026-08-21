"""Merge raw idea + all extracted sources into one normalized brief."""
from __future__ import annotations

import re
from typing import Any


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_brief(raw_idea: str, sources: list[dict[str, Any]]) -> str:
    """Combine the typed idea with text from PDF/image/voice sources."""
    parts: list[str] = []
    idea = _clean(raw_idea or "")
    if idea:
        parts.append(f"USER IDEA:\n{idea}")
    for src in sources:
        text = _clean(src.get("text", ""))
        meta = src.get("meta") or {}
        purpose = _clean(str(meta.get("purpose") or ""))
        url = str(meta.get("url") or "").strip()
        if not text and not purpose and not url:
            continue
        mode = src.get("mode", "source").upper()
        fname = src.get("filename") or ""
        header = f"{mode} SOURCE" + (f" ({fname})" if fname else "")
        lines = [f"{header}:"]
        # What it is for comes before what it looks like.
        if purpose:
            lines.append(f"The person says this is for: {purpose}")
        if url:
            lines.append(f"It is already in the project and served at `{url}` — "
                         f"use that exact path, do not invent another and do "
                         f"not ask for it to be generated.")
        if text:
            lines.append(text)
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts).strip()
