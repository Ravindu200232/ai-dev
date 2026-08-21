"""Customer wording formatted for agent prompts."""
from __future__ import annotations

IDEA_LIMIT = 3000
TYPED_LIMIT = 1500


ATTACHED_LIMIT = 2500


def typed_so_far(session: dict | None) -> list[str]:
    """Every sentence the customer typed themselves, oldest first."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in ((session or {}).get("answers") or {}).values():
        if not isinstance(entry, dict):
            continue
        for raw in (entry.get("custom_text"), entry.get("text")):
            text = str(raw or "").strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                out.append(text)
    return out


def attached_so_far(session: dict | None) -> list[str]:
    """`filename: text` for every file they attached to an answer."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in ((session or {}).get("answers") or {}).values():
        if not isinstance(entry, dict):
            continue
        for att in entry.get("attachments") or []:
            text = str((att or {}).get("text") or "").strip()
            name = str((att or {}).get("filename") or "attachment")
            if text and text.lower() not in seen:
                seen.add(text.lower())
                out.append(f"{name}: {text}")
    return out


def customer_context(brief: str = "", session: dict | None = None,
                     project: dict | None = None) -> str:
    """The idea and everything typed since, or "" when we know nothing yet."""
    idea = str(brief
               or (session or {}).get("raw_idea")
               or (project or {}).get("raw_idea") or "").strip()
    typed = typed_so_far(session)
    attached = attached_so_far(session)
    if not idea and not typed and not attached:
        return ""

    parts: list[str] = ["THE CUSTOMER'S OWN WORDS — this is their app, and "
                        "their vocabulary is the one to answer in:"]
    if idea:
        parts.append(f'"""\n{idea[:IDEA_LIMIT]}\n"""')
    if typed:
        block = "\n".join(f"- {t}" for t in typed)
        parts.append("What they have typed themselves since:\n"
                     + block[:TYPED_LIMIT])
    if attached:
        block = "\n".join(f"- {a}" for a in attached)
        parts.append("From files they attached to their answers (a photo, a "
                     "PDF or something they said out loud — treat it as them "
                     "talking):\n" + block[:ATTACHED_LIMIT])
    return "\n\n".join(parts)


__all__ = ["customer_context", "typed_so_far", "attached_so_far",
           "IDEA_LIMIT", "TYPED_LIMIT", "ATTACHED_LIMIT"]
