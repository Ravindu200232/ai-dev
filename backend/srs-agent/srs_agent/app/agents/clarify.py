"""What did the customer actually mean, and why do they want it."""
from __future__ import annotations

import json
import logging
import re

from ..llm import LLMRepairFailed, LLMUnavailable, get_llm
from ..services.events import bus
from .language import localize_question_payload, output_language_instruction

log = logging.getLogger("agentforge.clarify")


CONFIRMED = "confirmed"
NEEDS_MEANING = "needs_meaning_confirmation"
NEEDS_PURPOSE = "needs_purpose"
NEEDS_PURPOSE_CONFIRMATION = "needs_purpose_confirmation"
DUPLICATE = "duplicate"
REJECTED = "rejected"

OPEN_STATUSES = (NEEDS_MEANING, NEEDS_PURPOSE, NEEDS_PURPOSE_CONFIRMATION)


MAX_ATTEMPTS = 3


MAX_SUGGESTIONS = 3

KEEP_ORIGINAL = "__keep_original__"
TYPE_ANOTHER = "__type_another__"

PREFIX = "clarify:"

SYSTEM = """You interpret short answers written by a NON-TECHNICAL customer \
describing an app they want built.

Return ONLY a JSON object:
{
  "clear": true or false,
  "meaning": "one plain sentence saying what they are asking for",
  "suggestions": [{"label": "a specific reading of their words", "value": "machine_value"}],
  "duplicate_of": "the exact text of an existing item this repeats, or null",
  "reject_reason": "why this is not a requirement at all, or null"
}

Rules:
- "clear" is true only if there is exactly one sensible reading of their words.
- At most 3 suggestions. Each must be a different reading of what THEY wrote — \
never a generic feature, never something they did not mention.
- "duplicate_of" is only set when the new text means the same thing as an \
existing item, not merely something related.
- "reject_reason" is only for input that is not a request at all (random \
characters, a greeting, a question aimed at you).
- Plain language. No jargon: say "records" not "entities", "log in" not \
"authentication".
- Never invent scope. If they wrote two words, do not return a paragraph."""

PURPOSE_SYSTEM = """You check whether a customer has explained WHY they want \
something in their app.

Return ONLY a JSON object:
{"sufficient": true or false, "restated": "their reason in one plain sentence", \
"missing": "what is still unclear, or null"}

"sufficient" is true when the answer says what the feature is FOR — who uses it \
or what problem it solves. It does not need detail, only a real reason. \
"I just want it" and "because" are not reasons. Restate what they said; never \
add a reason they did not give."""


_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)


_FILLER = {"yes", "no", "ok", "okay", "none", "n/a", "na", "idk", "dunno",
           "maybe", "sure", "any", "anything", "all", "other", "etc",
           "nothing", "nope", "not sure", "no idea", "don't know"}


def is_self_explanatory(text: str, options=None) -> bool:
    """Can this answer be taken at face value?"""
    raw = str(text or "").strip()
    if not raw:
        return True

    for option in options or []:
        label = option.get("label") if isinstance(option, dict) else option
        value = option.get("value") if isinstance(option, dict) else option
        for candidate in (label, value):
            if candidate and raw.casefold() == str(candidate).casefold():
                return True

    if raw.casefold().strip(" .!?,") in _FILLER:
        return True

    words = _WORD.findall(raw)
    if not words:
        return False

    return len(words) >= 3


def needs_clarification(text: str, options=None) -> bool:
    """The inverse, named the way the caller thinks about it."""
    return bool(str(text or "").strip()) and not is_self_explanatory(text, options)


async def start(question_id: str, raw: str, *, topic: str = "", question: str = "",
                existing=None, options=None, project_id: str = "",
                context: str = "", language: str = "English") -> dict:
    """Open a clarification for one typed answer."""
    state = {
        "question_id": question_id,
        "topic": topic or question_id.split(":", 1)[0],
        "question": question,
        "project_id": project_id,
        "language": language or "English",

        "raw": str(raw or "").strip(),
        "status": CONFIRMED,
        "meaning": str(raw or "").strip(),
        "meaning_confirmed": True,
        "purpose": "",
        "purpose_confirmed": True,
        "suggestions": [],
        "duplicate_of": "",
        "attempts": 0,
        "history": [],
    }

    if is_self_explanatory(raw, options):
        return state

    state.update({
        "status": NEEDS_MEANING,
        "meaning": "",
        "meaning_confirmed": False,
        "purpose_confirmed": False,
    })
    return await _interpret_into(state, existing, project_id, context, language)


