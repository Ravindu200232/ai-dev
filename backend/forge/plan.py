"""The plan a human approves before anything is written."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECTION_RE = re.compile(r"^#{1,4}\s*(.+?)\s*$", re.M)
_FILE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])?\s*`?([\w./@\-\[\]]+\.\w{1,5})`?"
                      r"\s*[-—:]\s*(.+?)\s*$", re.M)
_STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.{4,300})$", re.M)


def sections(text: str) -> dict:
    """`{heading lowercased: body}` for a markdown document."""
    marks = list(_SECTION_RE.finditer(text or ""))
    found = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        found[mark.group(1).strip().lower()] = text[mark.end():end].strip()
    return found


def _pick(found: dict, *words) -> str:
    """The first section whose heading mentions one of these words."""
    for heading, body in found.items():
        if any(word in heading for word in words):
            return body
    return ""


@dataclass
class PlanStep:
    """One numbered move in build order."""

    order: int
    text: str


@dataclass
class Plan:
    """What the architect proposed, and whether a human said yes."""

    brief: str = ""
    raw: str = ""
    state: str = ""
    files: list = field(default_factory=list)      # [(path, purpose)]
    steps: list = field(default_factory=list)      # [PlanStep]
    risks: list = field(default_factory=list)
    approved: bool = False
    feedback: str = ""

    @property
    def paths(self) -> list:
        return [path for path, _ in self.files]

    def approve(self) -> "Plan":
        """The human-in-the-loop gate. Build mode refuses to start without it."""
        self.approved = True
        self.feedback = ""
        return self

    def revise(self, feedback: str) -> "Plan":
        """Send it back with a note; the next plan turn gets to read this."""
        self.approved = False
        self.feedback = str(feedback or "").strip()
        return self

    def is_empty(self) -> bool:
        return not self.files and not self.steps

    def render(self) -> str:
        """The plan as the user sees it in the approval prompt."""
        lines = [f"Plan for: {self.brief}".strip()]
        if self.state:
            lines += ["", "Current state:", self.state]
        if self.files:
            lines += ["", "Files:"] + [f"  {p} — {why}" for p, why in self.files]
        if self.steps:
            lines += ["", "Steps:"] + [f"  {s.order}. {s.text}" for s in self.steps]
        if self.risks:
            lines += ["", "Risks:"] + [f"  - {r}" for r in self.risks]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        """The shape the UI receives over the websocket."""
        return {"brief": self.brief, "state": self.state,
                "files": [{"path": p, "purpose": why} for p, why in self.files],
                "steps": [s.text for s in self.steps], "risks": self.risks,
                "approved": self.approved}


def parse(text: str, brief: str = "") -> Plan:
    """Read the architect's markdown into something the builder can follow."""
    body = str(text or "")
    found = sections(body)
    files_body = _pick(found, "file", "change", "create")
    steps_body = _pick(found, "step", "order", "implementation")
    risk_body = _pick(found, "risk", "trade", "edge")

    files = [(path, purpose.strip()) for path, purpose
             in _FILE_RE.findall(files_body or body)]
    steps = [PlanStep(index, text.strip())
             for index, text in enumerate(_STEP_RE.findall(steps_body), 1)]
    risks = [line.strip() for line in _STEP_RE.findall(risk_body)]

    return Plan(brief=brief, raw=body, state=_pick(found, "current", "state"),
                files=list(dict.fromkeys(files)), steps=steps, risks=risks)
