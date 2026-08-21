"""Reconcile persisted deployment runs with the real GitHub and AWS state."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from dfagents.deployer import _PERSISTED_ACTIVE_STATES, active_project_keys
from dfagents.monitor import MonitorAgent

from .models import RunState
from .state import StateStore


ORPHAN_GRACE = timedelta(minutes=20)


ANALYSIS_ORPHAN_GRACE = timedelta(minutes=90)

SUPERVISOR_INTERVAL_SECONDS = 15.0


_RECONCILED_STATES = _PERSISTED_ACTIVE_STATES | {RunState.ANALYZING.value}


def _is_orphaned(run: dict[str, Any]) -> bool:
    """True when no live in-process worker owns this run's project."""
    try:
        project_key = str(Path(run["project_path"]).resolve()).lower()
    except (OSError, ValueError):
        return False
    return project_key not in active_project_keys()


def _updated_before(run: dict[str, Any], cutoff: datetime) -> bool:
    try:
        updated = datetime.fromisoformat(str(run.get("updated_at", "")))
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated < cutoff


def reconcile_once(
    store: StateStore,
    monitor: MonitorAgent,
    emit: Callable[..., object] | None = None,
) -> list[dict[str, str]]:
    """Bring every abandoned active run back in line with reality."""
    emit = emit or (lambda *args, **kwargs: None)
    now = datetime.now(timezone.utc)
    cutoff = now - ORPHAN_GRACE
    analysis_cutoff = now - ANALYSIS_ORPHAN_GRACE
    reconciled: list[dict[str, str]] = []
    for run in store.list_runs(limit=250):
        if run["state"] not in _RECONCILED_STATES or not _is_orphaned(run):
            continue
        run_id = run["id"]
        previous = run["state"]
        repository = (run.get("repo") or {}).get("repository", "")
        if previous == RunState.ANALYZING.value:

            if not _updated_before(run, analysis_cutoff):
                continue
            store.transition_run(
                run_id,
                RunState.FAILED,
                error="Analysis worker did not survive an application restart; re-run analysis.",
            )
        elif repository:

            try:
                monitor.snapshot(run_id)
            except Exception as exc:  # noqa: BLE001 - one bad run must not stop the sweep
                emit(run_id, "warning", "supervisor", "warning", 0, f"Reconciliation deferred: {exc}")
                continue
        elif _updated_before(run, cutoff):

            store.transition_run(
                run_id,
                RunState.FAILED,
                error="Deployment worker did not survive an application restart; re-run analysis.",
            )
        else:
            continue
        current = (store.get_run(run_id) or {}).get("state", previous)
        if current == previous:
            continue
        reconciled.append({"run_id": run_id, "from": previous, "to": current})
        emit(
            run_id,
            "state",
            "supervisor",
            "complete" if current == RunState.LIVE.value else "failed"
            if current == RunState.FAILED.value
            else "running",
            100 if current in {RunState.LIVE.value, RunState.FAILED.value} else 80,
            f"Recovered abandoned {'analysis' if previous == RunState.ANALYZING.value else 'deployment'}: "
            f"{previous} to {current}",
            {"previous_state": previous, "state": current},
        )
    return reconciled


def start_supervisor(
    store: StateStore,
    monitor: MonitorAgent,
    emit: Callable[..., object] | None = None,
    interval: float = SUPERVISOR_INTERVAL_SECONDS,
) -> threading.Thread:
    """Run reconcile_once on a loop for as long as the application is alive."""

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                reconcile_once(store, monitor, emit)
            except Exception:  # noqa: BLE001 - a transient AWS/GitHub error must not end the loop
                continue

    thread = threading.Thread(target=loop, name="deployment-supervisor", daemon=True)
    thread.start()
    return thread
