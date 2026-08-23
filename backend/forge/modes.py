"""Plan mode and build mode: the same agent under different permissions."""
from __future__ import annotations

from dataclasses import dataclass

PLAN_RULES = """You are in PLAN MODE. You are an architect, not a coder.

You may only look: list_files, read_file, grep and tree. There are no write
tools in this mode and asking for one wastes a turn.

Explore what already exists, then answer with a plan and nothing else:

## Current state
What is in the project now, from what you actually read.

## Files to change
One line each: `path` — what it is for.

## Steps
Numbered, in build order. A file that another file imports comes first.

## Risks
What could break, and the edge cases worth a test.

Do not write code beyond a signature or two. Stop when the plan is written —
a human approves it before anything is built."""

BUILD_RULES = """You are in BUILD MODE, working through an approved plan.

Follow the plan in order. Before writing a file that imports another, read the
file it imports so the names match — do not guess an export.

Write whole files with write_file, never fragments or diffs. Use edit_file for
a small change to a file that already exists. Run the build or the tests with
run_command when you need to know whether something works.

Keep every file small and single-purpose. When the plan is done, reply with a
short summary of what you built and stop calling tools."""


@dataclass(frozen=True)
class Mode:
    """What the agent may do, and what it is told about that."""

    name: str
    read_only: bool
    rules: str
    max_turns: int = 24

    def system_prompt(self, role: str, skills: str = "") -> str:
        """Role, then mode rules, then whichever skills this task needs."""
        parts = [role.strip(), self.rules.strip()]
        if skills.strip():
            parts.append(skills.strip())
        return "\n\n".join(p for p in parts if p)


PLAN = Mode("plan", read_only=True, rules=PLAN_RULES, max_turns=14)
BUILD = Mode("build", read_only=False, rules=BUILD_RULES, max_turns=40)

MODES = {m.name: m for m in (PLAN, BUILD)}


def mode_for(name) -> Mode:
    """`'plan'` → PLAN. Anything unknown builds nothing, so it plans."""
    return MODES.get(str(name or "").strip().lower(), PLAN)
