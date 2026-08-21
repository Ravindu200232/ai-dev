"""IntakeExtractorAgent — normalise input and guard against nonsense."""
from __future__ import annotations

import re

from ..services.events import bus
from .state import AgentState

_WORD_RE = re.compile(r"[A-Za-z]{2,}")


_COMMON = {
    "the", "a", "an", "and", "or", "for", "to", "of", "with", "in", "on", "by",
    "that", "this", "where", "who", "can", "will", "should", "have", "has", "be",
    "is", "are", "my", "our", "their", "they", "it", "as", "so", "from", "at",
    "app", "application", "system", "software", "platform", "website", "web",
    "site", "page", "tool", "service", "solution", "product", "project",
    "shop", "store", "ecommerce", "commerce", "market", "marketplace",
    "manage", "management", "track", "tracker", "tracking", "monitor",
    "book", "booking", "order", "orders", "inventory", "stock", "report",
    "reports", "analytics", "dashboard", "payment", "payments", "invoice",
    "user", "users", "online", "sell", "buy", "build", "want", "need", "create",
    "customer", "customers", "client", "admin", "staff", "portal", "data",
    "hotel", "school", "student", "clinic", "patient", "vehicle", "car",
    "restaurant", "delivery", "event", "fitness", "gym", "real", "estate",
    "property", "rental", "rent", "social", "blog", "learning", "course",
}


_MASH = ("asd", "sdf", "dfg", "fgh", "ghj", "hjk", "jkl", "qwe", "qwer",
         "zxc", "xcv", "cvb", "vbn", "bnm", "qaz", "wsx", "edc", "rfv",
         "tgb", "yhn", "ujm", "poi", "oiu", "iuy", "mnb", "lkj", "kjh")


def _plausible_word(w: str) -> bool:
    """True if an unknown token still looks like a real word (not mash)."""
    if not any(c in "aeiou" for c in w):
        return False
    if any(m in w for m in _MASH):
        return False

    if len(w) >= 6 and w.count(w[:3]) >= 2:
        return False
    if re.search(r"(.)\1\1", w):
        return False
    vowel_ratio = sum(1 for c in w if c in "aeiou") / len(w)
    return 0.15 <= vowel_ratio <= 0.85 and len(w) <= 20


def looks_like_nonsense(text: str) -> tuple[bool, str]:
    """Heuristic gate. Returns (is_nonsense, reason)."""
    t = (text or "").strip()
    if len(t) < 8:
        return True, "The idea is too short to understand."

    words = _WORD_RE.findall(t.lower())
    if len(words) < 3:
        return True, "The idea does not contain enough real words."

    recognized = [w for w in words if w in _COMMON]
    plausible = [w for w in words if w not in _COMMON and _plausible_word(w)]
    ratio = (len(recognized) + len(plausible)) / max(len(words), 1)

    if not recognized and ratio < 0.5:
        return True, "The idea looks like random characters rather than a software description."
    if ratio < 0.4:
        return True, "The idea looks like random characters rather than a software description."
    if len(set(words)) <= 2 and len(words) <= 3 and not recognized:
        return True, "The idea looks like placeholder text."

    return False, ""


def _content_only(brief: str) -> str:
    """Strip brief section labels (e.g. 'USER IDEA:') before nonsense scoring."""
    out: list[str] = []
    for ln in (brief or "").splitlines():
        s = ln.strip()
        if not s or s == "---":
            continue
        if re.match(r"^[A-Z][A-Z0-9 ()._\-]*:$", s):
            continue
        out.append(s)
    return " ".join(out)


def detect_language(text: str) -> str:
    for ch in text:
        o = ord(ch)
        if 0x0D80 <= o <= 0x0DFF:
            return "Sinhala"
        if 0x0B80 <= o <= 0x0BFF:
            return "Tamil"
        if 0x0600 <= o <= 0x06FF:
            return "Arabic"
        if 0x4E00 <= o <= 0x9FFF:
            return "Chinese"
    return "English"


async def intake_node(state: AgentState) -> AgentState:
    pid = state["project_id"]
    brief = (state.get("brief") or state.get("raw_idea") or "").strip()
    await bus.log(pid, "IntakeExtractorAgent", "Reading your idea", progress=10)

    language = state.get("language") or detect_language(brief)
    is_nonsense, reason = looks_like_nonsense(_content_only(brief))

    if is_nonsense:
        await bus.emit(
            pid, "IntakeExtractorAgent",
            "Input is too vague to generate an SRS — asking for clarification.",
            level="warn", progress=20,
        )
    else:
        await bus.emit(pid, "IntakeExtractorAgent", "Idea understood; cleaning and normalising.", progress=20)

    return {
        **state,
        "brief": brief,
        "language": language,
        "is_nonsense": is_nonsense,
        "needs_clarification": is_nonsense,
        "clarification_reason": reason,
    }
