"""Live agent events: SSE stream + history + console channels."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from ..models import repositories as repo
from ..services.events import bus

router = APIRouter(prefix="/projects", tags=["events"])


@router.get("/{project_id}/events")
async def list_events(project_id: str):
    return {"events": await repo.list_events(project_id)}


@router.get("/{project_id}/traces")
async def list_traces(project_id: str):
    return {"traces": await repo.list_prompt_traces(project_id)}


@router.get("/{project_id}/errors")
async def list_errors(project_id: str):
    return {"errors": await repo.list_errors(project_id)}


@router.get("/{project_id}/events/stream")
async def stream(project_id: str, request: Request):
    async def gen():
        gen_iter = bus.subscribe(project_id).__aiter__()
        while True:
            if await request.is_disconnected():
                break
            try:
                ev = await asyncio.wait_for(gen_iter.__anext__(), timeout=15.0)
                yield {"event": "agent", "data": json.dumps(ev)}
            except asyncio.TimeoutError:

                yield {"event": "ping", "data": "{}"}
            except StopAsyncIteration:
                break

    return EventSourceResponse(gen())