async def _interpret_into(state: dict, existing=None, project_id: str = "",
                          context: str = "", language: str = "English") -> dict:
    """Ask the model what the text might mean, and fold the reading in."""
    data = await _interpret(state["raw"], state.get("question", ""), existing,
                            project_id, context, language)

    duplicate = str(data.get("duplicate_of") or "").strip()
    if duplicate and _matches_existing(duplicate, existing):

        state["status"] = DUPLICATE
        state["duplicate_of"] = _matches_existing(duplicate, existing)
        state["meaning"] = state["duplicate_of"]
        state["meaning_confirmed"] = True
        state["purpose_confirmed"] = True
        return state

    reason = str(data.get("reject_reason") or "").strip()
    if reason:
        state["attempts"] += 1
        state["history"].append({"stage": "meaning", "rejected": reason})
        if state["attempts"] >= MAX_ATTEMPTS:

            state["status"] = NEEDS_PURPOSE
            state["meaning"] = state["raw"]
            state["meaning_confirmed"] = True
            return state
        state["status"] = REJECTED
        state["reject_reason"] = reason
        state["suggestions"] = []
        return state

    suggestions = _clean_suggestions(data.get("suggestions"), data.get("meaning"))
    if not suggestions:

        state["status"] = CONFIRMED
        state["meaning"] = state["raw"]
        state["meaning_confirmed"] = True
        state["purpose_confirmed"] = True
        return state

    state["status"] = NEEDS_MEANING
    state["suggestions"] = suggestions
    return state


