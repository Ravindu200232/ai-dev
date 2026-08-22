"""Language contracts for customer-facing SRS model calls."""
from __future__ import annotations

import json
import re

from ..llm import get_llm


def language_name(value: object) -> str:
    """Return the selected language, with English as the safe default."""
    text = " ".join(str(value or "").split()).strip()
    return text[:80] or "English"


def is_english(value: object) -> bool:
    return language_name(value).casefold() in {
        "en", "eng", "english", "english (united kingdom)",
        "english (united states)",
    }


def output_language_instruction(value: object, *, artifact: str) -> str:
    """Prompt clause used only when the customer selected a non-English language."""
    language = language_name(value)
    if is_english(language):
        return ""
    return (
        "\nOUTPUT LANGUAGE CONTRACT (MANDATORY):\n"
        f"- Write every customer-visible natural-language part of this {artifact} in {language}.\n"
        f"- Questions, option labels, hints, explanations, headings, summaries, requirements, "
        f"workflow steps and descriptive prose must be in {language}.\n"
        "- Keep JSON keys, schema field names, IDs, requirement IDs, enum values, booleans, "
        "HTTP methods, routes, file paths, code identifiers, table/field keys and option `value` "
        "fields unchanged in their required machine format.\n"
        "- Keep names and exact text supplied by the customer unchanged unless they explicitly "
        "asked for a translation.\n"
        "- Do not add, remove or reinterpret any requirement while changing the output language.\n"
    )


def _question_translation_validator(source: dict):
    expected = [str(o.get("value")) for o in (source.get("options") or [])
                if isinstance(o, dict)]

    def validate(data: dict) -> None:
        if not isinstance(data, dict) or not str(data.get("question") or "").strip():
            raise ValueError("return a translated question object")
        actual = [str(o.get("value")) for o in (data.get("options") or [])
                  if isinstance(o, dict)]
        if actual != expected:
            raise ValueError("option values and option order must remain unchanged")

    return validate


async def localize_question_payload(payload: dict, language: object, *,
                                    project_id: str = "") -> dict:
    """Translate a fixed clarification payload without changing its machine values."""
    if is_english(language) or not payload:
        return payload
    source = {
        "question": payload.get("question", ""),
        "why_needed": payload.get("why_needed", ""),
        "placeholder": payload.get("placeholder", ""),
        "options": [
            {"label": o.get("label", ""), "value": o.get("value"),
             "hint": o.get("hint", "")}
            for o in (payload.get("options") or []) if isinstance(o, dict)
        ],
    }
    system = (
        "You translate one software-requirements interview question. Return ONLY a JSON "
        "object with question, why_needed, placeholder and options. Translate only the "
        "customer-visible text. Preserve every option value exactly, including its type and "
        "order. Do not add or remove options."
        + output_language_instruction(language, artifact="interview question")
    )
    try:
        translated = await get_llm().complete_json(
            system=system,
            user=json.dumps(source, ensure_ascii=False),
            validator=_question_translation_validator(source),
            label="question_language",
        )
    except Exception:  # A language helper must never block the interview.
        return payload

    options = translated.get("options") or []
    return {
        **payload,
        "question": str(translated.get("question") or payload.get("question") or ""),
        "why_needed": str(translated.get("why_needed") or payload.get("why_needed") or ""),
        "placeholder": str(translated.get("placeholder") or payload.get("placeholder") or ""),
        "options": options,
        "suggested_options": [str(o.get("label", "")) for o in options],
    }


def _builder_translation_validator(source: str):
    ids = set(re.findall(r"\b(?:FR|NFR|AC|WF|CAP)-[A-Za-z0-9_.:-]+", source))
    protected = set(re.findall(r"`[^`\n]+`", source))

    def validate(data: dict) -> None:
        prompt = str((data or {}).get("prompt") or "").strip()
        if len(prompt) < 200 or "AGENTFORGE BUILD HANDOFF" not in prompt:
            raise ValueError("return the complete AgentForge build handoff")
        missing_ids = sorted(x for x in ids if x not in prompt)
        missing_tokens = sorted(x for x in protected if x not in prompt)
        if missing_ids or missing_tokens:
            raise ValueError("translation changed protected identifiers")

    return validate


async def ensure_english_builder_prompt(handoff: dict, source_language: object,
                                        *, project_id: str = "") -> dict:
    """Keep the customer document localized while giving Builder an English prompt."""
    handoff = dict(handoff or {})
    source = str(handoff.get("prompt") or "").strip()
    language = language_name(source_language)
    handoff["source_document_language"] = language
    handoff["prompt_language"] = "English"
    if not source or is_english(language):
        return handoff

    system = (
        "You translate an authoritative software build contract into English. Return ONLY "
        "JSON as {\"prompt\": \"the complete English contract\"}. Translate every human "
        "sentence into clear technical English, but preserve the exact meaning and scope. "
        "Never add or remove a requirement. Preserve all headings, ordering, requirement IDs, "
        "backtick-delimited text, routes, HTTP methods, filenames, field names, table names, "
        "role keys, test IDs, commands, numbers and code identifiers exactly."
    )
    try:
        data = await get_llm().complete_json(
            system=system,
            user=source,
            validator=_builder_translation_validator(source),
            label="builder_handoff_english",
        )
        translated = str(data.get("prompt") or "").strip()
        if translated:
            handoff["prompt"] = translated + "\n"
    except Exception:
        # The original contract is safer than a partial or structurally altered translation.
        handoff["prompt_language"] = language
    return handoff


__all__ = [
    "ensure_english_builder_prompt", "is_english", "language_name",
    "localize_question_payload", "output_language_instruction",
]
