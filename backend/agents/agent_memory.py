"""A notebook the agents keep for themselves, per project.

`lessons.py` is the long-lived store: what went wrong across every build.
This is the short-lived one — what THIS run has already tried, ruled out and
decided. It exists so an agent stops re-proposing the fix it just watched
fail, which is the single thing that most makes a loop feel mechanical
rather than like someone working through a problem.

The agents write to it themselves with `<remember>`, so what gets kept is
what the model thought was worth keeping.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("memory")

KINDS = ("goal", "tried", "learned", "decided", "avoid")

MAX_ENTRIES = 200
MAX_TEXT = 400
MAX_IN_PROMPT = 14
MAX_PROMPT_CHARS = 2200

# Kinds worth re-reading first, most useful first.
PROMPT_ORDER = ("goal", "avoid", "decided", "learned", "tried")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def store_path(project_dir) -> Path:
    return Path(project_dir) / ".agentforge" / "memory.json"


def load(project_dir) -> dict:
    """Everything remembered about this project, or an empty notebook."""
    path = store_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"memory unreadable at {path}: {e}")
    return {"entries": []}


def save(project_dir, data: dict) -> bool:
    """Write the notebook, replacing it whole so a crash cannot half-write."""
    path = store_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(data, indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=str(path.parent),
                prefix=".memory-", suffix=".json", delete=False) as fh:
            fh.write(body)
            tmp = Path(fh.name)
        tmp.replace(path)
        return True
    except Exception as e:                                      # noqa: BLE001
        log.debug(f"memory not saved at {path}: {e}")
        return False


class AgentMemory:
    """One project's notebook, shared by every agent working on it."""

    def __init__(self, project_dir, agent: str = "agent"):
        self.project_dir = Path(project_dir)
        self.agent = str(agent or "agent")
        self.data = load(self.project_dir)

    # ------------------------------------------------------------ writing
    def remember(self, kind: str, text: str, *, agent: str = "") -> bool:
        """Keep one short note. Returns False when it is already known."""
        kind = str(kind or "learned").strip().lower()
        if kind not in KINDS:
            kind = "learned"
        text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]
        if len(text) < 8:
            return False
        if self.knows(text):
            return False
        self.data.setdefault("entries", []).append({
            "when": _now(), "kind": kind, "agent": agent or self.agent,
            "text": text,
        })
        self.data["entries"] = self.data["entries"][-MAX_ENTRIES:]
        return save(self.project_dir, self.data)

    def knows(self, text: str) -> bool:
        """Whether this note, or one close enough, is already written down."""
        want = _norm(text)
        if not want:
            return True
        words = set(want.split())
        for row in self.data.get("entries", []):
            got = _norm(row.get("text"))
            if got == want:
                return True
            other = set(got.split())
            if words and other:
                overlap = len(words & other) / max(len(words), len(other))
                if overlap > 0.82:
                    return True
        return False

    # ------------------------------------------------------------ reading
    def entries(self, kind: str = "") -> list:
        rows = list(self.data.get("entries", []))
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        return rows

    def recall(self, query: str = "", limit: int = 8) -> list:
        """Notes that share wording with `query`, newest first."""
        rows = list(reversed(self.entries()))
        want = set(_norm(query).split())
        if not want:
            return rows[:limit]
        scored = []
        for row in rows:
            got = set(_norm(row.get("text")).split())
            hit = len(want & got)
            if hit:
                scored.append((hit, row))
        scored.sort(key=lambda pair: -pair[0])
        return [row for _, row in scored[:limit]]

    def block(self, limit: int = MAX_IN_PROMPT) -> str:
        """The notebook as a prompt section, or '' when there is nothing."""
        rows = self.entries()
        if not rows:
            return ""
        picked, seen = [], set()
        for kind in PROMPT_ORDER:
            for row in reversed(rows):
                if row.get("kind") != kind:
                    continue
                key = _norm(row.get("text"))
                if key in seen:
                    continue
                seen.add(key)
                picked.append(row)
                if len(picked) >= limit:
                    break
            if len(picked) >= limit:
                break
        if not picked:
            return ""
        lines = ["WHAT THIS RUN ALREADY KNOWS — you wrote these yourself. Do "
                 "not repeat an attempt listed under `tried`, and do not "
                 "propose anything listed under `avoid`."]
        used = len(lines[0])
        for row in picked:
            line = f"  [{row.get('kind', 'learned')}] {row.get('text', '')}"
            if used + len(line) > MAX_PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    def forget_all(self) -> bool:
        self.data = {"entries": []}
        return save(self.project_dir, self.data)


def memory_for(arch, agent: str = "agent") -> AgentMemory:
    """The notebook for whatever project this agent is working on."""
    return AgentMemory(getattr(arch, "project_dir", "."), agent)
