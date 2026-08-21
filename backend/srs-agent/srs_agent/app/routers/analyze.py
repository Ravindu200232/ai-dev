"""Analyze endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import orchestrator

router = APIRouter(prefix="/projects", tags=["analyze"])


@router.post("/{project_id}/analyze")
async def analyze(project_id: str):
    try:
        return await orchestrator.analyze(project_id)
    except KeyError:
        raise HTTPException(404, "project not found")


@router.get("/{project_id}/questions")
async def get_questions(project_id: str):
    """Every question asked so far, in order."""
    return await orchestrator.get_questions(project_id)
