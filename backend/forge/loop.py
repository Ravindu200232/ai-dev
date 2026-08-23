"""The turn loop: ask, run what it asked for, hand back what happened."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .compaction import compact
from .modes import BUILD

log = logging.getLogger("forge.loop")

REPEAT_LIMIT = 3
_TOUCHED_RE = re.compile(r"^(created|replaced|edited|deleted) (\S+)", re.M)


@dataclass
class LoopResult:
    """What one run of the loop produced."""

    text: str = ""
    turns: int = 0
    stopped: str = "answered"          # answered | max_turns | cancelled
    tool_calls: list = field(default_factory=list)
    files: list = field(default_factory=list)
    compactions: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stopped == "answered"


class AgentLoop:
    """Drives one model, one toolset and one conversation to an answer."""

    def __init__(self, model, registry, convo, *, mode=BUILD, on_event=None,
                 summarise=None, max_turns=None, repeat_limit=REPEAT_LIMIT,
                 should_stop=None):
        self.model = model
        self.tools = registry
        self.convo = convo
        self.mode = mode
        self.on_event = on_event
        self.summarise = summarise
        self.max_turns = int(max_turns or mode.max_turns)
        self.repeat_limit = repeat_limit
        self.should_stop = should_stop
        self._seen: dict[str, int] = {}

    def _emit(self, event: str, **fields) -> None:
        if not self.on_event:
            return
        try:
            self.on_event({"type": event, "mode": self.mode.name, **fields})
        except Exception as e:                                     # noqa: BLE001
            log.debug(f"event sink failed: {e}")

    @staticmethod
    def _signature(name: str, arguments: dict) -> str:
        return f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"

    def _call(self, call: dict, result: LoopResult):
        """Run one tool call, refusing a request already answered twice."""
        function = call.get("function") or {}
        name = function.get("name") or ""
        arguments = function.get("arguments") or {}
        signature = self._signature(name, arguments)
        self._seen[signature] = self._seen.get(signature, 0) + 1

        if self._seen[signature] > self.repeat_limit:
            self._emit("tool_repeat", name=name)
            from .tools.spec import ToolResult
            outcome = ToolResult(name, f"you have already called {name} with "
                                       f"these arguments {self.repeat_limit} "
                                       f"times and the answer has not changed. "
                                       f"Use what you have, or do something "
                                       f"different.", ok=False)
        else:
            self._emit("tool", name=name, args=arguments)
            outcome = self.tools.dispatch(name, arguments)

        result.tool_calls.append({"name": name, "args": arguments,
                                  "ok": outcome.ok})
        for _, path in _TOUCHED_RE.findall(outcome.text):
            if path not in result.files:
                result.files.append(path)
        self._emit("tool_result", name=name, ok=outcome.ok,
                   preview=outcome.text[:200])
        return outcome.as_message(call.get("id", ""))

    def run(self, task: str = "") -> LoopResult:
        """Turn after turn until the model stops asking for tools."""
        if task:
            self.convo.add("user", task)
        result = LoopResult()

        for turn in range(1, self.max_turns + 1):
            if self.should_stop and self.should_stop():
                result.stopped = "cancelled"
                break

            report = compact(self.convo, self.summarise)
            if report.get("saved"):
                result.compactions.append(report)
                self._emit("compact", **report)

            reply = self.model.chat(self.convo.messages(), self.tools.schemas())
            result.turns = turn

            message = {"role": "assistant", "content": reply.get("content", "")}
            if reply.get("tool_calls"):
                message["tool_calls"] = reply["tool_calls"]
            self.convo.add_message(message)

            if message["content"]:
                self._emit("assistant", text=message["content"], turn=turn)

            if not reply.get("tool_calls"):
                result.text = message["content"]
                result.stopped = "answered"
                return result

            for call in reply["tool_calls"]:
                self.convo.add_message(self._call(call, result))
        else:
            result.stopped = "max_turns"

        result.text = self.convo.last_text()
        self._emit("stopped", reason=result.stopped, turns=result.turns)
        return result
