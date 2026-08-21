"""Project CRUD + input ingestion endpoints."""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from ..extraction import read_image, read_pdf, transcribe_audio
from ..models import repositories as repo
from ..schemas.project import CreateProjectRequest, UploadRequest
from ..services import orchestrator, storage

router = APIRouter(prefix="/projects", tags=["projects"])

# Limit base64 uploads before decoding.
MAX_UPLOAD_BYTES = 7_500_000


@router.post("")
async def create_project(req: CreateProjectRequest):
    project = await orchestrator.create_project(req.idea, req.language)
    return {"project": project}


@router.get("")
async def list_projects():
    return {"projects": await repo.list_projects()}


@router.get("/{project_id}")
async def get_project(project_id: str):
    try:
        return await orchestrator.project_detail(project_id)
    except KeyError:
        raise HTTPException(404, "project not found")


@router.post("/{project_id}/approve")
async def approve_project(project_id: str):
    project = await repo.get_project(project_id)
    if not project:
        raise HTTPException(404, "project not found")
    await repo.update_project(project_id, {"status": "approved"})
    return {"project": await repo.get_project(project_id)}


PUBLIC_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")


def _public_name(fname: str) -> str:
    """A safe file name a built app can serve from public/."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(fname or "upload").name).strip("-.")
    return stem or "upload"


async def _ingest(project_id: str, mode: str, data: bytes, fname: str, ctype: str,
                  purpose: str = "") -> dict:
    """Save an upload, extract it, and record its source."""
    uploads = storage.project_dir(project_id) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / Path(fname).name).write_bytes(data)

    lower = fname.lower()
    purpose = " ".join(str(purpose or "").split())[:300]

    public_url = ""
    if lower.endswith(PUBLIC_IMAGE_EXT) or ctype.startswith("image/"):
        public = storage.project_dir(project_id) / "public" / "uploads"
        public.mkdir(parents=True, exist_ok=True)
        safe = _public_name(fname)
        (public / safe).write_bytes(data)
        public_url = f"/uploads/{safe}"

    if mode == "pdf" or lower.endswith(".pdf") or "pdf" in ctype:
        res = await read_pdf(data, fname); resolved = "pdf"
    elif mode == "image" or ctype.startswith("image/") or lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
        res = await read_image(data, fname); resolved = "image"
    elif mode == "voice" or ctype.startswith("audio/") or lower.endswith((".webm", ".wav", ".mp3", ".m4a", ".ogg", ".flac")):
        res = await run_in_threadpool(transcribe_audio, data, fname); resolved = "voice"
    else:
        res = {"text": data.decode("utf-8", "ignore"), "engine": "raw"}; resolved = "text"

    meta = {k: v for k, v in res.items() if k != "text"}
    if public_url:
        meta["url"] = public_url
    if purpose:
        meta["purpose"] = purpose

    src = await orchestrator.add_source(
        project_id, resolved, res.get("text", ""), fname, meta,
    )

    note = res.get("warning") or res.get("error")
    return {"source": src, "extraction": meta, "note": note,
            "url": public_url, "purpose": purpose}


@router.post("/{project_id}/inputs")
async def add_input(
    project_id: str,
    mode: str = Form("text"),
    text: Optional[str] = Form(None),
    purpose: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    project = await repo.get_project(project_id)
    if not project:
        raise HTTPException(404, "project not found")

    if file is None:
        src = await orchestrator.add_source(project_id, mode, text or "", None, {})
        return {"source": src}

    return await _ingest(project_id, mode, await file.read(),
                         file.filename or "upload",
                         (file.content_type or "").lower(), purpose or "")


@router.post("/{project_id}/inputs-json")
async def add_input_json(project_id: str, req: UploadRequest):
    """The same ingestion, with the file base64."""
    project = await repo.get_project(project_id)
    if not project:
        raise HTTPException(404, "project not found")

    if not req.data_base64:
        src = await orchestrator.add_source(project_id, req.mode, req.text or "", None, {})
        return {"source": src}

    raw = req.data_base64
    if "," in raw[:64] and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]  # A browser data: URL
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"attachment is not valid base64: {exc}")

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"attachment is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB")

    return await _ingest(project_id, req.mode, data,
                         req.filename or "upload",
                         (req.content_type or "").lower(), req.purpose or "")
