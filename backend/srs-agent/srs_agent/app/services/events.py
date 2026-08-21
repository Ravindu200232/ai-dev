"""In-process event bus that persists agent events and fans them out to SSE."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator, Optional

from ..models import repositories as repo
from ..schemas.project import now_iso


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def emit(
        self,
        project_id: str,
        agent: str,
        message: str,
        *,
        channel: str = "agent_events",
        level: str = "info",
        progress: Optional[float] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> dict:
        event = {
            "id": repo.new_id("evt_"),
            "project_id": project_id,
            "agent": agent,
            "channel": channel,
            "level": level,
            "message": message,
            "progress": progress,
            "data": data or {},
            "created_at": now_iso(),
        }
        try:
            await repo.add_event(event)
        except Exception:  # noqa: BLE001 - logging must never break the flow
            pass
        for q in list(self._subs.get(project_id, ())):
            q.put_nowait(event)
        return event

    async def log(self, project_id: str, agent: str, message: str, **kw) -> dict:
        """Emit to the live_logs view (channel overridable)."""
        kw.setdefault("channel", "live_logs")
        return await self.emit(project_id, agent, message, **kw)

    async def trace(self, project_id: str, payload: dict) -> None:
        """Persist a prompt trace and surface a compact line in the console."""
        doc = {
            "id": repo.new_id("trc_"),
            "project_id": project_id,
            "created_at": now_iso(),
            **payload,
        }
        try:
            await repo.add_prompt_trace(doc)
        except Exception:  # noqa: BLE001
            pass
        await self.emit(
            project_id,
            payload.get("label", "llm"),
            f"{payload.get('model', 'llm')} · {payload.get('ms', 0)}ms",
            channel="prompt_trace",
            level="debug",
            data={"label": payload.get("label")},
        )

    async def error(self, project_id: str, agent: str, message: str, data=None) -> dict:
        doc = {
            "id": repo.new_id("err_"),
            "project_id": project_id,
            "agent": agent,
            "message": message,
            "data": data or {},
            "created_at": now_iso(),
        }
        try:
            await repo.add_error(doc)
        except Exception:  # noqa: BLE001
            pass
        return await self.emit(
            project_id, agent, message, channel="errors", level="error", data=data
        )

    async def subscribe(self, project_id: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[project_id].add(q)
        try:

            for ev in await repo.list_events(project_id, limit=200):
                yield ev
            while True:
                ev = await q.get()
                yield ev
        finally:
            self._subs[project_id].discard(q)


bus = EventBus()
