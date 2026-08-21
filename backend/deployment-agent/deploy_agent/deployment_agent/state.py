from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DB_PATH
from .models import RunState
from .security import json_dumps_safe


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    ALLOWED_TRANSITIONS = {
        RunState.DRAFT.value: {RunState.ANALYZING.value},
        RunState.ANALYZING.value: {
            RunState.REVIEW_READY.value,
            RunState.FAILED.value,
            RunState.CANCELLED.value,
        },
        RunState.REVIEW_READY.value: {
            RunState.BOOTSTRAPPING.value,
            RunState.FAILED.value,
            RunState.DESTROYED.value,
            RunState.CANCELLED.value,
        },
        RunState.BOOTSTRAPPING.value: {
            RunState.CI_RUNNING.value,
            RunState.FAILED.value,
            RunState.ROLLED_BACK.value,
            RunState.CANCELLED.value,
        },
        RunState.CI_RUNNING.value: {
            RunState.DEPLOYING.value,
            RunState.VALIDATING.value,
            RunState.FAILED.value,
            RunState.ROLLED_BACK.value,
            RunState.CANCELLED.value,
        },
        RunState.DEPLOYING.value: {
            RunState.VALIDATING.value,
            RunState.FAILED.value,
            RunState.ROLLED_BACK.value,
            RunState.CANCELLED.value,
        },
        RunState.VALIDATING.value: {
            RunState.LIVE.value,
            RunState.FAILED.value,
            RunState.ROLLED_BACK.value,
            RunState.CANCELLED.value,
        },
        RunState.LIVE.value: {
            RunState.DEPLOYING.value,
            RunState.FAILED.value,
            RunState.ROLLED_BACK.value,
            RunState.DESTROYED.value,
        },
        RunState.FAILED.value: {
            RunState.ANALYZING.value,
            RunState.BOOTSTRAPPING.value,
            RunState.ROLLED_BACK.value,
            RunState.DESTROYED.value,
        },
        RunState.ROLLED_BACK.value: {RunState.BOOTSTRAPPING.value, RunState.DESTROYED.value},

        RunState.CANCELLED.value: {
            RunState.BOOTSTRAPPING.value,
            RunState.DESTROYED.value,
        },

        RunState.DESTROYED.value: set(),
    }

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    spec_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    readiness_json TEXT NOT NULL DEFAULT '{}',
                    monitor_json TEXT NOT NULL DEFAULT '{}',
                    repo_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, id);
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, path)
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create_run(self, run_id: str, project_name: str, project_path: str, staged_path: str) -> None:
        now = utc_now()
        with self._lock, self._connection() as conn:
            conn.execute(
                """INSERT INTO runs
                (id, project_name, project_path, staged_path, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'DRAFT', ?, ?)""",
                (run_id, project_name, project_path, staged_path, now, now),
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        allowed = {
            "project_name",
            "project_path",
            "staged_path",
            "state",
            "spec_json",
            "plan_json",
            "readiness_json",
            "monitor_json",
            "repo_json",
            "error",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            if key.endswith("_json") and not isinstance(value, str):
                value = json_dumps_safe(value)
            values.append(value)
        if not assignments:
            return
        assignments.append("updated_at = ?")
        values.extend([utc_now(), run_id])
        with self._lock, self._connection() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(assignments)} WHERE id = ?", values)

    def transition_run(self, run_id: str, target: RunState | str, **fields: Any) -> None:
        target_value = target.value if isinstance(target, RunState) else str(target)
        with self._lock:
            run = self.get_run(run_id)
            if not run:
                raise ValueError("Run not found")
            current = run["state"]
            if target_value != current and target_value not in self.ALLOWED_TRANSITIONS.get(current, set()):
                raise RuntimeError(f"Invalid deployment state transition: {current} -> {target_value}")
            self.update_run(run_id, state=target_value, **fields)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._decode_run(row) if row else None

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    @staticmethod
    def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("spec_json", "plan_json", "readiness_json", "monitor_json", "repo_json"):
            try:
                data[key[:-5]] = json.loads(data.pop(key) or "{}")
            except json.JSONDecodeError:
                data[key[:-5]] = {}
        return data

    def add_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO events(run_id, event_json, created_at) VALUES (?, ?, ?)",
                (run_id, json_dumps_safe(event), utc_now()),
            )

    def get_events(self, run_id: str, after_id: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, event_json FROM events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                (run_id, after_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            event = json.loads(row["event_json"])
            event["event_id"] = row["id"]
            result.append(event)
        return result

    def set_artifacts(self, run_id: str, records: list[dict[str, Any]]) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("DELETE FROM artifacts WHERE run_id = ?", (run_id,))
            conn.executemany(
                "INSERT INTO artifacts(run_id, path, record_json) VALUES (?, ?, ?)",
                [(run_id, item["path"], json_dumps_safe(item)) for item in records],
            )

    def get_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT record_json FROM artifacts WHERE run_id = ? ORDER BY path", (run_id,)
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def add_evidence(self, run_id: str, name: str, path: str) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO evidence(run_id, name, path, created_at) VALUES (?, ?, ?, ?)",
                (run_id, name, path, utc_now()),
            )

    def list_evidence(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, name, path, verified, created_at FROM evidence WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def verify_evidence(self, run_id: str, evidence_id: int, verified: bool) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE evidence SET verified = ? WHERE run_id = ? AND id = ?",
                (1 if verified else 0, run_id, evidence_id),
            )
