"""Forge tools bound to a host's writer instead of writing straight to disk.

The architect's `write_file` does more than save bytes: it merges
package.json, canonicalises a path, removes a shadowed .js/.jsx twin and
emits the file event the studio draws. An edit that bypassed it would lose
all of that silently, so the write half of the toolset delegates.
"""
from __future__ import annotations

from ..tools import ToolRegistry
from ..tools.paths import PathError, resolve
from ..tools.read import read_tools
from ..tools.spec import Param, Tool


def _current(arch, project_dir, path: str) -> str | None:
    """What the file holds now — the host's copy first, then disk."""
    files = getattr(arch, "files", None) or {}
    if path in files:
        return str(files[path])
    target = resolve(project_dir, path)
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="replace")


def host_write_tools(arch, project_dir, writer=None) -> list:
    """`write_file` / `edit_file`, routed through the host.

    `writer(path, content) -> bool` overrides `arch.write_file` for a caller
    that guards or announces each write itself.
    """
    def _save(path: str, body: str, verb: str) -> str:
        """Hand the write to the host and answer in the loop's vocabulary.

        The wording matters: the loop reads `created` / `replaced` / `edited`
        back out of tool results to know which files a run touched. The writer
        is resolved here, not at build time: a read-only registry still builds
        these tools, it just never offers them.
        """
        save = writer or getattr(arch, "write_file", None)
        if save is None:
            return f"refused: this agent has nowhere to write {path}"
        if not save(path, body):
            return f"{path} already held exactly that — nothing written"
        return f"{verb} {path} ({len(body.splitlines())} lines)"

    def write(path: str, content: str) -> str:
        resolve(project_dir, path)                 # refuse anything outside
        existed = _current(arch, project_dir, path) is not None
        body = str(content or "")
        if not body.strip():
            return f"refused: {path} would be empty"
        return _save(path, body, "replaced" if existed else "created")

    def edit(path: str, find: str, replace: str) -> str:
        body = _current(arch, project_dir, path)
        if body is None:
            return f"{path}: no such file — use write_file to create it"
        hits = body.count(find)
        if hits == 0:
            return f"{path}: that exact text is not in the file — read it again"
        if hits > 1:
            return (f"{path}: that text appears {hits} times. Include more "
                    f"surrounding lines so the match is unique.")
        return _save(path, body.replace(find, replace, 1), "edited")

    return [
        Tool("write_file", "Create or overwrite a file with complete contents.",
             write,
             (Param("path", "File to write, project-relative."),
              Param("content", "The whole file. Never a fragment or a diff.")),
             writes=True),
        Tool("edit_file", "Replace one exact, unique block of text in a file.",
             edit,
             (Param("path", "File to edit."),
              Param("find", "Exact text to replace, unique in the file."),
              Param("replace", "What to put in its place.")),
             writes=True),
    ]


def capture_write_tools(writes: dict, arch, project_dir) -> list:
    """Write tools that record instead of applying.

    A caller that has to inspect a rewrite before accepting it — the element
    editor rejects one that overran its selection, and escalates one that
    touched a file it did not own — needs the content without the change
    having happened yet.
    """
    def _record(path: str, body: str, verb: str) -> str:
        writes[path] = body
        return f"{verb} {path} ({len(body.splitlines())} lines)"

    def write(path: str, content: str) -> str:
        resolve(project_dir, path)
        body = str(content or "")
        if not body.strip():
            return f"refused: {path} would be empty"
        existed = _current(arch, project_dir, path) is not None
        return _record(path, body, "replaced" if existed else "created")

    def edit(path: str, find: str, replace: str) -> str:
        body = writes.get(path) or _current(arch, project_dir, path)
        if body is None:
            return f"{path}: no such file — use write_file to create it"
        hits = body.count(find)
        if hits == 0:
            return f"{path}: that exact text is not in the file — read it again"
        if hits > 1:
            return (f"{path}: that text appears {hits} times. Include more "
                    f"surrounding lines so the match is unique.")
        return _record(path, body.replace(find, replace, 1), "edited")

    return [
        Tool("write_file", "Create or overwrite a file with complete contents.",
             write,
             (Param("path", "File to write, project-relative."),
              Param("content", "The whole file. Never a fragment or a diff.")),
             writes=True),
        Tool("edit_file", "Replace one exact, unique block of text in a file.",
             edit,
             (Param("path", "File to edit."),
              Param("find", "Exact text to replace, unique in the file."),
              Param("replace", "What to put in its place.")),
             writes=True),
    ]


def edit_registry(arch, project_dir, *, read_only: bool = False,
                  capture: dict = None, writer=None) -> ToolRegistry:
    """Read from disk; write through the host, or into `capture` when given.

    No shell tool: an edit changes files, it does not build them.
    """
    writers = (capture_write_tools(capture, arch, project_dir) if capture is not None
               else host_write_tools(arch, project_dir, writer))
    return ToolRegistry(list(read_tools(project_dir)) + list(writers),
                        read_only=read_only)


__all__ = ["PathError", "capture_write_tools", "edit_registry", "host_write_tools"]
