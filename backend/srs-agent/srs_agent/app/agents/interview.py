"""InterviewAgent — the per-question LLM call."""
from __future__ import annotations

import json
import logging
import re

from ..knowledge import app_types as catalog
from ..knowledge import topics
from ..llm import LLMRepairFailed, LLMUnavailable, get_llm
from ..services.events import bus
from .customer_context import customer_context

log = logging.getLogger("agentforge.interview")

SYSTEM = """You are a senior requirements engineer interviewing a NON-TECHNICAL customer before a product is built.

Your job is to uncover what they actually need, not to fill a questionnaire. Ask ONE decision-focused question at a time in plain language. Use the customer's own domain words. Avoid implementation jargon unless they used it first.

Return ONLY a JSON object:
{
  "question": "one clear question",
  "why_needed": "one short sentence explaining what this decision changes",
  "options": [{"label": "short answer", "value": "machine_value", "hint": "optional 3-5 words"}],
  "recommended": "machine_value of a low-risk default, or null",
  "known": ["answer values already stated by the customer"],
  "known_quote": "their exact words that support the known answer"
}

Interview standard:
- Ask about outcomes and behaviour before screens or implementation.
- For a workflow decision, make the trigger, actor, expected result, business rule or exception clear enough that a developer and tester would reach the same interpretation.
- Options must be realistic alternatives for THIS product. Prefer mutually exclusive choices for single-select questions and atomic choices for multi-select questions.
- Do not invent scope to make the interview look complete. If the customer has not said it, ask or leave it open.
- Do not lead the customer toward a feature they did not request. Set "recommended" only for a conventional, reversible, low-risk default.
- If their earlier words already answer the topic, do not make them repeat themselves. Ask a concise confirmation question, place the matching values in "known", and quote the supporting words in "known_quote".
- For an options question, every "known" value must exactly match one option value. For text/number questions, return the answer as a person would type it, not snake_case.
- For yes/no questions use exactly two options with values true and false.
- Keep each option atomic: one role, one field, one payment method, one rule. Never bundle unrelated decisions into one option.
- Never ask whether the customer has or wants a logo. Logo generation is handled only when they explicitly request logo artwork.
- Do not ask about navbar/sidebar placement, fonts, themes, colours or layout unless the current topic explicitly exists to capture a customer-owned visual requirement.
- Keep the question natural and specific. It should sound like an experienced engineer who has read everything the customer already said.
"""


ANSWER_TYPE = {
    "single": "single_choice",
    "multi": "multi_choice",
    "yes_no": "yes_no",
    "number": "number",
    "text": "free_text",
    "upload": "single_choice",
}


ATTACHMENT_STORE_LIMIT = 4000
ATTACHMENT_DIGEST_LIMIT = 700


# Ignore yes/no words as app names.
AFFIRMATIONS = {
    "y", "ya", "yah", "yeah", "yep", "yes", "yup", "yes please", "yes thanks",
    "ok", "okay", "oky", "k", "sure", "fine", "good", "great", "perfect",
    "correct", "right", "thats right", "that is right", "exactly", "exact",
    "true", "confirm", "confirmed", "agreed", "agree", "keep it", "use that",
    "thats it", "that is it", "same", "as it is",
}
NEGATIONS = {
    "n", "no", "nope", "nah", "no thanks", "not", "not really", "not exactly",
    "not that", "wrong", "incorrect", "negative", "false", "change it",
}

_BARE_RE = re.compile(r"[^a-z0-9 ]+")


def _bare_word(text) -> str:
    """An answer reduced to plain lowercase words, for matching against the sets."""
    return _BARE_RE.sub("", str(text or "").lower()).strip()


def _confirmable(session: dict, key: str, topic_key: str) -> str:
    """The candidate the question on screen quoted back, or ""."""
    topic = topics.TOPICS_BY_KEY.get(topic_key)
    if not topic or topic.kind not in ("text", "number"):
        return ""
    current = session.get("current") or {}
    if str(current.get("key") or current.get("id") or "") != key:
        return ""
    prefill = current.get("prefill") or []
    return str(prefill[0]).strip() if prefill else ""


def _attachment_entries(attachments) -> list[dict]:
    """Uploaded sources, trimmed to what an answer needs to carry."""
    out = []
    for src in attachments or []:
        if not isinstance(src, dict):
            continue
        text = str(src.get("text") or "").strip()
        out.append({
            "id": str(src.get("id") or ""),
            "filename": str(src.get("filename") or "attachment"),
            "mode": str(src.get("mode") or ""),
            "text": text[:ATTACHMENT_STORE_LIMIT],
        })
    return out


