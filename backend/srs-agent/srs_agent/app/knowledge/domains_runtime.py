"""Domain lookup and fixed classification."""
from __future__ import annotations

import re
from typing import Any

from .domains_catalog_a import DOMAIN_PART_A
from .domains_catalog_b import DOMAIN_PART_B
from .domains_generic import GENERIC_DOMAIN

DOMAIN_LIBRARY: dict[str, dict[str, Any]] = {**DOMAIN_PART_A, **DOMAIN_PART_B}
GENERIC_KEY = "custom"

def get_domain(domain_key: str) -> dict[str, Any]:
    return DOMAIN_LIBRARY.get(domain_key, GENERIC_DOMAIN)


_WORD_RE = re.compile(r"[a-z0-9]+")


def classify_domain(text: str) -> tuple[str, float, str]:
    """Keyword-score the brief against the library."""
    tokens = set(_WORD_RE.findall((text or "").lower()))
    if not tokens:
        return GENERIC_KEY, 0.0, GENERIC_DOMAIN["label"]

    scores: dict[str, int] = {}
    for key, dom in DOMAIN_LIBRARY.items():
        hits = 0
        for kw in dom["keywords"]:
            parts = kw.split()
            if len(parts) == 1:
                if kw in tokens:
                    hits += 1
            elif kw in (text or "").lower():
                hits += 2
        scores[key] = hits

    best_key = max(scores, key=scores.get)
    best = scores[best_key]
    if best == 0:
        return GENERIC_KEY, 0.0, GENERIC_DOMAIN["label"]

    total = sum(scores.values()) or 1

    confidence = min(0.97, 0.45 + 0.5 * (best / total) + min(best, 4) * 0.05)
    return best_key, round(confidence, 2), DOMAIN_LIBRARY[best_key]["label"]
