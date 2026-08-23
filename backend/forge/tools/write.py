"""The write tools. Build mode only — plan mode never sees them."""
from __future__ import annotations

from .paths import PathError, ignored, resolve
from .spec import Param, Tool

MAX_WRITE_CHARS = 60_000


def write_file(root, path: str, content: str) -> str:
    """Create or replace a file. Parent directories are made on the way."""
    target = resolve(root, path)
    if ignored(target, root):
        raise PathError(f"{path} is in a generated directory — do not write there")
    body = str(content or "")
    if len(body) > MAX_WRITE_CHARS:
        return (f"refused: {path} is {len(body):,} characters. Split it — no file "
                f"in this project should be that large.")
    existed = target.is_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    verb = "replaced" if existed else "created"
    return f"{verb} {path} ({len(body.splitlines())} lines)"


def edit_file(root, path: str, find: str, replace: str) -> str:
    """Replace one exact block. Fails loudly rather than guessing."""
    target = resolve(root, path)
    if not target.is_file():
        return f"{path}: no such file — use write_file to create it"
    body = target.read_text(encoding="utf-8", errors="replace")
    hits = body.count(find)
    if hits == 0:
        return f"{path}: that exact text is not in the file — read it again"
    if hits > 1:
        return (f"{path}: that text appears {hits} times. Include more "
                f"surrounding lines so the match is unique.")
    target.write_text(body.replace(find, replace, 1), encoding="utf-8")
    return f"edited {path}"


def delete_file(root, path: str) -> str:
    """Remove a file the plan says should not exist."""
    target = resolve(root, path)
    if not target.is_file():
        return f"{path}: nothing to delete"
    target.unlink()
    return f"deleted {path}"


def write_tools(root) -> list:
    """The tools that change the project."""
    return [
        Tool("write_file", "Create or overwrite a file with complete contents.",
             lambda path, content: write_file(root, path, content),
             (Param("path", "File to write, project-relative."),
              Param("content", "The whole file. Never a fragment or a diff.")),
             writes=True),
        Tool("edit_file", "Replace one exact, unique block of text in a file.",
             lambda path, find, replace: edit_file(root, path, find, replace),
             (Param("path", "File to edit."),
              Param("find", "Exact text to replace, unique in the file."),
              Param("replace", "What to put in its place.")),
             writes=True),
        Tool("delete_file", "Delete a file.",
             lambda path: delete_file(root, path),
             (Param("path", "File to delete."),), writes=True),
    ]
