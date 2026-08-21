"""Best-effort JSON extraction & repair for LLM output."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _balanced_object(text: str) -> str | None:
    """Return the first balanced {...} block, honouring strings/escapes."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from arbitrary LLM text."""
    if not text or not text.strip():
        raise ValueError("empty LLM response")

    candidates: list[str] = []
    unfenced = strip_code_fences(text)
    candidates.append(unfenced)
    block = _balanced_object(unfenced)
    if block:
        candidates.append(block)
    block2 = _balanced_object(text)
    if block2:
        candidates.append(block2)

    last_err: Exception | None = None
    for cand in candidates:
        for attempt in (cand, _remove_trailing_commas(cand)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as exc:  # noqa: PERF203
                last_err = exc
    raise ValueError(f"could not parse JSON from LLM output: {last_err}")


def format_validation_errors(errors: Any) -> str:
    """Render pydantic ValidationError.errors() into compact repair guidance."""
    lines: list[str] = []
    try:
        for e in errors:
            loc = ".".join(str(x) for x in e.get("loc", []))
            lines.append(f"- {loc}: {e.get('msg')}")
    except Exception:  # noqa: BLE001
        return str(errors)
    return "\n".join(lines[:40])