async def _interpret(raw: str, question: str, existing=None,
                     project_id: str = "", context: str = "",
                     language: str = "English") -> dict:
    known = [str(e) for e in (existing or []) if str(e).strip()][:20]

    preamble = f"{context}\n\n" if context else ""
    user = f"""{preamble}The customer was asked: {question or '(a question about their app)'}

They typed this answer, exactly as written:
\"\"\"{raw}\"\"\"

Things they have already asked for:
{json.dumps(known, ensure_ascii=False) if known else "(nothing yet)"}

Interpret their answer now, in the context of the app they are describing."""

    try:
        data = await get_llm().complete_json(
            system=SYSTEM + output_language_instruction(
                language, artifact="clarification response"),
            user=user, label="clarify_meaning",
            trace_sink=(lambda p: bus.trace(project_id, p)) if project_id else None,
        )
    except (LLMUnavailable, LLMRepairFailed) as exc:
        log.info("clarify: no interpretation for %r (%s)", raw[:60], exc)
        return {}
    except Exception:  # noqa: BLE001
        log.warning("clarify: interpretation failed for %r", raw[:60], exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _clean_suggestions(raw_suggestions, meaning) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for item in (raw_suggestions or []):
        label = (item.get("label") if isinstance(item, dict) else item) or ""
        label = str(label).strip()
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        value = str((item.get("value") if isinstance(item, dict) else "")
                    or label).strip()
        out.append({"label": label, "value": value})
        if len(out) >= MAX_SUGGESTIONS:
            break

    if not out and str(meaning or "").strip():
        out.append({"label": str(meaning).strip(), "value": str(meaning).strip()})
    return out


async def question(state: dict) -> dict | None:
    """The question payload for whichever step this state is waiting on."""
    status = state.get("status")
    if status not in OPEN_STATUSES and status != REJECTED:
        return None

    key = f"{PREFIX}{state['question_id']}"
    base = {
        "id": key,
        "key": key,
        "topic": state.get("topic", ""),
        "subject": None,
        "clarify": True,
        "status": status,
        "raw": state["raw"],
        "why_needed": "Confirming what you typed before it becomes a requirement.",
        "maps_to_srs_fields": [],
        "coverage_areas": [],
        "required": False,
        "recommended": None,
        "placeholder": "",

        "prefill": [],
        "prefill_note": "",

        "counts_toward_total": False,
        "index": 0,
        "total": 0,
    }

    if status == NEEDS_MEANING:
        options = list(state.get("suggestions") or [])
        options.append({"label": f"Keep my original answer: “{state['raw']}”",
                        "value": KEEP_ORIGINAL})
        options.append({"label": "Let me type it another way",
                        "value": TYPE_ANOTHER})
        payload = _shape(base, "single",
                         f"You wrote “{state['raw']}”. Which of these did you mean?",
                         options)
        return await localize_question_payload(
            payload, state.get("language", "English"),
            project_id=state.get("project_id", ""),
        )

    if status == REJECTED:
        payload = _shape(base, "text",
                         f"We could not tell what “{state['raw']}” should do in your "
                         "app. Could you say it another way?",
                         [], placeholder="What should it do?")
        return await localize_question_payload(
            payload, state.get("language", "English"),
            project_id=state.get("project_id", ""),
        )

    if status == NEEDS_PURPOSE:
        payload = _shape(base, "text", f"What is “{state['meaning']}” for?", [],
                         placeholder="Who uses it, or what problem it solves")
        return await localize_question_payload(
            payload, state.get("language", "English"),
            project_id=state.get("project_id", ""),
        )

    payload = _shape(base, "yes_no",
                     f"So: {state['meaning']} — {state['purpose']}. "
                     "Have we got that right?",
                     [{"label": "Yes, that's right", "value": True},
                      {"label": "No, let me explain again", "value": False}])
    return await localize_question_payload(
        payload, state.get("language", "English"),
        project_id=state.get("project_id", ""),
    )


_ANSWER_TYPE = {"single": "single_choice", "text": "free_text", "yes_no": "yes_no"}


def _shape(base: dict, kind: str, text: str, options: list,
           placeholder: str = "") -> dict:
    return {
        **base,
        "kind": kind,
        "answer_type": _ANSWER_TYPE[kind],
        "question": text,
        "options": options,
        "suggested_options": [str(o.get("label", o)) for o in options],
        "placeholder": placeholder,
    }


async def answer(state: dict, value=None, text: str = "", *, existing=None,
                 project_id: str = "", context: str = "") -> dict:
    """Advance one step. Returns the same state, mutated."""
    status = state.get("status")
    typed = str(text or "").strip()

    if status == NEEDS_MEANING:
        return await _answer_meaning(state, value, typed, existing, project_id, context)
    if status == REJECTED:
        return await _retry_meaning(state, typed, existing, project_id, context)
    if status == NEEDS_PURPOSE:
        return await _answer_purpose(state, typed, project_id, context)
    if status == NEEDS_PURPOSE_CONFIRMATION:
        return _answer_purpose_confirmation(state, value, typed)
    return state


def _clean_answer(value, question_id: str, raw: str = "") -> str:
    """The bare answer out of a suggestion's machine value."""
    text = str(value or "").strip()
    key = str(question_id or "").strip()

    qualified = False
    if key:
        for sep in (":", "_", "-"):
            prefix = f"{key.lower()}{sep}"
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                qualified = True
                break

    text = text.strip("\"'“”‘’ ").strip()

    if qualified and raw and _looks_machine_made(text):
        return str(raw).strip()
    return text


def _looks_machine_made(text: str) -> bool:
    """A slug: no spaces, and joined up the way a machine writes an identifier."""
    return bool(text) and " " not in text and ("_" in text or "-" in text)


async def _answer_meaning(state, value, typed, existing, project_id, context=""):
    choice = "" if value is None else str(value)

    if choice == TYPE_ANOTHER or (not choice and typed):
        return await _retry_meaning(state, typed, existing, project_id, context)

    if choice == KEEP_ORIGINAL or not choice:
        state["meaning"] = state["raw"]
        state["answer"] = state["raw"]
    else:
        hit = next((s for s in state.get("suggestions") or []
                    if choice in (s.get("value"), s.get("label"))), None)
        state["meaning"] = (hit or {}).get("label") or typed or choice

        state["answer"] = _clean_answer(
            (hit or {}).get("value") or (hit or {}).get("label")
            or typed or choice,
            state.get("question_id", ""),
            typed or state.get("raw", ""))

    state["meaning_confirmed"] = True
    state["status"] = NEEDS_PURPOSE
    state["history"].append({"stage": "meaning", "chose": state["meaning"]})
    return state


async def _retry_meaning(state, typed, existing, project_id, context=""):
    """They want to say it differently."""
    if not typed:
        return state

    state["history"].append({"stage": "meaning", "retyped": typed})

    probe = dict(state)
    probe["raw"] = typed
    result = await _interpret_into(
        probe, existing, project_id, context, state.get("language", "English"),
    )

    for field in ("status", "attempts", "suggestions", "duplicate_of",
                  "meaning", "meaning_confirmed", "purpose_confirmed"):
        state[field] = result.get(field, state.get(field))
    if result.get("reject_reason"):
        state["reject_reason"] = result["reject_reason"]
    else:
        state.pop("reject_reason", None)
    return state


async def _answer_purpose(state, typed, project_id, context=""):
    if not typed:
        return state

    state["attempts"] += 1
    verdict = await _check_purpose(
        state["meaning"], typed, project_id, context,
        state.get("language", "English"),
    )

    if not verdict.get("sufficient") and state["attempts"] < MAX_ATTEMPTS:
        state["history"].append({"stage": "purpose", "missing":
                                 verdict.get("missing") or "no reason given"})
        state["purpose"] = typed
        state["status"] = NEEDS_PURPOSE
        return state

    state["purpose"] = str(verdict.get("restated") or typed).strip() or typed
    state["status"] = NEEDS_PURPOSE_CONFIRMATION
    state["history"].append({"stage": "purpose", "said": state["purpose"]})
    return state


async def _check_purpose(meaning: str, purpose: str, project_id: str,
                         context: str = "", language: str = "English") -> dict:
    preamble = f"{context}\n\n" if context else ""
    try:
        data = await get_llm().complete_json(
            system=PURPOSE_SYSTEM + output_language_instruction(
                language, artifact="clarification response"),
            user=f"{preamble}They want: {meaning}\nTheir reason: \"\"\"{purpose}\"\"\"",
            label="clarify_purpose",
            trace_sink=(lambda p: bus.trace(project_id, p)) if project_id else None,
        )
    except Exception:  # noqa: BLE001

        return {"sufficient": True, "restated": purpose}
    if not isinstance(data, dict):
        return {"sufficient": True, "restated": purpose}
    return data


def _answer_purpose_confirmation(state, value, typed):
    if value in (True, "true", "yes", 1):
        state["purpose_confirmed"] = True
        state["status"] = CONFIRMED
        return state

    state["purpose_confirmed"] = False
    state["purpose"] = typed or ""
    state["status"] = NEEDS_PURPOSE
    state["history"].append({"stage": "purpose", "rejected": True})
    return state


def is_ready(state: dict) -> bool:
    """Both halves confirmed. Nothing may become a requirement before this."""
    if state.get("status") == DUPLICATE:
        return True
    return (state.get("status") == CONFIRMED
            and bool(state.get("meaning_confirmed"))
            and bool(state.get("purpose_confirmed")))


def resolve(state: dict) -> dict | None:
    """The confirmed requirement, or None while anything is still open."""
    if not is_ready(state):
        return None
    if state.get("status") == DUPLICATE:
        return {
            "question_id": state["question_id"],
            "text": state["duplicate_of"],
            "raw": state["raw"],
            "purpose": state.get("purpose", ""),
            "merged_into": state["duplicate_of"],
        }
    return {
        "question_id": state["question_id"],
        "text": state["meaning"] or state["raw"],

        "answer": state.get("answer") or state["meaning"] or state["raw"],
        "raw": state["raw"],
        "purpose": state.get("purpose", ""),
        "merged_into": "",
    }


def pending(session: dict) -> dict | None:
    """The first clarification still waiting on the customer."""
    for state in (session.get("clarifications") or {}).values():
        if state.get("status") in OPEN_STATUSES or state.get("status") == REJECTED:
            return state
    return None


def _matches_existing(text: str, existing) -> str:
    """The existing item this text names, or "" — compared loosely on words."""
    target = _key(text)
    if not target:
        return ""
    for item in existing or []:
        label = str(item)
        if _key(label) == target:
            return label
    return ""


def _key(text: str) -> str:
    return " ".join(sorted(w.casefold() for w in _WORD.findall(str(text or ""))))
