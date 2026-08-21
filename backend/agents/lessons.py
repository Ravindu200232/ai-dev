"""What previous builds got wrong, so the next one is told before it starts."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("lessons")

STORE = Path.home() / ".agentforge" / "lessons.json"

# The block handed to the builder.
MAX_IN_PROMPT = 10
MAX_CHARS = 1600
MAX_KEPT = 120

MIN_PROJECTS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: Path = None) -> dict:
    """Every lesson recorded so far."""
    path = path or STORE
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("lessons") or {}
    except Exception as e:                                      # noqa: BLE001
        log.warning(f"lessons unreadable: {e}")
    return {}


def _save(lessons: dict, path: Path = None) -> None:
    path = path or STORE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Trimmed by how much evidence there is, not by age alone.
        if len(lessons) > MAX_KEPT:
            ranked = sorted(lessons.items(),
                            key=lambda kv: (kv[1].get("count", 0),
                                            kv[1].get("last_seen", "")),
                            reverse=True)
            lessons = dict(ranked[:MAX_KEPT])
        body = json.dumps({"version": 1, "lessons": lessons}, indent=1)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False,
                                         dir=str(path.parent)) as tmp:
            tmp.write(body)
            temp = tmp.name
        os.replace(temp, path)
    except Exception as e:                                      # noqa: BLE001
        log.warning(f"lessons unwritable: {e}")


def record(project: str, entries, path: Path = None) -> int:
    """Note what went wrong in one project."""
    lessons = load(path)
    fresh = 0
    for code, remedy in entries or []:
        code = " ".join(str(code or "").split())[:80]
        if not code:
            continue
        row = lessons.setdefault(code, {"count": 0, "projects": [],
                                        "remedy": "", "last_seen": ""})
        if project and project not in row["projects"]:
            row["projects"].append(project)
            row["count"] = len(row["projects"])
            fresh += 1
        row["last_seen"] = _now()
        remedy = " ".join(str(remedy or "").split())[:220]
        if remedy and len(remedy) > len(row.get("remedy") or ""):
            row["remedy"] = remedy
    if fresh or lessons:
        _save(lessons, path)
    return fresh


def _unbag_lint(message: str) -> tuple:
    """A LINT finding's own rule, as a code and a remedy."""
    text = " ".join(str(message or "").split())
    head, sep, rest = text.partition(": ")
    # Only a real path prefix is dropped.
    if sep and ("/" in head or head.endswith((".js", ".jsx", ".mjs", ".json"))):
        text = rest
    rule, _, remedy = text.partition(" — ")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", rule).strip("_").upper()
    if len(slug) > 44:  # On a word boundary, so it stays readable
        slug = slug[:44].rpartition("_")[0] or slug[:44]
    return (f"LINT_{slug}" if slug else "", remedy.strip() or rule.strip())


def from_findings(findings) -> list:
    """`(code, fix)` pairs out of an AnalyzerReport's findings."""
    out = []
    for f in findings or []:
        code = getattr(f, "code", "") or ""
        if not code:
            continue
        message = getattr(f, "message", "") or ""
        if code == "LINT":
            code, remedy = _unbag_lint(message)
            if code:
                out.append((code, remedy))
            continue
        out.append((code, getattr(f, "fix", "") or message))
    return out


QA_REMEDIES = {
    "route handler crashed":
        "A route handler threw instead of answering. Wrap the body in "
        "try/catch, return a JSON error with a real status, and never let an "
        "await reject into the framework.",
    "next/navigation not mocked":
        "A Client Component called useRouter/useSearchParams. Read them in one "
        "small 'use client' component, not in something a test renders bare.",
    "submit-by-click":
        "A form's submit handler never fired. Put the handler on the <form> "
        "onSubmit with a type=\"submit\" button inside it, not an onClick on "
        "the button.",
    "seed-data ambiguity":
        "Two seeded rows render the same text, so the page has two identical "
        "elements. Seed distinguishable rows — different names, not Item 1 and "
        "Item 1.",
    "ObjectId vs JSON":
        "An ObjectId crossed to a Client Component. serialize(doc) before it "
        "leaves the server; compare with doc.field?.toString().",
    "invented testid":
        "A data-testid was expected that no component sets. Find elements by "
        "role and accessible name instead of inventing test hooks.",
    "role/name not in markup":
        "The element could not be found by role or label. Give every control "
        "real text, and every input a <label htmlFor> matching its id.",
    "exact copy not there":
        "Copy was expected that the page does not contain. Keep the visible "
        "wording of a screen and its test in step.",
    "missing helper or import":
        "Something was called that is not exported. Check the export list of "
        "the file you import from before you call into it.",
    "vi.mock hoisting":
        "vi.mock is hoisted above the file, so it cannot close over a const "
        "declared later. Declare mock data inside the factory.",
    "timeout or async":
        "A test hung. Await every promise, and never leave a pending fetch "
        "with no resolved mock.",
    "value mismatch":
        "The value produced was not the value promised. Field names are the "
        "usual cause — the shape written to the database and the shape read "
        "back must use exactly the same keys.",
    "presence assumption":
        "Something was expected on the page that never rendered. A list needs "
        "its empty state, and a value that can be absent needs a fallback, so "
        "the screen says something either way.",
}


def from_qa_history(rounds) -> list:
    """`(class, remedy)` pairs out of the QA agent's own failure classes."""
    out = []
    for row in rounds or []:
        if not isinstance(row, dict) or not row.get("failed"):
            continue
        for top in row.get("top") or []:
            name = (top or {}).get("class")
            if name:
                code = f"TEST_{str(name).upper().replace(' ', '_')[:40]}"
                out.append((code, QA_REMEDIES.get(str(name), "")))
    return out


def prompt_block(path: Path = None) -> str:
    """The block for the builder's system prompt, or "" when there is nothing."""
    lessons = load(path)
    # A remedy is the price of admission.
    ranked = [(code, row) for code, row in lessons.items()
              if row.get("count", 0) >= MIN_PROJECTS and (row.get("remedy") or "").strip()]
    ranked.sort(key=lambda kv: (kv[1].get("count", 0), kv[1].get("last_seen", "")),
                reverse=True)
    if not ranked:
        return ""

    lines = [f"  • {code} — seen in {row['count']} previous builds. {row['remedy']}"
             for code, row in ranked[:MAX_IN_PROMPT]]

    head = ("WHAT PREVIOUS BUILDS GOT WRONG\n\n"
            "Each of these was found by the checks that run after a build, in "
            "more than one project. They are not hypothetical and they are not "
            "style — they are the mistakes this system actually makes. Do not "
            "make them again:\n")
    block = head + "\n".join(lines)
    return block[:MAX_CHARS]


__all__ = ["record", "load", "prompt_block", "from_findings", "from_qa_history",
           "STORE"]
