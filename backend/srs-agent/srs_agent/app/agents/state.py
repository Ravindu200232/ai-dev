"""Shared LangGraph state for the agentic workflow."""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    project_id: str
    project: dict[str, Any]
    raw_idea: str
    brief: str
    language: str

    classification: dict[str, Any]
    is_nonsense: bool
    needs_clarification: bool
    clarification_reason: str

    questions: list[dict[str, Any]]
    answers: list[dict[str, Any]]
    session: dict[str, Any]
    coverage: dict[str, Any]

    plan: dict[str, Any]
    plan_markdown: str

    srs: dict[str, Any]
    diagrams: list[dict[str, Any]]

    customization_prompt: str
    diff_summary: list[str]

    error: str
