"""The pipeline: scaffold, plan, approve, build, then prove it works."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .builder import BuilderAgent, auto_approve
from .events import relay
from .qa import QAAgent

log = logging.getLogger("forge.service")

STAGES = ("scaffold", "plan", "build", "unit", "e2e")


@dataclass
class PipelineResult:
    """What a full run produced, and whether it is safe to call it done."""

    brief: str = ""
    plan: dict = field(default_factory=dict)
    built: bool = False
    files: list = field(default_factory=list)
    reports: dict = field(default_factory=dict)     # kind → QAReport
    stopped_at: str = ""

    @property
    def green(self) -> bool:
        return self.built and all(r.green for r in self.reports.values())

    def summary(self) -> str:
        if self.stopped_at:
            return f"stopped at {self.stopped_at}"
        lines = [f"built {len(self.files)} file(s)"]
        lines += [report.summary() for report in self.reports.values()]
        return " · ".join(lines)

    def as_dict(self) -> dict:
        return {"brief": self.brief, "plan": self.plan, "built": self.built,
                "files": self.files, "green": self.green,
                "stopped_at": self.stopped_at,
                "reports": {k: r.as_dict() for k, r in self.reports.items()},
                "summary": self.summary()}


class Pipeline:
    """One brief, from nothing to a tested application."""

    def __init__(self, project_dir, model, *, emit=None, qa_model=None,
                 budget: int = 24_000, should_stop=None, qa_runner=None):
        self.project_dir = project_dir
        self.emit = emit
        self.on_event = relay(emit) if emit else None
        self.builder = BuilderAgent(project_dir, model, on_event=self.on_event,
                                    budget=budget, should_stop=should_stop)
        self.qa = QAAgent(project_dir, qa_model or model, on_event=self.on_event,
                          budget=budget, should_stop=should_stop,
                          runner=qa_runner)
        self.should_stop = should_stop

    def _stage(self, name: str, status: str = "run") -> None:
        if self.on_event:
            self.on_event({"type": "stage", "step": name, "status": status})

    def _cancelled(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    def run(self, brief: str, *, name: str = "app", title: str = "New App",
            approve=auto_approve, kinds=("unit", "e2e"),
            rounds: int = 3) -> PipelineResult:
        """Scaffold → plan → approval gate → build → unit → e2e."""
        result = PipelineResult(brief=brief)

        self._stage("scaffold")
        self.builder.scaffold(name=name, title=title)
        self._stage("scaffold", "done")

        self._stage("plan")
        plan = self.builder.settle(brief, approve, rounds)
        result.plan = plan.as_dict()
        self._stage("plan", "done" if plan.approved else "error")

        if not plan.approved:
            result.stopped_at = "plan"
            return result

        self._stage("build")
        built = self.builder.build(plan, brief)
        result.built = True
        result.files = list(getattr(built, "files", []) or [])
        self._stage("build", "done" if built.ok else "error")

        for kind in kinds:
            if self._cancelled():
                result.stopped_at = kind
                break
            self._stage(kind)
            self.qa.author(kind, brief)
            result.reports[kind] = self.qa.verify(kind, rounds)
            self._stage(kind, "done" if result.reports[kind].green else "error")

        if self.on_event:
            self.on_event({"type": "qa_done", "summary": result.summary()})
        return result
