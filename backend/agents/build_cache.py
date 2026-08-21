"""Do not compile the same bytes twice.

Every verification stage ends by rebuilding, so it can tell whether its own
repair broke the compile. That is the right instinct, but a stage that
repaired nothing — or repaired only a test file — still pays sixty seconds
for `next build` to reach the same answer it gave a minute ago.

This records what was on disk the last time the build came back green. When
a stage asks for a build and nothing the compiler reads has changed since,
the answer is already known.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

log = logging.getLogger("buildcache")

# Everything `next build` actually reads.
SOURCE_DIRS = ("app", "components", "lib", "src", "public/generated")
CONFIG_FILES = ("package.json", "next.config.mjs", "next.config.js",
                "jsconfig.json", "tsconfig.json", "tailwind.config.js",
                "postcss.config.js", ".env.local")
SOURCE_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".json")

# Things the compiler never reads, so a change here cannot break a build.
IGNORE_RE = re.compile(
    r"(^|/)(node_modules|\.next|\.git|\.agentforge|coverage|"
    r"tests?|__tests__|e2e|playwright-report|test-results)(/|$)", re.I)
TEST_FILE_RE = re.compile(r"\.(?:test|spec)\.[jt]sx?$", re.I)


def _state_path(proj_dir) -> Path:
    return Path(proj_dir) / ".agentforge" / "build-state.json"


def _is_source(rel: str) -> bool:
    rel = str(rel).replace("\\", "/")
    if IGNORE_RE.search(rel) or TEST_FILE_RE.search(rel):
        return False
    if rel in CONFIG_FILES:
        return True
    return (rel.startswith(tuple(d + "/" for d in SOURCE_DIRS))
            and rel.endswith(SOURCE_EXT))


def fingerprint(proj_dir) -> str:
    """One hash over everything the compiler would read."""
    root = Path(proj_dir)
    digest = hashlib.sha256()
    rows = []
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if not _is_source(rel):
                continue
            rows.append((rel, path.stat().st_size, int(path.stat().st_mtime_ns)))
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"fingerprint walk failed under {root}: {e}")
        return ""
    if not rows:
        return ""
    for rel, size, mtime in sorted(rows):
        digest.update(f"{rel}:{size}:{mtime}\n".encode("utf-8"))
    return digest.hexdigest()[:32]


def mark_green(proj_dir, note: str = "") -> str:
    """Remember that these exact bytes compiled. Returns the fingerprint."""
    fp = fingerprint(proj_dir)
    if not fp:
        return ""
    path = _state_path(proj_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"green": fp, "note": str(note or "")[:120]}, indent=2),
            encoding="utf-8")
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"build state not saved: {e}")
    return fp


def clear(proj_dir) -> None:
    try:
        _state_path(proj_dir).unlink()
    except Exception:
        pass


def already_green(proj_dir) -> bool:
    """Whether these bytes are the ones that last compiled cleanly."""
    try:
        state = json.loads(_state_path(proj_dir).read_text(encoding="utf-8"))
    except Exception:
        return False
    known = str(state.get("green") or "")
    if not known:
        return False
    now = fingerprint(proj_dir)
    return bool(now) and now == known
