"""CoverageAuditorAgent — score SRS coverage before generation."""
from __future__ import annotations

from ..knowledge.coverage_signals import covered as _covered
from ..schemas.questions import COVERAGE_AREAS, CRITICAL_AREAS
from ..services.events import bus
from .state import AgentState


def _answered_areas(questions: list[dict], answers: list[dict]) -> set[str]:
    by_id = {q["id"]: q for q in questions}
    covered: set[str] = set()
    for a in answers:
        q = by_id.get(a.get("question_id"))
        if not q:
            continue
        val = a.get("value")
        raw = a.get("raw_text")
        non_empty = raw or (val not in (None, "", [], False))
        if non_empty:
            covered.update(q.get("coverage_areas", []))
    return covered


def compute_coverage(brief: str, questions: list[dict], answers: list[dict]) -> dict:
    answered = _answered_areas(questions, answers)
    covered: set[str] = set(answered)
    for area in COVERAGE_AREAS:
        if area in covered:
            continue
        if _covered(area, brief):
            covered.add(area)

    missing = [a for a in COVERAGE_AREAS if a not in covered]
    critical_missing = [a for a in missing if a in CRITICAL_AREAS]
    optional_missing = [a for a in missing if a not in CRITICAL_AREAS]

    total = len(COVERAGE_AREAS)

    weighted = len(covered) + 0.6 * len(optional_missing)
    score = round(100 * weighted / total)
    complete = len(critical_missing) == 0 and score >= 80

    return {
        "score": score,
        "covered": sorted(covered),
        "missing": missing,
        "critical_missing": critical_missing,
        "optional_missing": optional_missing,
        "complete": complete,
    }


async def audit_node(state: AgentState) -> AgentState:
    pid = state["project_id"]
    brief = state.get("brief", "")
    questions = state.get("questions", [])
    answers = state.get("answers", [])

    cov = compute_coverage(brief, questions, answers)
    await bus.emit(
        pid, "CoverageAuditorAgent",
        f"Coverage {cov['score']}% — "
        + ("ready to generate." if cov["complete"] else f"missing critical: {', '.join(cov['critical_missing']) or 'none'}"),
        level="success" if cov["complete"] else "info",
        progress=92, data={"coverage": cov},
    )
    return {**state, "coverage": cov}
