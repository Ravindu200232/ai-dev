"""The pipeline: scaffold, plan, approve, build, then prove it works."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .builder import BuilderAgent, auto_approve
from .events import relay
from .qa import QAAgent, QAReport

log = logging.getLogger("forge.service")

STAGES = ("scaffold", "install", "plan", "build", "unit", "e2e")

INSTALL = "npm install --no-audit --no-fund --loglevel=error"


@dataclass
class PipelineResult:
    """What a full run produced, and whether it is safe to call it done."""

    brief: str = ""
    plan: dict = field(default_factory=dict)
    built: bool = False
    files: list = field(default_factory=list)
    reports: dict = field(default_factory=dict)     # kind → QAReport
    requested: tuple = ()
    stopped_at: str = ""

    @property
    def green(self) -> bool:
        """Built, and every suite that was asked for ran and passed.

        A suite that could not run is not a suite that passed — without the
        second check a blocked end-to-end stage would report the whole run
        green while proving nothing.
        """
        if not self.built or not self.reports:
            return False
        if any(kind not in self.reports for kind in self.requested):
            return False
        return all(r.green for r in self.reports.values())

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

    def __init__(self, project_dir, model, *, emit=None, on_event=None,
                 qa_model=None, budget: int = 24_000, should_stop=None,
                 qa_runner=None):
        self.project_dir = project_dir
        self.emit = emit
        # `emit` is the plain websocket sink; `on_event` lets a host that has
        # its own event vocabulary translate the stream itself.
        self.on_event = on_event or (relay(emit) if emit else None)
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

    def _browser_ready(self) -> bool:
        """Whether Playwright has a browser it can actually launch."""
        from .qa import e2e as e2e_kit
        return e2e_kit.browser_ready(self.project_dir, runner=self.qa.runner)

    def install(self) -> bool:
        """Put node_modules there. Skipped when it already is."""
        from pathlib import Path

        from .tools.shell import run_command
        if (Path(self.project_dir) / "node_modules").is_dir():
            return True
        output = (self.qa.runner or run_command)(self.project_dir, INSTALL, 900)
        ok = "[exit 0]" in output
        if self.on_event:
            self.on_event({"type": "assistant" if ok else "tool_result",
                           "text": "dependencies installed", "ok": ok,
                           "name": "npm install", "preview": output[-300:]})
        return ok

    def run(self, brief: str, *, name: str = "app", title: str = "New App",
            approve=auto_approve, kinds=("unit", "e2e"),
            rounds: int = 3) -> PipelineResult:
        """Scaffold → plan → approval gate → build → unit → e2e."""
        result = PipelineResult(brief=brief, requested=tuple(kinds))

        self._stage("scaffold")
        self.builder.scaffold(name=name, title=title)
        self._stage("scaffold", "done")

        # Nothing can be built or tested until the toolchain is on disk, and
        # nothing else in the pipeline puts it there.
        self._stage("install")
        installed = self.install()
        self._stage("install", "done" if installed else "error")

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
            if kind == "e2e" and not self._browser_ready():
                blocked = QAReport(kind=kind, ran=False,
                                   note="no browser is installed, so the "
                                        "end-to-end suite could not run")
                result.reports[kind] = blocked
                if self.on_event:
                    self.on_event({"type": "suite", "report": blocked.as_dict()})
                self._stage(kind, "error")
                continue
            self.qa.author(kind, brief)
            result.reports[kind] = self.qa.verify(kind, rounds)
            self._stage(kind, "done" if result.reports[kind].green else "error")

        if self.on_event:
            self.on_event({"type": "qa_done", "summary": result.summary()})
        return result
