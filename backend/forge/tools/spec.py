"""What a tool is, and how a model is told about it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# Long tool output is what fills a context window fastest, so every result
# is capped here rather than at each call site.
MAX_RESULT_CHARS = 6_000


@dataclass(frozen=True)
class Param:
    """One argument in a tool's function schema."""

    name: str
    description: str
    type: str = "string"
    required: bool = True


@dataclass(frozen=True)
class Tool:
    """A callable the model may ask for by name."""

    name: str
    description: str
    run: Callable[..., str]
    params: tuple = ()
    writes: bool = False          # withheld while the agent is planning
    cost: str = "cheap"           # cheap | slow — slow tools are rate limited

    def schema(self) -> dict:
        """The function-calling shape Ollama and the OpenAI API both accept."""
        properties = {
            p.name: {"type": p.type, "description": p.description}
            for p in self.params
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [p.name for p in self.params if p.required],
                },
            },
        }


@dataclass
class ToolResult:
    """What came back, already trimmed to something a context can hold."""

    tool: str
    text: str
    ok: bool = True
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        body = str(self.text or "")
        if len(body) > MAX_RESULT_CHARS:
            kept = len(body) - MAX_RESULT_CHARS
            body = body[:MAX_RESULT_CHARS] + f"\n… {kept} more characters cut"
            self.meta["truncated"] = True
        self.text = body

    def as_message(self, call_id: str = "") -> dict:
        """The `tool` role message that goes back into the conversation."""
        message = {"role": "tool", "content": self.text, "name": self.tool}
        if call_id:
            message["tool_call_id"] = call_id
        return message
