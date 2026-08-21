"""One place that decides whether something changes data."""
from __future__ import annotations

import re


# Verbs that change stored data. `book` is deliberately absent
_MUTATION_VERBS = (
    r"add|apply|approve|archive|assign|borrow|buy|cancel|check\s*in|"
    r"check\s*out|complete|confirm|create|decline|delete|edit|enrol|enroll|"
    r"issue|join|lend|mark|pay|publish|purchase|register|reject|release|"
    r"remove|renew|reserve|restock|return|save|send|sign\s*up|signup|submit|"
    r"subscribe|update|upload"
)

# A control labelled "Book" IS the action
ACTION_LABEL_RE = re.compile(rf"\b(?:{_MUTATION_VERBS}|book)\b", re.I)

# Prose is the opposite case.
MUTATION_PROSE_RE = re.compile(
    rf"\b(?:{_MUTATION_VERBS})\b|\bbooks?\s+(?:a|an|the|one|this|that|your|my|their)\b",
    re.I)

READ_VERBS_RE = re.compile(
    r"\b(?:shows?|lists?|displays?|views?|sees?|browses?|renders?|"
    r"contains?|includes?)\b", re.I)


def prose_mutates(text: str) -> bool:
    """Does this sentence describe changing stored data?"""
    return bool(MUTATION_PROSE_RE.search(str(text or "")))


def label_mutates(text: str) -> bool:
    """Does this control's name say it commits something?"""
    return bool(ACTION_LABEL_RE.search(str(text or "")))


def any_prose_mutates(parts) -> bool:
    """True when any ONE of these sentences describes a change."""
    return any(prose_mutates(p) for p in (parts or []) if str(p or "").strip())


def capability_mutates(cap) -> bool:
    """Whether one planned capability changes data."""
    if not isinstance(cap, dict):
        return False
    return any_prose_mutates([cap.get("requirement"), cap.get("proof")])


def journey_mutates(caps, fallback_parts=()) -> bool:
    """Whether a journey has to persist a change to prove itself."""
    caps = [c for c in (caps or []) if isinstance(c, dict)]
    if caps:
        return any(capability_mutates(c) for c in caps)
    return any_prose_mutates(fallback_parts)