def attachment_texts(entry: dict) -> list[str]:
    """`filename: extracted text` for every attachment on one answer."""
    return [f"{a['filename']}: {a['text']}"
            for a in (entry.get("attachments") or []) if a.get("text")]


def _answers_digest(session: dict) -> str:
    """Compact transcript of what we already know."""
    lines = []
    for key, entry in (session.get("answers") or {}).items():
        val = entry.get("value")
        if isinstance(val, (list, dict)):
            val = json.dumps(val, ensure_ascii=False)
        line = f"- {key}: {val}"
        typed = str(entry.get("custom_text") or entry.get("text") or "").strip()
        if typed and typed not in str(val):
            line += f"  (they typed: {typed})"
        lines.append(line)
        for att in attachment_texts(entry):
            lines.append(f"    (they attached {att[:ATTACHMENT_DIGEST_LIMIT]})")
    return "\n".join(lines) or "(nothing yet)"


def _suggested_options(topic: topics.Topic, item: dict, session: dict) -> list:
    """The topic's own fixed options, if it has any."""
    if not topic.options_from:
        return []
    try:
        return topic.options_from(session, item) or []
    except Exception:  # noqa: BLE001 - a template gap must not break the interview
        log.warning("options_from failed for %s", topic.key, exc_info=True)
        return []


def _topic_brief(topic: topics.Topic, item: dict, session: dict) -> str:
    subject = item.get("subject")
    lines = [
        f"Topic: {topic.key}",
        f"What we need: {topic.intent}",
        f"Answer style: {topic.kind}",
    ]
    if subject:
        lines.append(f"This question is specifically about: {subject}")
    suggested = _suggested_options(topic, item, session)
    if suggested:
        labels = [str(o.get("label", o)) for o in suggested][:12]
        lines.append(f"Reasonable options to draw from: {', '.join(labels)}")
    return "\n".join(lines)


async def ask(session: dict) -> dict | None:
    """Build the next question."""
    queue = topics.build_queue(session)
    if not queue:
        return None

    item = queue[0]
    topic = topics.TOPICS_BY_KEY[item["topic"]]
    total = topics.total_estimate(session)
    index = len([k for k in session.get("asked", [])
                 if not topics.is_clarification(k)]) + 1
    pid = session.get("project_id", "")

    if topic.key == "app_type":
        return _question(item, topic, index, total,
                         question="Which description best matches what you want people to use this product for?",
                         options=(_suggested_options(topic, item, session)
                                  or catalog.app_type_options()),
                         recommended=session.get("guessed_app_type"))

    if topic.fixed:
        return _question(item, topic, index, total,
                         question=topic.fixed,
                         options=list(topic.fallback_options))

    pack = session.get("pack") or {}
    user_msg = f"""{customer_context(session=session)}

Detected app type: {pack.get('app_label', 'unknown')} ({pack.get('archetype', '')})
Detected business domain: {pack.get('domain_label', 'general')}

What the customer has told us so far:
{_answers_digest(session)}

{_topic_brief(topic, item, session)}

Ask the next question now. Every option you offer must read as though you had
their idea in front of you — name their trade, their goods, their people. If
their own words above already answer this, ask it back for confirmation and
fill in "known" — never make them tell you something twice."""

    data: dict | None = None
    try:
        data = await get_llm().complete_json(
            system=SYSTEM, user=user_msg,
            label=f"interview:{topic.key}",
            trace_sink=(lambda p: bus.trace(pid, p)) if pid else None,
        )
    except (LLMUnavailable, LLMRepairFailed) as exc:
        log.info("interview: LLM unusable for %s (%s)", topic.key, exc)
    except Exception:  # noqa: BLE001
        log.warning("interview: LLM error for %s", topic.key, exc_info=True)

    suggested = _suggested_options(topic, item, session)
    default_options = suggested or topic.fallback_options

    if not isinstance(data, dict) or not data.get("question"):
        return _question(item, topic, index, total,
                         question=_fallback_question(topic, item),
                         options=default_options)

    if topic.options_locked:
        options = default_options
    else:
        options = [o for o in (data.get("options") or [])
                   if isinstance(o, dict) and o.get("label")]
        if not options and topic.kind in ("single", "multi", "yes_no"):
            options = default_options

    return _question(
        item, topic, index, total,
        question=str(data["question"]),
        options=options,
        recommended=data.get("recommended"),
        prefill=_known_values(data.get("known"), options, topic),
        prefill_note=str(data.get("known_quote") or "").strip()[:240],
        why_needed=str(data.get("why_needed") or "").strip(),
    )


