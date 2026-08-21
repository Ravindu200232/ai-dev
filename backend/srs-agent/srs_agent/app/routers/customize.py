"""Prompt-based customization endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas.project import CustomizeRequest
from ..services import orchestrator

router = APIRouter(prefix="/projects", tags=["customize"])


@router.post("/{project_id}/customize")
async def customize(project_id: str, req: CustomizeRequest):
    try:
        result = await orchestrator.customize(project_id, req.prompt)
    except KeyError:
        raise HTTPException(404, "project not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "project": result["project"],
        "version": result["version"],
        "diff_summary": result["diff_summary"],
        "summary": result["summary"],
    }
