"""Language contracts for customer-facing SRS model calls."""
from __future__ import annotations

import json
import re

from ..llm import get_llm


class LanguageConversionError(RuntimeError):
    """The selected SRS model could not honour the output-language contract."""


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
    expected_count = len(source.get("options") or [])

    def validate(data: dict) -> None:
        if not isinstance(data, dict) or not str(data.get("question") or "").strip():
            raise ValueError("return a translated question object")
        options = data.get("options") or []
        if not isinstance(options, list) or len(options) != expected_count:
            raise ValueError("return exactly the supplied number of options")
        for index, option in enumerate(options):
            if (not isinstance(option, dict)
                    or option.get("index") != index
                    or not str(option.get("label") or "").strip()):
                raise ValueError("return every option once, in index order")

    return validate


async def localize_question_payload(payload: dict, language: object, *,
                                    project_id: str = "") -> dict:
    """Translate a fixed clarification payload without changing its machine values."""
    if not payload:
        return payload
    selected_language = language_name(language)
    if is_english(selected_language):
        return {**payload, "output_language": selected_language}
    source = {
        "question": payload.get("question", ""),
        "why_needed": payload.get("why_needed", ""),
        "placeholder": payload.get("placeholder", ""),
        "options": [
            {"index": index, "label": o.get("label", ""),
             "hint": o.get("hint", "")}
            for index, o in enumerate(payload.get("options") or [])
            if isinstance(o, dict)
        ],
    }
    system = (
        "You translate one software-requirements interview question. Return ONLY a JSON "
        "object with question, why_needed, placeholder and options. Each returned option "
        "must contain only index, label and hint. Copy every numeric index exactly and in "
        "the same order. Translate only the customer-visible text. Do not answer the "
        "question, explain the translation, add options or remove options."
        + output_language_instruction(selected_language, artifact="interview question")
    )
    try:
        translated = await get_llm().complete_json(
            system=system,
            user=json.dumps(source, ensure_ascii=False),
            validator=_question_translation_validator(source),
            label="question_language",
        )
    except Exception as exc:
        raise LanguageConversionError(
            f"The selected SRS model could not return the interview in "
            f"{selected_language}. Check that the selected model is available and try again."
        ) from exc

    localized_options = translated.get("options") or []
    options = []
    for original, localized in zip(payload.get("options") or [], localized_options):
        merged = {
            **original,
            "label": str(localized.get("label") or original.get("label") or ""),
        }
        hint = str(localized.get("hint") or original.get("hint") or "")
        if hint or "hint" in original:
            merged["hint"] = hint
        options.append(merged)
    return {
        **payload,
        "question": str(translated.get("question") or payload.get("question") or ""),
        "why_needed": str(translated.get("why_needed") or payload.get("why_needed") or ""),
        "placeholder": str(translated.get("placeholder") or payload.get("placeholder") or ""),
        "options": options,
        "suggested_options": [str(o.get("label", "")) for o in options],
        "output_language": selected_language,
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
    except Exception as exc:
        raise LanguageConversionError(
            "The selected SRS model could not prepare the required English Builder "
            "handoff. Check that the selected model is available and try again."
        ) from exc
    return handoff


__all__ = [
    "ensure_english_builder_prompt", "is_english", "language_name",
    "LanguageConversionError",
    "localize_question_payload", "output_language_instruction",
]
