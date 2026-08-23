"""Tools an agent may call, assembled for one project directory."""
from __future__ import annotations

from .paths import IGNORED, PathError, rel, resolve, walk
from .read import read_tools
from .registry import ToolRegistry
from .shell import shell_tools, validate
from .spec import Param, Tool, ToolResult
from .write import write_tools


def build_registry(project_dir, *, read_only: bool = False,
                   shell: bool = True) -> ToolRegistry:
    """Every tool, pointed at one project. Read-only hides the write half."""
    tools = list(read_tools(project_dir))
    tools += list(write_tools(project_dir))
    if shell:
        tools += list(shell_tools(project_dir))
    return ToolRegistry(tools, read_only=read_only)


__all__ = ["IGNORED", "Param", "PathError", "Tool", "ToolRegistry", "ToolResult",
           "build_registry", "read_tools", "rel", "resolve", "shell_tools",
           "validate", "walk", "write_tools"]