def _known_values(known, options: list, topic: topics.Topic) -> list:
    """The answer their own words already gave, ready to be confirmed."""
    if not isinstance(known, list) or not known:
        return []

    if topic.kind in ("text", "number"):
        first = str(known[0]).strip()[:200]
        if topic.kind == "number":
            try:
                float(first)
            except ValueError:
                return []
        elif "_" in first and " " not in first:

            first = first.replace("_", " ")
        return [first] if first else []

    allowed = {str(o.get("value")): o.get("value") for o in options
               if isinstance(o, dict)}
    out: list = []
    for value in known:
        real = allowed.get(str(value))
        if real is not None and real not in out:
            out.append(real)
    return out if topic.kind == "multi" else out[:1]


def _question(item, topic, index, total, question, options,
              recommended=None, prefill=None, prefill_note="", why_needed=""):
    """One question, in both shapes at once."""
    options = options or []
    return {

        "id": item["key"],
        "question": question,
        "why_needed": (str(why_needed or "").strip() or _why_needed(topic, item))[:240],
        "answer_type": ANSWER_TYPE.get(topic.kind, "single_choice"),
        "suggested_options": [str(o.get("label", o)) for o in options],
        "maps_to_srs_fields": list(topic.srs_fields),
        "coverage_areas": list(topic.coverage),
        "required": not topic.optional,

        "key": item["key"],
        "topic": topic.key,
        "subject": item.get("subject"),
        "kind": topic.kind,
        "index": index,
        "total": max(total, index),
        "options": options,
        "recommended": recommended,
        "placeholder": topic.placeholder,

        "optional": topic.optional,
        "multiline": topic.multiline,

        "prefill": prefill or [],
        "prefill_note": prefill_note,
    }


def _why_needed(topic: topics.Topic, item: dict) -> str:
    subject = item.get("subject")
    reasons = {
        "app_type": "This sets the right requirement pattern without forcing features you do not need.",
        "app_name": "This keeps the specification, screens and generated project named consistently.",
        "core_outcome": "This becomes the main success path the build and end-to-end tests must prove.",
        "auth_roles": "This defines who can use which parts of the product.",
        "auth_identity": "This defines the information needed to create and access an account.",
        "account_creation": "This decides who may enter the system and how new accounts are controlled.",
        "role_functions": f"This defines exactly what {subject or 'this role'} may and may not do.",
        "page_functions": "This defines the actions a public visitor must be able to complete.",
        "data_tables": "This determines the business information that must persist between visits.",
        "table_entities": f"These details define what one {subject or 'record'} contains and what forms and validation need.",
        "images": "This tells the builder whether artwork is part of the requested scope.",
        "image_kinds": "This names the exact artwork assets the builder must create.",
        "responsive_pwa": "This sets the device and installation expectations the finished product must meet.",
        "extra_notes": "This is the final chance to capture a rule, exception or expectation not covered earlier.",
    }
    return reasons.get(topic.key, topic.intent)


def _fallback_question(topic: topics.Topic, item: dict) -> str:
    subject = item.get("subject")
    base = {
        "app_type": "Which description best matches the product you want to build?",
        "app_name": "What name should appear on the product and in the specification?",
        "core_outcome": "What is the single most important result a user must be able to achieve from start to finish?",
        "auth_roles": "Who are the distinct kinds of people who will use this product?",
        "auth_identity": "What information should a person use to sign in?",
        "role_functions": (f"For {subject}, which actions should be allowed in day-to-day use?" if subject
                           else "Which actions should this role be allowed to perform?"),
        "page_functions": "What must a visitor be able to do without signing in?",
        "data_tables": "Which kinds of business information must still be there when someone comes back later?",
        "table_entities": (f"What information must be stored for one {subject}?" if subject
                           else "What information must be stored for one record?"),
        "pos_payments": "Which payment methods must a cashier be able to accept during a sale?",
        "pos_receipt": "After a sale is completed, what receipt or confirmation should the customer receive?",
        "pos_stock_rules": "When a sale or stock change happens, which inventory rules must the system enforce automatically?",
        "saas_plans": "Which subscription levels must the product support at launch?",
        "saas_signup": "How should a new person gain access to a workspace?",
        "saas_workspace": "Should each account keep its own data, or should a team share one workspace?",
        "store_checkout": "At checkout, should a shopper be able to buy without creating an account?",
        "store_payments": "Which payment methods must checkout support?",
        "store_fulfilment": "After payment, how should the order reach the customer?",
        "dash_metrics": "Which numbers or signals must someone see first to know what needs attention?",
        "dash_exports": "When someone needs to take data out of the dashboard, which export formats must be available?",
        "blog_workflow": "From draft to public, which publishing steps must a post go through?",
        "blog_organisation": "How should a reader find the right post when the library grows?",
        "blog_engagement": "Besides reading, which actions should a visitor be able to take on a post?",

        "sections": "Which sections must a visitor see as they move from the top of the page to the main call to action?",
        "offerings": "Which specific services, packages or pieces of work must the page present?",
        "cta": "What is the single action you most want a visitor to complete?",
        "lead_capture": "Should the site save enquiries so the business can review and follow them up later?",
        "lead_fields": "What information is genuinely needed to respond to an enquiry?",
        "contact_channels": "Which direct contact methods must be available if someone does not use the form?",
        "social_proof": "What evidence should the page show to help a new visitor trust the business?",

        "tool_job": "What exact job should someone be able to finish with this tool?",
        "tool_inputs": "What information must a person provide before the tool can produce a correct result?",
        "tool_outputs": "What result must the tool return so the user can act on it?",
        "save_history": "Should someone be able to return later and see previous results?",
        "images": "Does the build need original generated artwork, or can it work without generated images?",
        "image_kinds": "Which original image assets should be produced as part of the build?",
        "theme_type": "Light or dark?",
        "color_palette": "Which colours suit your brand?",
        "responsive_pwa": "Which devices and install modes must the finished product support?",
        "extra_notes": "Before I write the specification, is there any rule, exception, must-have behaviour or failure case that would make the finished product unacceptable if we missed it?",
    }
    return base.get(topic.key, f"Tell us about {topic.label.lower()}.")


