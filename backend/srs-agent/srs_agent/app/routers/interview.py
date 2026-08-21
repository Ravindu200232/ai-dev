"""Customer-led requirement interview endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas.questions import InterviewAnswer
from ..services import orchestrator

router = APIRouter(prefix="/projects", tags=["interview"])


@router.get("/{project_id}/interview")
async def interview_state(project_id: str):
    return await orchestrator.interview_state(project_id)


@router.post("/{project_id}/interview/answer")
async def interview_answer(project_id: str, payload: InterviewAnswer):
    try:
        return await orchestrator.interview_answer(
            project_id,
            key=payload.key,
            value=payload.value,
            text=payload.text or "",
            selected=payload.selected,
            custom=payload.custom or "",
            attachments=payload.attachments,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc) or "project not found")
