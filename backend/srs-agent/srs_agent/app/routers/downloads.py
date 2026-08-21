"""Artifact download endpoints (JSON + PDF)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ..agents.pdf_generator import generate_pdf
from ..models import repositories as repo
from ..services import orchestrator

router = APIRouter(prefix="/projects", tags=["downloads"])


@router.get("/{project_id}/download/json")
async def download_json(project_id: str):
    srs = await orchestrator.latest_srs(project_id)
    if not srs:
        raise HTTPException(404, "no SRS generated yet")
    project = await repo.get_project(project_id)
    name = (project or {}).get("title", "srs").replace(" ", "_")[:40]
    return Response(
        content=json.dumps(srs, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}_SRS.json"'},
    )


@router.get("/{project_id}/download/pdf")
async def download_pdf(project_id: str):
    project = await repo.get_project(project_id)
    if not project:
        raise HTTPException(404, "project not found")
    srs = await orchestrator.latest_srs(project_id)
    if not srs:
        raise HTTPException(404, "no SRS generated yet")

    status = "Approved" if project.get("status") in ("approved", "customized") else "Draft"
    version = project.get("current_version", "1.0.0")
    path = await generate_pdf(project_id, srs, status=status, version=version)
    name = project.get("title", "srs").replace(" ", "_")[:40]
    return FileResponse(path, media_type="application/pdf", filename=f"{name}_SRS_v{version}.pdf")
