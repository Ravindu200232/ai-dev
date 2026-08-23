"""One shape for a model reply, whichever daemon produced it."""
from __future__ import annotations

import json
import logging

log = logging.getLogger("forge.llm")


def normalise(reply) -> dict:
    """Any chat response → `{role, content, tool_calls}` with real dict args."""
    message = ((reply or {}).get("message") if isinstance(reply, dict) else None)
    if message is None:
        message = reply if isinstance(reply, dict) else {"content": str(reply or "")}
    calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = (call or {}).get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError:
                arguments = {}
        calls.append({
            "id": call.get("id") or f"call_{index}",
            "function": {"name": function.get("name") or "",
                         "arguments": arguments if isinstance(arguments, dict) else {}},
        })
    return {"role": "assistant",
            "content": str(message.get("content") or ""),
            "tool_calls": calls}


class Model:
    """A named Ollama model, asked for one turn at a time."""

    def __init__(self, name: str, client=None, *, options=None, think=None,
                 timeout: int = 900):
        self.name = name
        self._client = client
        self.options = dict(options or {})
        self.think = think
        self.timeout = timeout

    @property
    def client(self):
        if self._client is None:
            from .ollama import OllamaClient
            self._client = OllamaClient()
        return self._client

    def chat(self, messages, tools=None) -> dict:
        """One assistant turn, retried while the daemon is merely busy."""
        from .ollama import max_context, with_retry
        options = {"num_ctx": max_context(self.name), **self.options}
        return normalise(with_retry(
            lambda: self.client.chat(self.name, list(messages), tools=tools or None,
                                     options=options, think=self.think,
                                     timeout=self.timeout),
            what=self.name))

    def ask(self, system: str, user: str) -> str:
        """A single question with no tools — used for summaries and reviews."""
        reply = self.chat([{"role": "system", "content": system},
                           {"role": "user", "content": user}])
        return reply["content"]


class ScriptedModel:
    """A model that replays prepared replies. The tests' stand-in for Ollama."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, tools=None) -> dict:
        self.seen.append({"messages": list(messages),
                          "tools": [t["function"]["name"] for t in tools or []]})
        if not self.replies:
            return normalise({"content": "done"})
        reply = self.replies.pop(0)
        return normalise(reply if isinstance(reply, dict) else {"content": str(reply)})

    def ask(self, system: str, user: str) -> str:
        return self.chat([{"role": "system", "content": system},
                          {"role": "user", "content": user}])["content"]


def tool_call(name: str, **arguments) -> dict:
    """Build the assistant reply a test needs, without the JSON ceremony."""
    return {"content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}
