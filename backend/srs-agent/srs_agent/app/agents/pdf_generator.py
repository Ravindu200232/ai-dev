"""SrsPdfGeneratorAgent — render the professional SRS PDF on demand."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..generators.pdf import build_srs_pdf
from ..generators.standards import apply_international_profile
from ..services import storage
from ..services.events import bus


async def generate_pdf(project_id: str, srs: dict, *, status: str = "Draft",
                       version: str | None = None) -> Path:
    apply_international_profile(srs)
    await bus.emit(project_id, "SrsPdfGeneratorAgent", "Rendering SRS PDF...", channel="agent_events")
    diagrams = srs.get("srs_document", {}).get("diagrams", [])
    out = storage.srs_pdf_path(project_id, version)

    path = await asyncio.to_thread(build_srs_pdf, srs, out,
                                   status=status, diagrams=diagrams)

    if version:
        await asyncio.to_thread(build_srs_pdf, srs,
                                storage.srs_pdf_path(project_id),
                                status=status, diagrams=diagrams)
    await bus.emit(project_id, "SrsPdfGeneratorAgent", f"SRS PDF ready: {path.name}", level="success")
    return path
