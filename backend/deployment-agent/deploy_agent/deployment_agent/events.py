from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .security import redact_data
from .state import StateStore


class EventBus:
    def __init__(self, store: StateStore):
        self.store = store

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
        return event
