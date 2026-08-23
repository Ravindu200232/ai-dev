"""One place every message to the studio goes through."""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("forge.server")

CLIENTS = set()
LOOP = None


def use_loop(loop) -> None:
    """Remember the loop the websocket server runs on."""
    global LOOP
    LOOP = loop


def emit(message: dict) -> None:
    """Send to every connected studio. Safe to call from a worker thread."""
    if LOOP is None or not CLIENTS:
        return
    payload = json.dumps(message, ensure_ascii=False)

    async def send():
        dead = set()
        for socket in list(CLIENTS):
            try:
                await socket.send(payload)
            except Exception:                                      # noqa: BLE001
                dead.add(socket)
        CLIENTS.difference_update(dead)

    try:
        asyncio.run_coroutine_threadsafe(send(), LOOP)
    except RuntimeError as e:
        log.debug(f"could not emit: {e}")


def elog(level: str, text: str) -> None:
    log.info(f"[{level}] {text}")
    emit({"type": "log", "level": level, "text": text})


def estep(step: str, status: str) -> None:
    emit({"type": "step", "step": step, "status": status})


def eprog(step: str, pct: int) -> None:
    emit({"type": "progress", "step": step, "pct": pct})


def eerr(text: str) -> None:
    log.error(text)
    emit({"type": "error", "text": text})


def edone(project: str, preview: str = "/") -> None:
    emit({"type": "done", "project": project, "preview": preview})


def eproject(name: str) -> None:
    emit({"type": "project", "project": str(name)})
