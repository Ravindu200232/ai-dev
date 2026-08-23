"""The registry: what the model may call, and what happens when it does."""
from __future__ import annotations

import json
import logging

from .paths import PathError
from .spec import Tool, ToolResult

log = logging.getLogger("forge.tools")


class ToolRegistry:
    """Holds the tools and dispatches calls, refusing anything out of mode."""

    def __init__(self, tools=(), *, read_only: bool = False):
        self._tools: dict[str, Tool] = {}
        self.read_only = bool(read_only)
        self.calls: list[dict] = []
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def __contains__(self, name) -> bool:
        return name in self._tools

    def available(self) -> list:
        """The tools this mode exposes — write tools vanish when read-only."""
        return [t for t in self._tools.values() if not (self.read_only and t.writes)]

    def names(self) -> list:
        return [t.name for t in self.available()]

    def schemas(self) -> list:
        """What is sent as the `tools` payload of a chat request."""
        return [t.schema() for t in self.available()]

    @staticmethod
    def _args(raw) -> dict:
        """Models send arguments as a dict or as a JSON string. Accept both."""
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def dispatch(self, name: str, arguments=None) -> ToolResult:
        """Run one call. Every failure comes back as text the model can use."""
        args = self._args(arguments)
        self.calls.append({"tool": name, "args": args})
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name, f"there is no tool called {name!r}. "
                                    f"Available: {', '.join(self.names())}",
                              ok=False)
        if self.read_only and tool.writes:
            return ToolResult(name, f"{name} is a write tool and this is plan "
                                    f"mode. Finish the plan first.", ok=False)
        try:
            return ToolResult(name, str(tool.run(**args)))
        except PathError as e:
            return ToolResult(name, f"refused: {e}", ok=False)
        except TypeError as e:
            expected = ", ".join(p.name for p in tool.params) or "no arguments"
            return ToolResult(name, f"{name} takes {expected} ({e})", ok=False)
        except Exception as e:                                     # noqa: BLE001
            log.warning(f"{name} failed: {e}")
            return ToolResult(name, f"{name} failed: {e}", ok=False)

    def describe(self) -> str:
        """A plain-text list for models that do not do function calling well."""
        return "\n".join(f"- {t.name}: {t.description}" for t in self.available())
