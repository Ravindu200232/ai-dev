"""SRS generation + read endpoints (requirements, diagrams, ambiguities, risks)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..models import repositories as repo
from ..services import orchestrator

router = APIRouter(prefix="/projects", tags=["srs"])


async def _doc_or_404(project_id: str) -> dict:
    srs = await orchestrator.latest_srs(project_id)
    if not srs:
        raise HTTPException(404, "no SRS generated yet")
    return srs["srs_document"]


@router.post("/{project_id}/generate-srs")
async def generate_srs(project_id: str):
    try:
        result = await orchestrator.generate_srs(project_id)
    except KeyError:
        raise HTTPException(404, "project not found")
    return {"project": result["project"], "version": result["version"], "summary": result["summary"]}


@router.get("/{project_id}/srs-json")
async def srs_json(project_id: str):
    srs = await orchestrator.latest_srs(project_id)
    if not srs:
        raise HTTPException(404, "no SRS generated yet")
    return srs


@router.get("/{project_id}/requirements")
async def requirements(project_id: str):
    doc = await _doc_or_404(project_id)
    return {
        "functional_requirements": doc.get("functional_requirements", []),
        "non_functional_requirements": doc.get("non_functional_requirements", []),
    }


@router.get("/{project_id}/diagrams")
async def diagrams(project_id: str):
    stored = await repo.list_diagrams(project_id)
    if not stored:
        doc = await _doc_or_404(project_id)
        stored = doc.get("diagrams", [])
    return {"diagrams": [_with_svg(d) for d in stored]}


_SVG_INLINE_LIMIT = 400_000


def _with_svg(diagram: dict) -> dict:
    if not isinstance(diagram, dict) or diagram.get("svg"):
        return diagram
    path = diagram.get("svg_path")
    if not path:
        return diagram
    try:
        body = Path(path).read_text(encoding="utf-8")
    except OSError:
        return diagram
    if not 0 < len(body) <= _SVG_INLINE_LIMIT:
        return diagram
    return {**diagram, "svg": body}


@router.get("/{project_id}/ambiguities")
async def ambiguities(project_id: str):
    doc = await _doc_or_404(project_id)
    return {"ambiguities": doc.get("ambiguities", []), "assumptions": doc.get("assumptions", [])}


@router.get("/{project_id}/risks")
async def risks(project_id: str):
    doc = await _doc_or_404(project_id)
    return {"risk_priority": doc.get("risk_priority", [])}


async def _live_handoff(project_id: str) -> dict:
    """Return the stored handoff upgraded to the current builder contract."""
    doc = await _doc_or_404(project_id)
    handoff = doc.get("builder_handoff")
    if not handoff:
        raise HTTPException(404, "no builder handoff — this SRS predates the approved-plan flow")
    try:
        from ..generators.builder_brief import refresh_handoff
        from ..services import plan_approval
        plan_doc = (await plan_approval.approved_plan(project_id)
                    or await repo.latest_plan(project_id))
        plan = (plan_doc or {}).get("plan") or {}
        if plan:
            fresh = refresh_handoff(handoff, plan, doc)
            if fresh.get("prompt") and len(fresh["prompt"]) > 200:
                handoff = fresh
    except Exception:
        pass
    return handoff


@router.get("/{project_id}/builder-handoff")
async def builder_handoff(project_id: str):
    """The plan restated in the app builder's vocabulary, plus its prompt."""
    return await _live_handoff(project_id)


@router.get("/{project_id}/builder-prompt", response_class=PlainTextResponse)
async def builder_prompt(project_id: str):
    """Just the string. Paste it straight into the builder."""
    prompt = (await _live_handoff(project_id)).get("prompt")
    if not prompt:
        raise HTTPException(404, "no builder prompt — this SRS predates the approved-plan flow")
    return prompt
