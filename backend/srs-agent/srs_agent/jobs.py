"""Long POSTs, made short."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("srs.jobs")

router = APIRouter(prefix="/jobs", tags=["agentforge"])


_JOBS: dict[str, dict] = {}


_KEEP_FINISHED_S = 900
_MAX_JOBS = 200

_app = None


class JobRequest(BaseModel):
    path: str
    method: str = "POST"
    body: dict | None = None


def attach(app) -> None:
    """Mount the job routes onto the SRS app and remember it for replay."""
    global _app
    _app = app
    app.include_router(router)


def _reap() -> None:
    done = [(j["finished"], jid) for jid, j in _JOBS.items() if j.get("finished")]
    now = time.time()
    for finished, jid in done:
        if now - finished > _KEEP_FINISHED_S:
            _JOBS.pop(jid, None)
    if len(_JOBS) > _MAX_JOBS:
        for _, jid in sorted(done)[:len(_JOBS) - _MAX_JOBS]:
            _JOBS.pop(jid, None)


async def _run(job_id: str, req: JobRequest) -> None:
    import httpx

    job = _JOBS[job_id]
    try:
        transport = httpx.ASGITransport(app=_app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://srs",
                                     timeout=None) as client:
            r = await client.request(req.method, req.path, json=req.body or {})
        job["http_status"] = r.status_code
        try:
            job["result"] = r.json()
        except Exception:
            job["result"] = {"text": r.text}

        job["status"] = "done"
    except Exception as e:                                  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        log.warning(f"job {job_id} ({req.path}) failed: {job['error']}")
    finally:
        job["finished"] = time.time()


@router.post("")
async def start_job(req: JobRequest):
    """Begin the work and answer immediately."""
    if _app is None:
        raise HTTPException(503, "job runner is not attached")
    if not req.path.startswith("/"):
        raise HTTPException(400, "path must be absolute")

    if req.path.startswith("/jobs"):
        raise HTTPException(400, "jobs cannot run jobs")

    _reap()
    job_id = "job_" + uuid.uuid4().hex[:20]
    _JOBS[job_id] = {"status": "running", "path": req.path,
                     "started": time.time(), "finished": None,
                     "http_status": None, "result": None, "error": ""}
    asyncio.create_task(_run(job_id, req))
    return {"job_id": job_id, "status": "running", "path": req.path}


@router.get("/{job_id}")
async def poll_job(job_id: str):
    job = _JOBS.get(job_id)
    if job is None:

        raise HTTPException(404, "no such job (it may have expired)")
    elapsed = (job.get("finished") or time.time()) - job["started"]
    return {"job_id": job_id, **job, "elapsed": round(elapsed, 1)}
