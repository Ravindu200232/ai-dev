"""Turning what the agents do into the websocket messages the UI already knows."""
from __future__ import annotations

_ARG_KEYS = ("path", "pattern", "command", "prefix")


def _args_label(args: dict) -> str:
    for key in _ARG_KEYS:
        if args.get(key):
            return str(args[key])[:80]
    return ""


def _log(level: str, text: str) -> dict:
    return {"type": "log", "level": level, "text": text}


def to_ws(event: dict) -> list:
    """One agent event → zero or more websocket messages."""
    kind = str((event or {}).get("type") or "")

    if kind == "stage":
        return [{"type": "step", "step": event.get("step"),
                 "status": event.get("status", "run")}]

    if kind == "progress":
        return [{"type": "progress", "step": event.get("step"),
                 "pct": event.get("pct", 0)}]

    if kind == "tool":
        label = _args_label(event.get("args") or {})
        return [_log("INFO", f"   🔧 {event.get('name')} {label}".rstrip())]

    if kind == "tool_result" and not event.get("ok", True):
        return [_log("WARN", f"   ⚠ {event.get('name')}: "
                             f"{str(event.get('preview') or '')[:160]}")]

    if kind == "tool_repeat":
        return [_log("WARN", f"   ↻ {event.get('name')} asked the same thing "
                             f"again — told to move on")]

    if kind == "assistant":
        first = str(event.get("text") or "").strip().splitlines()
        return [_log("INFO", f"   💬 {first[0][:200]}")] if first else []

    if kind == "compact":
        return [_log("INFO", f"   🧹 context compacted — {event.get('saved', 0)} "
                             f"tokens freed, {event.get('after', 0)} in use")]

    if kind == "scaffold":
        return [{"type": "file", "name": name, "size": 0, "content": ""}
                for name in event.get("files") or []]

    if kind == "plan":
        plan = event.get("plan") or {}
        return [{"type": "plan", "plan": plan},
                _log("INFO", f"   🧭 plan ready — {len(plan.get('files') or [])} "
                             f"files, {len(plan.get('steps') or [])} steps")]

    if kind in ("plan_revision", "plan_rejected"):
        note = event.get("note") or "rejected"
        return [_log("WARN", f"   🧭 plan sent back: {str(note)[:160]}")]

    if kind in ("built", "tests_written", "repaired"):
        files = event.get("files") or []
        messages = [{"type": "file", "name": name, "size": 0, "content": ""}
                    for name in files]
        return messages + [_log("INFO", f"   ✅ {kind.replace('_', ' ')}: "
                                        f"{len(files)} file(s)")]

    if kind == "suite":
        report = event.get("report") or {}
        return [{"type": "test_result",
                 "status": "pass" if report.get("green") else "fail",
                 "kind": report.get("kind"), "passed": report.get("passed", 0),
                 "failed": report.get("failed", 0),
                 "failures": report.get("failures") or []}]

    if kind == "qa_done":
        return [_log("INFO", f"   🧪 {event.get('summary')}")]

    if kind == "stopped":
        return [_log("WARN", f"   ⏹ stopped: {event.get('reason')} after "
                             f"{event.get('turns', 0)} turn(s)")]

    return []


def relay(emit):
    """Wrap a websocket `emit(dict)` so it can be handed in as `on_event`."""
    def sink(event: dict) -> None:
        for message in to_ws(event):
            emit(message)
    return sink
