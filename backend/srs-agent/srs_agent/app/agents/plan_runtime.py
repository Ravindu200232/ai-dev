"""LLM refinement for the approval plan."""
from __future__ import annotations

import json
import logging

from ..llm import LLMRepairFailed, LLMUnavailable, get_llm
from ..services.events import bus
from .customer_context import customer_context
from .language import LanguageConversionError, is_english, output_language_instruction
from .plan_merge import _merge
from .plan_offline import (
    _answers_digest, _domain_library, _what_nobody_asked, _what_we_worked_out,
    build_offline_plan, customer_notes,
)
from .plan_rules import _REVISION_TAIL, _SYS, _plan_validator

log = logging.getLogger("agentforge.plan")

async def generate_plan(*, project: dict, session: dict, brief: str = "",
                        previous: dict | None = None, revision: str = "",
                        coverage: dict | None = None) -> dict:
    """Build an enriched plan, falling back to its skeleton."""
    pid = project.get("id", "")
    selected_language = project.get("language", "English")
    skeleton = build_offline_plan(project=project, session=session, brief=brief)

    pack = session.get("pack") or {}

    parts = [
        customer_context(brief=brief, session=session, project=project)
        or f"THE CUSTOMER'S IDEA:\n{brief or project.get('raw_idea', '')}",
        "",
        f"APP TYPE: {pack.get('app_label', 'unknown')} ({pack.get('archetype', '')})",
        f"BUSINESS DOMAIN: {pack.get('domain_label', 'general')}",
    ]
    language_rule = output_language_instruction(
        selected_language, artifact="approved plan",
    )
    if language_rule:
        parts.append(language_rule)
    parts.append("")

    for block in (_what_we_worked_out(project), _domain_library(pack)):
        if block:
            parts += [block, ""]

    parts += [f"WHAT THEY ANSWERED:\n{_answers_digest(session)}", ""]

    gaps = _what_nobody_asked(coverage)
    if gaps:
        parts += [gaps, ""]

    notes = customer_notes(session)
    if notes:

        parts += [
            "IN THE CUSTOMER'S OWN WORDS — they wrote this at the end, unprompted, "
            "when asked what else the site should show:",
            f'"""{notes}"""',
            "",
            "Everything they named there must appear in the plan — as a screen, a "
            "section, a record or a feature, whichever it actually is. Do not "
            "summarise it away, and do not drop the parts you have no question for.",
            "",
        ]

    # A revision edits the exact plan the customer approved.
    if previous is None:
        parts += [
            "A PLAN ASSEMBLED FROM THOSE ANSWERS (rewrite it in their terms; keep "
            "anything that is already right):",
            "```json",
            json.dumps(skeleton, ensure_ascii=False),
            "```",
        ]
    else:
        parts += [
            "## THE PLAN AS IT STANDS — this is the plan you are editing",
            "```json",
            json.dumps(previous, ensure_ascii=False),
            "```",
            "",
            "## WHAT THE CUSTOMER WANTS CHANGED — their words, in full",
            revision or "(no change requested)",
            "",
            _REVISION_TAIL,
        ]

    try:
        await bus.emit(pid, "PlanGeneratorAgent",
                       "Turning the answers into a plan…", progress=30)
        plan = await get_llm().complete_json(
            system=_SYS, user="\n".join(parts), validator=_plan_validator(pack),
            label="plan_generate",
            trace_sink=(lambda p: bus.trace(pid, p)) if pid else None,
        )
        # Missing revision fields fall back to the plan being edited.
        merged = _merge(previous if previous is not None else skeleton, plan)
        await bus.emit(pid, "PlanGeneratorAgent",
                       f"Plan ready: {len(merged['screens'])} screens, "
                       f"{len(merged['records'])} record types, "
                       f"{len(merged['features'])} features.",
                       level="success", progress=90)
        return merged
    except LLMUnavailable as exc:
        if not is_english(selected_language):
            await bus.error(pid, "PlanGeneratorAgent",
                            f"The selected SRS model could not produce the plan in "
                            f"{selected_language}.")
            raise LanguageConversionError(
                f"The selected SRS model could not produce the plan in "
                f"{selected_language}. Check that the selected model is available and try again."
            ) from exc
        await bus.emit(pid, "PlanGeneratorAgent",
                       f"LLM not used ({str(exc)[:140]}) — using the plan built "
                       "from your answers.", level="warn", progress=90)
    except LLMRepairFailed as exc:
        if not is_english(selected_language):
            await bus.error(pid, "PlanGeneratorAgent",
                            f"The selected SRS model returned an invalid "
                            f"{selected_language} plan.")
            raise LanguageConversionError(
                f"The selected SRS model could not return a valid plan in "
                f"{selected_language}. Try again with the selected model."
            ) from exc
        await bus.error(pid, "PlanGeneratorAgent",
                        f"LLM plan didn't validate after retries; kept the plan "
                        f"built from your answers. ({exc.label})")
    except Exception as exc:  # noqa: BLE001
        if not is_english(selected_language):
            await bus.error(pid, "PlanGeneratorAgent",
                            f"The selected SRS model could not produce the plan in "
                            f"{selected_language}: {exc}")
            raise LanguageConversionError(
                f"The selected SRS model could not produce the plan in "
                f"{selected_language}. Try again with the selected model."
            ) from exc
        await bus.error(pid, "PlanGeneratorAgent",
                        f"Plan generation error: {exc}; kept the plan built from "
                        "your answers.")

    if previous is not None and revision:

        skeleton = {**previous,
                    "open_questions": list(previous.get("open_questions") or [])}
        skeleton.setdefault("assumptions", []).append(
            f"Requested change not applied automatically: {revision[:200]}")
    return skeleton
