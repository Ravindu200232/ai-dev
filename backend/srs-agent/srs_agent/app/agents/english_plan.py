"""Create the hidden English plan used after the localized plan is approved."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from ..llm import get_llm
from ..services.events import bus
from .language import LanguageConversionError, is_english, language_name
from .plan_generator import render_plan_markdown
from .state import AgentState


_MACHINE_KEYS = {
    "id", "key", "role_key", "route", "method", "path", "required",
    "priority", "type", "table_name", "field_name", "requirement_id",
}
_CONTRACT_TOKEN = re.compile(
    r"`[^`\n]+`|\b(?:FR|NFR|AC|WF|CAP|TC)-[A-Za-z0-9_.:-]+\b|"
    r"(?:/[A-Za-z0-9_./{}:-]+)", re.I,
)


def _translatable(key: str | None, value: str) -> bool:
    return bool(value.strip()) and key != "app_name" and key not in _MACHINE_KEYS


def _collect(value: Any) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()

    def walk(item: Any, parent_key: str | None = None) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, str):
                    if _translatable(key, child) and child not in seen:
                        seen.add(child)
                        texts.append(child)
                else:
                    walk(child, key)
        elif isinstance(item, list):
            for child in item:
                if isinstance(child, str):
                    if _translatable(parent_key, child) and child not in seen:
                        seen.add(child)
                        texts.append(child)
                else:
                    walk(child, parent_key)

    walk(value)
    return texts


def _apply(value: Any, translations: dict[str, str],
           parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: (translations[child] if isinstance(child, str)
                  and _translatable(key, child)
                  else _apply(child, translations, key))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            translations[child] if isinstance(child, str)
            and _translatable(parent_key, child)
            else _apply(child, translations, parent_key)
            for child in value
        ]
    return value


def _same_shape(source: Any, translated: Any, path: str = "plan") -> None:
    if isinstance(source, dict):
        if not isinstance(translated, dict) or set(source) != set(translated):
            raise ValueError(f"translation changed fields at {path}")
        for key in source:
            _same_shape(source[key], translated[key], f"{path}.{key}")
    elif isinstance(source, list):
        if not isinstance(translated, list) or len(source) != len(translated):
            raise ValueError(f"translation changed list length at {path}")
        for index, item in enumerate(source):
            _same_shape(item, translated[index], f"{path}[{index}]")
    elif source is None or isinstance(source, (bool, int, float)):
        if source != translated:
            raise ValueError(f"translation changed a non-text value at {path}")
    elif not isinstance(translated, str):
        raise ValueError(f"translation changed a text value at {path}")


def _translation_validator(texts: list[str]):
    expected = dict(enumerate(texts))

    def validate(data: dict) -> None:
        rows = data.get("translations") if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise ValueError("return every translation exactly once")
        seen: set[int] = set()
        for row in rows:
            index = row.get("index") if isinstance(row, dict) else None
            text = str((row or {}).get("text") or "").strip()
            if not isinstance(index, int) or index not in expected or index in seen or not text:
                raise ValueError("translation indexes must be unique and complete")
            protected = _CONTRACT_TOKEN.findall(expected[index])
            if any(token not in text for token in protected):
                raise ValueError("translation changed protected contract tokens")
            seen.add(index)
        if seen != set(expected):
            raise ValueError("translation indexes are incomplete")

    return validate


async def english_plan_node(state: AgentState) -> AgentState:
    """Replace only the graph's plan with English; the approved UI plan stays localized."""
    plan = copy.deepcopy(state.get("plan") or {})
    if not plan:
        return state
    project = state.get("project") or {}
    selected_language = language_name(project.get("language", state.get("language")))
    if is_english(selected_language):
        return state

    pid = state["project_id"]
    texts = _collect(plan)
    system = (
        "Translate each supplied approved software-plan text into clear English. Return "
        "ONLY JSON as {\"translations\":[{\"index\":0,\"text\":\"...\"}]}. Return every "
        "numeric index exactly once. Preserve exact meaning, scope, ordering, project/app "
        "names, IDs, routes, code identifiers, numbers and technical product names. Never "
        "add, remove, merge or split a requirement."
    )
    try:
        await bus.emit(pid, "EnglishPlanAgent",
                       "Preparing the approved plan for the English SRS…", progress=8)
        payload = {"texts": [{"index": index, "text": text}
                             for index, text in enumerate(texts)]}
        data = await get_llm().complete_json(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            validator=_translation_validator(texts),
            label="approved_plan_english",
            trace_sink=lambda item: bus.trace(pid, item),
        )
        by_index = {row["index"]: str(row["text"]).strip()
                    for row in data.get("translations") or []}
        translations = {text: by_index[index] for index, text in enumerate(texts)}
        english_plan = _apply(plan, translations)
        _same_shape(plan, english_plan)
        if english_plan.get("app_name") != plan.get("app_name"):
            raise ValueError("the customer app name changed")
        await bus.emit(pid, "EnglishPlanAgent",
                       "English plan source ready; writing the SRS in English.",
                       level="success", progress=10)
        return {
            **state,
            "plan": english_plan,
            "plan_markdown": render_plan_markdown(
                english_plan, app_name=english_plan.get("app_name", ""),
            ),
        }
    except Exception as exc:
        raise LanguageConversionError(
            "The selected SRS model could not prepare the approved plan for the English "
            "SRS. Check that the selected model is available and try again."
        ) from exc


__all__ = ["english_plan_node"]
