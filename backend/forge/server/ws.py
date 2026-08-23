"""The websocket protocol the studio speaks."""
from __future__ import annotations

import json
import logging

from . import events, runs, state

log = logging.getLogger("forge.server.ws")

HELLO = {"type": "log", "level": "INFO",
         "text": "✅ AgentForge connected — enter a prompt and click Build"}


def handle(message: dict) -> None:
    """Act on one message. Unknown types are ignored, not an error."""
    kind = str(message.get("type") or "")

    if kind in ("agent_build", "forge_build"):
        prompt = str(message.get("prompt") or "").strip()
        if not prompt:
            return
        runs.start(prompt,
                   model=str(message.get("builder_model")
                             or message.get("model") or "").strip(),
                   qa_model=str(message.get("qa_model") or "").strip(),
                   project=str(message.get("project") or "").strip(),
                   kinds=tuple(message.get("kinds") or ("unit", "e2e")))

    elif kind in ("chat", "agent_update", "edit"):
        project = str(message.get("project") or "").strip()
        prompt = str(message.get("prompt") or "").strip()
        if project and prompt:
            runs.edit(prompt, project,
                      model=str(message.get("model") or "").strip(),
                      focus=tuple(message.get("files") or ()))

    elif kind == "plan_decision":
        if not runs.decide(str(message.get("verdict") or "").strip(),
                           str(message.get("note") or "")):
            events.elog("WARN", "   🧭 no run is waiting for a plan decision")

    elif kind in ("cancel", "build_cancel"):
        if not runs.cancel():
            events.elog("INFO", "   nothing is running")

    else:
        log.debug(f"ignored message: {kind}")


async def serve(socket, path=None):
    """One studio connection, for as long as it stays open."""
    events.CLIENTS.add(socket)
    log.info(f"studio connected ({len(events.CLIENTS)})")
    try:
        await socket.send(json.dumps(HELLO))
        async for raw in socket:
            try:
                handle(json.loads(raw))
            except json.JSONDecodeError:
                log.warning("ignored a message that was not JSON")
            except Exception as e:                                 # noqa: BLE001
                log.exception("message handler failed")
                events.eerr(f"{type(e).__name__}: {e}")
    except Exception as e:                                         # noqa: BLE001
        log.debug(f"connection closed: {e}")
    finally:
        events.CLIENTS.discard(socket)
        log.info(f"studio disconnected ({len(events.CLIENTS)})")


del state
