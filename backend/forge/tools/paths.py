"""Sandboxed paths: nothing outside the project, nothing from the noise dirs."""
from __future__ import annotations

from pathlib import Path

# Directories no agent should ever read, list or walk into.
IGNORED = {
    "node_modules", ".next", ".git", ".turbo", ".venv", "__pycache__",
    "dist", "build", "coverage", "test-results", "playwright-report",
    ".agentforge",
}

MAX_FILE_BYTES = 96_000


class PathError(ValueError):
    """A path the sandbox refuses to touch."""


def clean(value) -> str:
    """`./app\\page.tsx` → `app/page.tsx`."""
    text = str(value or "").strip().replace("\\", "/").strip("/")
    while text.startswith("./"):
        text = text[2:]
    return text


def resolve(root, relative) -> Path:
    """Absolute path inside `root`. Raises PathError for anything outside."""
    base = Path(root).resolve()
    raw = str(relative or "").strip()
    # Checked before cleaning: cleaning strips the leading slash, which would
    # quietly turn `/etc/passwd` into a path inside the project.
    if raw.startswith("~") or Path(raw.replace("\\", "/")).is_absolute():
        raise PathError(f"{relative!r} must be a project-relative path")
    text = clean(raw)
    target = (base / text).resolve() if text else base
    if target != base and base not in target.parents:
        raise PathError(f"{relative!r} escapes the project directory")
    return target


def rel(root, path) -> str:
    """The project-relative key for a path, or its name when it is outside."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def ignored(path, root) -> bool:
    """True when any segment below the root is a noise directory."""
    return any(part in IGNORED for part in Path(rel(root, path)).parts)


def walk(root, start=None, depth=2):
    """Entries under `start`, breadth-first, capped at `depth` levels."""
    base = Path(root).resolve()
    top = resolve(base, start) if start else base
    if not top.is_dir():
        return []
    found, frontier = [], [(top, 0)]
    while frontier:
        current, level = frontier.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            continue
        for entry in entries:
            if ignored(entry, base) or entry.name.startswith("."):
                continue
            found.append(entry)
            if entry.is_dir() and level + 1 < depth:
                frontier.append((entry, level + 1))
    return found
