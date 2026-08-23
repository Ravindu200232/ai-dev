"""The read-only tools: list (ls), read, grep, tree."""
from __future__ import annotations

import re
from pathlib import Path

from .paths import MAX_FILE_BYTES, ignored, rel, resolve, walk
from .spec import Param, Tool

MAX_MATCHES = 40
MAX_LINES = 400


def _size(path: Path) -> str:
    try:
        return f"{path.stat().st_size:>7,}"
    except OSError:
        return "      ?"


def list_files(root, path: str = "", depth: int = 2) -> str:
    """`ls -la` for the model: names, sizes and directory markers."""
    entries = walk(root, path, depth=int(depth or 2))
    if not entries:
        return f"{path or '.'} is empty or does not exist"
    lines = [f"{path or '.'} ({len(entries)} entries, depth {depth})"]
    for entry in entries[:300]:
        name = rel(root, entry)
        lines.append(f"  dir  {name}/" if entry.is_dir()
                     else f"  {_size(entry)}  {name}")
    if len(entries) > 300:
        lines.append(f"  … {len(entries) - 300} more not listed")
    return "\n".join(lines)


def read_file(root, path: str, start: int = 1, limit: int = MAX_LINES) -> str:
    """A file, numbered, capped, and honest about what it left out."""
    target = resolve(root, path)
    if not target.is_file():
        return f"{path}: no such file"
    if target.stat().st_size > MAX_FILE_BYTES:
        return (f"{path} is {target.stat().st_size:,} bytes — too large to read "
                f"whole. Grep it, or read a line range.")
    body = target.read_text(encoding="utf-8", errors="replace").splitlines()
    first = max(1, int(start or 1))
    window = body[first - 1:first - 1 + max(1, int(limit or MAX_LINES))]
    numbered = [f"{first + i:>5} {line}" for i, line in enumerate(window)]
    tail = (f"\n… {len(body) - first + 1 - len(window)} more lines"
            if first - 1 + len(window) < len(body) else "")
    return f"{path} ({len(body)} lines)\n" + "\n".join(numbered) + tail


def grep(root, pattern: str, path: str = "", glob: str = "") -> str:
    """Search file contents. The answer is where to look, not the whole file."""
    try:
        rx = re.compile(pattern, re.I)
    except re.error as e:
        return f"bad pattern {pattern!r}: {e}"
    base = resolve(root, path)
    files = [base] if base.is_file() else [
        p for p in walk(root, path, depth=8) if p.is_file()]
    if glob:
        files = [p for p in files if Path(p.name).match(glob)]
    hits = []
    for target in files:
        if ignored(target, root) or target.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            body = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(body.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel(root, target)}:{number}: {line.strip()[:180]}")
            if len(hits) >= MAX_MATCHES:
                return "\n".join(hits) + f"\n… stopped at {MAX_MATCHES} matches"
    return "\n".join(hits) or f"no match for {pattern!r}"


def tree(root, path: str = "", depth: int = 3) -> str:
    """The shape of a directory without the file sizes."""
    entries = walk(root, path, depth=int(depth or 3))
    lines = []
    for entry in entries[:400]:
        name = rel(root, entry)
        indent = "  " * name.count("/")
        lines.append(f"{indent}{entry.name}{'/' if entry.is_dir() else ''}")
    return "\n".join(lines) or f"{path or '.'} is empty"


def read_tools(root) -> list:
    """Every tool that only ever looks — the plan-mode toolset."""
    return [
        Tool("list_files", "List files and directories, like `ls`. Start here.",
             lambda path="", depth=2: list_files(root, path, depth),
             (Param("path", "Directory to list, project-relative. '' is the root.",
                    required=False),
              Param("depth", "How many levels deep, 1-4.", "integer", False))),
        Tool("read_file", "Read a file with line numbers.",
             lambda path, start=1, limit=MAX_LINES: read_file(root, path, start, limit),
             (Param("path", "File to read, project-relative."),
              Param("start", "First line to read.", "integer", False),
              Param("limit", "How many lines to read.", "integer", False))),
        Tool("grep", "Search file contents for a regular expression.",
             lambda pattern, path="", glob="": grep(root, pattern, path, glob),
             (Param("pattern", "Regular expression to look for."),
              Param("path", "Directory or file to search.", required=False),
              Param("glob", "Filename filter such as *.tsx.", required=False))),
        Tool("tree", "Show the directory structure without file sizes.",
             lambda path="", depth=3: tree(root, path, depth),
             (Param("path", "Directory to show.", required=False),
              Param("depth", "Levels deep, 1-5.", "integer", False))),
    ]
