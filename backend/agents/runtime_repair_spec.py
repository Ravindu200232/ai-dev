"""Deterministic repair plans for exact runtime ReferenceErrors."""
from __future__ import annotations

import re

from .features_common import FeatureSpec


_REFERENCE_RE = re.compile(
    r"ReferenceError:\s*([A-Za-z_$][\w$]*)\s+is\s+not\s+defined", re.I)

_KNOWN_OWNER = {
    "getSessionUser": "@/lib/auth",
    "getCollection": "@/lib/mongodb",
    "getDb": "@/lib/mongodb",
    "serialize": "@/lib/mongodb",
    "notFound": "next/navigation",
    "redirect": "next/navigation",
    "NextResponse": "next/server",
    "ObjectId": "mongodb",
}


def _source_kind(path: str, body: str) -> str:
    if path.endswith("/route.js") or path.endswith("/route.jsx"):
        return "route"
    head = str(body or "").lstrip()
    return "client" if head.startswith(("'use client'", '"use client"')) else "server"


def _stack_line(blob: str, path: str) -> int:
    hit = re.search(re.escape(path) + r":(\d+)(?::\d+)?", blob, re.I)
    if not hit:
        return 0
    try:
        return int(hit.group(1))
    except Exception:
        return 0


def _definition_before(name: str, body: str, line: int) -> bool:
    """Whether source establishes `name` before the runtime stack line."""
    prefix = "\n".join(str(body or "").splitlines()[: max(0, line - 1)])
    if re.search(rf"\bimport\b[^\n;]*\b{re.escape(name)}\b[^\n;]*(?:from\b|;|$)",
                 prefix):
        return True
    if re.search(rf"\b(?:const|let|var|function|class)\s+{re.escape(name)}\b",
                 prefix):
        return True
    # Destructured declaration/import aliases are common enough
    if re.search(rf"\b(?:const|let|var|import)\s*\{{[^}}]*\b{re.escape(name)}\b[^}}]*\}}",
                 prefix):
        return True
    return False


def exact_runtime_repair_spec(arch, evidence: str, focus_paths=None):
    """Return a one-file evidence-backed FeatureSpec for a proven free name."""
    blob = str(evidence or "").replace("\\", "/")
    m = _REFERENCE_RE.search(blob)
    if not m:
        return None
    name = m.group(1)
    files = getattr(arch, "files", None) or {}

    candidates = []
    for raw in focus_paths or []:
        rel = str(raw or "").strip().replace("\\", "/").lstrip("./")
        if rel in files and rel.startswith(("app/", "components/", "lib/")):
            candidates.append(rel)
    for rel in files:
        reln = str(rel or "").replace("\\", "/").lstrip("./")
        if not reln.startswith(("app/", "components/", "lib/")):
            continue
        if re.search(re.escape(reln) + r":\d+(?::\d+)?", blob, re.I):
            candidates.append(reln)

    for rel in dict.fromkeys(candidates):
        body = str(files.get(rel, "") or "")
        line = _stack_line(blob, rel)
        if not line:
            continue
        rows = body.splitlines()
        if line > len(rows):
            continue
        source_line = rows[line - 1]
        if not re.search(rf"\b{re.escape(name)}\b", source_line):
            continue
        if _definition_before(name, body, line):
            continue

        owner = _KNOWN_OWNER.get(name, "")
        if owner:
            cause = (f"{rel}:{line} uses `{name}` before this file imports it; "
                     f"the generated stack owns that helper in '{owner}'")
            why = (f"runtime stack proves `{name}` is free at line {line}; import "
                   f"it from '{owner}' and preserve all existing behavior")
        else:
            cause = (f"{rel}:{line} uses `{name}` before any local import or "
                     "declaration establishes it")
            why = (f"runtime stack proves `{name}` is free at line {line}; "
                   "establish the existing project value/import before first use")

        return FeatureSpec(
            summary=f"Repair the exact runtime ReferenceError for `{name}` in {rel}",
            files=[{
                "path": rel,
                "action": "edit",
                "kind": _source_kind(rel, body),
                "why": why,
            }],
            context={
                "current": f"The real browser reached {rel} and threw ReferenceError: {name} is not defined.",
                "gap": "The route must render without an uncaught runtime exception before the E2E step can be judged.",
                "cause": cause,
                "evidence": [{
                    "path": rel,
                    "fact": f"runtime stack points to line {line}, whose source is: {source_line.strip()[:420]}",
                }],
                "verify": (f"rerun the failing browser checkpoint and confirm no "
                           f"ReferenceError for {name} appears and the journey advances past this route"),
                "confidence": "high",
            },
        )
    return None


__all__ = ["exact_runtime_repair_spec"]
