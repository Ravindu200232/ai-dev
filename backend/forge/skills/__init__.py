"""Skills: short guides loaded into the prompt only when a task needs them.

A skill is a markdown file with front matter. Loading all of them would undo
the point of a context budget, so `select` scores them against the task text
and only the top few are rendered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
MAX_SKILLS = 3
MAX_SKILL_CHARS = 4_200

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Skill:
    """One guide, and the words that mean a task should see it."""

    name: str
    description: str = ""
    triggers: tuple = ()
    body: str = ""
    path: str = ""
    always: bool = False

    def score(self, text: str) -> int:
        """How many of this skill's triggers appear in the task text."""
        low = str(text or "").lower()
        return sum(1 for trigger in self.triggers if trigger and trigger in low)

    def render(self) -> str:
        body = self.body.strip()[:MAX_SKILL_CHARS]
        return f"--- skill: {self.name} ---\n{body}"


def _parse(text: str, path: Path) -> Skill:
    front, body = {}, text
    match = _FRONT_RE.match(text)
    if match:
        body = text[match.end():]
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                front[key.strip().lower()] = value.strip()
    triggers = tuple(t.strip().lower() for t in
                     (front.get("triggers") or "").split(",") if t.strip())
    return Skill(name=front.get("name") or path.stem,
                 description=front.get("description", ""),
                 triggers=triggers, body=body, path=str(path),
                 always=str(front.get("always", "")).lower() == "true")


def load(directory=SKILL_DIR) -> list:
    """Every skill in a directory, by name."""
    found = []
    for path in sorted(Path(directory).glob("*.md")):
        try:
            found.append(_parse(path.read_text(encoding="utf-8"), path))
        except OSError:
            continue
    return found


def select(task: str, skills=None, limit: int = MAX_SKILLS) -> list:
    """The skills this task actually needs, best match first."""
    pool = list(skills if skills is not None else load())
    always = [s for s in pool if s.always]
    scored = [(s.score(task), s) for s in pool if not s.always]
    hits = sorted((pair for pair in scored if pair[0] > 0),
                  key=lambda pair: (-pair[0], pair[1].name))
    return always + [skill for _, skill in hits[:max(0, limit - len(always))]]


def render(skills) -> str:
    """The block that goes into the system prompt."""
    chosen = list(skills or [])
    if not chosen:
        return ""
    head = ("Skills for this task — follow them over your own habits:")
    return head + "\n\n" + "\n\n".join(s.render() for s in chosen)


def for_task(task: str, directory=SKILL_DIR, limit: int = MAX_SKILLS) -> str:
    """Load, pick and render in one call — what the agents actually use."""
    return render(select(task, load(directory), limit))


__all__ = ["MAX_SKILLS", "Skill", "for_task", "load", "render", "select"]
