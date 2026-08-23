"""The QA agent: write the tests, run them, and repair what they catch."""
from __future__ import annotations

import logging

from .. import skills as skillkit
from ..compaction import summariser
from ..context import Conversation
from ..loop import AgentLoop
from ..modes import BUILD
from ..tools import build_registry
from . import e2e, prompts, unit
from .report import QAReport

log = logging.getLogger("forge.qa")

MAX_ROUNDS = 3
KINDS = {"unit": unit, "e2e": e2e}


def signature(report: QAReport) -> frozenset:
    """What is failing right now, so two rounds can be compared."""
    return frozenset((f.suite, f.name) for f in report.failures)


class QAAgent:
    """Authoring and repair for one project, one kind of suite at a time."""

    def __init__(self, project_dir, model, *, on_event=None, budget: int = 20_000,
                 should_stop=None, runner=None):
        self.project_dir = project_dir
        self.model = model
        self.on_event = on_event
        self.budget = budget
        self.should_stop = should_stop
        self.runner = runner            # injected in tests; None means the real shell

    def _emit(self, event: str, **fields) -> None:
        if self.on_event:
            self.on_event({"type": event, **fields})

    def _loop(self, task_text: str, goal: str) -> AgentLoop:
        """Write-enabled, with whichever skill the task text matches."""
        convo = Conversation(
            system=BUILD.system_prompt(prompts.TESTER,
                                       skillkit.for_task(task_text)),
            goal=goal, budget=self.budget)
        return AgentLoop(self.model, build_registry(self.project_dir), convo,
                         mode=BUILD, on_event=self.on_event,
                         summarise=summariser(self.model.ask),
                         should_stop=self.should_stop)

    def author(self, kind: str, brief: str):
        """Write the suite. The task text is what selects the skill."""
        task = prompts.unit_task(brief) if kind == "unit" else prompts.e2e_task(brief)
        result = self._loop(task, task).run()
        self._emit("tests_written", kind=kind, files=result.files)
        return result

    def run_suite(self, kind: str) -> QAReport:
        """Run one suite and report."""
        module = KINDS[kind]
        options = {"runner": self.runner} if self.runner else {}
        report = module.run(self.project_dir, **options)
        self._emit("suite", kind=kind, report=report.as_dict())
        return report

    def repair(self, report: QAReport):
        """One repair turn against one round of failures."""
        task = prompts.repair_task(report)
        result = self._loop(f"{report.kind} test failures", task).run()
        self._emit("repaired", kind=report.kind, files=result.files)
        return result

    def verify(self, kind: str, rounds: int = MAX_ROUNDS) -> QAReport:
        """Run, repair, run again — until green, or until it stops improving."""
        report = self.run_suite(kind)
        last = signature(report)
        for round_number in range(1, max(1, rounds) + 1):
            if report.green or not report.ran:
                break
            if report.environmental():
                # A missing browser or a dead dev server is not something a
                # rewrite of the tests can fix. Say so instead of looping.
                report.note = (f"{report.kind} could not run: "
                               f"{report.failures[0].message[:160]}")
                self._emit("qa_blocked", kind=kind, why=report.note)
                break
            if self.should_stop and self.should_stop():
                report.note = "cancelled"
                break
            self.repair(report)
            report = self.run_suite(kind)
            current = signature(report)
            self._emit("qa_round", kind=kind, round=round_number,
                       remaining=len(current))
            if current and current == last:
                # The same cases failing after a repair means the next round
                # would repeat itself. Stop and report honestly instead.
                report.note = (f"stopped after round {round_number}: the same "
                               f"{len(current)} case(s) still fail")
                break
            last = current
        return report

    def run(self, brief: str, kinds=("unit", "e2e"), rounds: int = MAX_ROUNDS) -> dict:
        """Author and verify each suite. Returns `{kind: QAReport}`."""
        reports = {}
        for kind in kinds:
            if self.should_stop and self.should_stop():
                break
            self.author(kind, brief)
            reports[kind] = self.verify(kind, rounds)
            self._emit("qa_done", kind=kind, summary=reports[kind].summary())
        return reports