def record(session: dict, key: str, value=None, text: str = "", images=None,
           selected=None, custom: str = "", attachments=None) -> dict:
    """Store an answer and re-derive anything that depends on it."""
    topic_key = key.split(":", 1)[0]

    if selected is None and custom == "":

        selected = value if isinstance(value, list) else (
            [] if value is None else [value])
        custom = text or ""

    entry = {
        "topic": topic_key,
        "value": value if value is not None else text,
        "text": text,
        "images": images or [],
        "question_id": key,
        "selected_values": list(selected or []),
        "custom_text": str(custom or "").strip(),
        "attachments": _attachment_entries(attachments),
        "details": {},
    }

    candidate = _confirmable(session, key, topic_key)
    if candidate and not isinstance(entry["value"], (list, dict)):
        said = _bare_word(entry["value"])
        if said in AFFIRMATIONS:
            entry["details"]["confirmed"] = said
            entry["value"] = candidate
            entry["text"] = candidate
            entry["custom_text"] = candidate
            entry["selected_values"] = [candidate]
        elif said in NEGATIONS:
            entry["details"]["declined"] = said
            entry["value"] = ""
            entry["text"] = ""
            entry["custom_text"] = ""
            entry["selected_values"] = []

    picked = [s for s in entry["selected_values"] if str(s).strip()]
    if (entry["attachments"] and not picked
            and not str(entry["value"] or "").strip()):
        read = " ".join(a["text"] for a in entry["attachments"] if a["text"]).strip()
        names = ", ".join(a["filename"] for a in entry["attachments"])
        summary = read[:ATTACHMENT_STORE_LIMIT] or f"(attached {names}; no text could be read)"
        kind = getattr(topics.TOPICS_BY_KEY.get(topic_key), "kind", "text")
        entry["value"] = [summary] if kind == "multi" else summary

    session.setdefault("answers", {})[key] = entry
    if key not in session.setdefault("asked", []):
        session["asked"].append(key)

    if topic_key == "app_type":
        chosen = entry["value"]
        if isinstance(chosen, list):
            chosen = chosen[0] if chosen else catalog.DEFAULT_APP_TYPE
        chosen = str(chosen)
        if chosen not in catalog.APP_TYPES:
            # Map typed answers back to a known category.
            guess, confidence = catalog.guess_app_type(
                f"{chosen} {session.get('raw_idea', '')}")
            chosen = guess if confidence >= catalog.GUESS_FLOOR else "other"
        session["app_type"] = chosen
        session["pack"] = catalog.build_pack(chosen, session.get("raw_idea", ""))
        session["answers"][key]["value"] = chosen

    return session


def flat_answers(session: dict) -> list[dict]:
    """The keyed interview answers as the flat list the rest of the API reads."""
    out: list[dict] = []
    for key, entry in (session.get("answers") or {}).items():
        raw = entry.get("custom_text") or entry.get("text") or None

        attached = " ".join(attachment_texts(entry)).strip()
        if attached:
            raw = f"{raw} {attached}".strip() if raw else attached
        out.append({
            "question_id": key,
            "value": entry.get("value"),
            "raw_text": raw,
        })
    return out
