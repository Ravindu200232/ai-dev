"""The plan the customer approves before the SRS is written."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import orchestrator

router = APIRouter(prefix="/projects", tags=["plan"])


class PlanRequest(BaseModel):
    revision: str = ""


class ApproveRequest(BaseModel):
    version: int | None = None


@router.post("/{project_id}/plan")
async def generate_plan(project_id: str, payload: PlanRequest | None = None):
    try:
        return await orchestrator.generate_plan(project_id,
                                                (payload.revision if payload else ""))
    except KeyError:
        raise HTTPException(404, "project not found")


@router.get("/{project_id}/plan")
async def get_plan(project_id: str):
    return await orchestrator.plan_state(project_id)


@router.post("/{project_id}/plan/approve")
async def approve_plan(project_id: str, payload: ApproveRequest | None = None):
    verdict = await orchestrator.approve_plan(project_id,
                                              payload.version if payload else None)
    if not verdict.get("approved"):
        raise HTTPException(409, verdict.get("reason") or "plan cannot be approved")
    return verdict
