from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any

from .security import redact_data
from .state import StateStore


class EventBus:
    def __init__(self, store: StateStore):
        self.store = store
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[Any] = set()
        self._lock = threading.RLock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def add_client(self, client: Any) -> None:
        with self._lock:
            self.clients.add(client)

    def remove_client(self, client: Any) -> None:
        with self._lock:
            self.clients.discard(client)

    def emit(
        self,
        run_id: str,
        event_type: str,
        stage: str,
        status: str,
        percent: int,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = redact_data(
            {
                "run_id": run_id,
                "type": event_type,
                "stage": stage,
                "status": status,
                "percent": max(0, min(100, int(percent))),
                "message": message,
                "data": data or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.store.add_event(run_id, event)
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._broadcast(event), self.loop)
        return event

    async def _broadcast(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        dead: list[Any] = []
        with self._lock:
            clients = list(self.clients)
        for client in clients:
            try:
                await client.send(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.remove_client(client)
