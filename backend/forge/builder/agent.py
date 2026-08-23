"""The builder: plan it, get it approved, then write it."""
from __future__ import annotations

import logging

from .. import skills as skillkit
from ..compaction import summariser
from ..context import Conversation
from ..loop import AgentLoop
from ..modes import BUILD, PLAN
from ..plan import Plan, parse
from ..tools import build_registry
from ..tools.read import list_files
from . import prompts
from .scaffold import scaffold

log = logging.getLogger("forge.builder")

MAX_PLAN_ROUNDS = 3


def auto_approve(plan: Plan) -> bool:
    """The gate used when no human is watching. Pipelines pass their own."""
    return not plan.is_empty()


class BuilderAgent:
    """One project, one model, two modes."""

    def __init__(self, project_dir, model, *, on_event=None, budget: int = 24_000,
                 should_stop=None):
        self.project_dir = project_dir
        self.model = model
        self.on_event = on_event
        self.budget = budget
        self.should_stop = should_stop

    def _emit(self, event: str, **fields) -> None:
        if self.on_event:
            self.on_event({"type": event, **fields})

    def _loop(self, mode, task_text: str, goal: str) -> AgentLoop:
        """A loop wired for one mode, with only the skills this task needs."""
        registry = build_registry(self.project_dir, read_only=mode.read_only)
        convo = Conversation(
            system=mode.system_prompt(
                prompts.ARCHITECT if mode.read_only else prompts.BUILDER,
                skillkit.for_task(task_text)),
            goal=goal, budget=self.budget)
        return AgentLoop(self.model, registry, convo, mode=mode,
                         on_event=self.on_event,
                         summarise=summariser(self.model.ask),
                         should_stop=self.should_stop)

    def scaffold(self, name: str = "app", title: str = "New App") -> list:
        """Put the Next.js skeleton down so the agent has something to read."""
        written = scaffold(self.project_dir, name=name, title=title)
        self._emit("scaffold", files=written)
        return written

    def state(self) -> str:
        """What the project looks like right now, for the planning prompt."""
        return list_files(self.project_dir, "", 2)

    def plan(self, brief: str, feedback: str = "") -> Plan:
        """Read-only exploration ending in a plan a human can read."""
        task = prompts.plan_task(brief, feedback, self.state())
        loop = self._loop(PLAN, brief, task)
        result = loop.run()
        plan = parse(result.text, brief=brief)
        plan.feedback = feedback
        self._emit("plan", plan=plan.as_dict(), turns=result.turns)
        return plan

    def build(self, plan: Plan, brief: str = "") -> "object":
        """Write the approved plan. Refuses to start on an unapproved one."""
        if not plan.approved:
            raise PermissionError("this plan has not been approved")
        task = prompts.build_task(plan, brief)
        loop = self._loop(BUILD, brief or plan.brief, task)
        result = loop.run()
        self._emit("built", files=result.files, stopped=result.stopped)
        return result

    def settle(self, brief: str, approve=auto_approve,
               rounds: int = MAX_PLAN_ROUNDS) -> Plan:
        """Plan, put it to the gate, revise while asked. Nothing is written.

        `approve` returns True to build, a string to send the plan back with
        that note, or False to stop. The plan comes back either approved or
        not, and the caller decides what that means.
        """
        plan, feedback = Plan(brief=brief), ""
        for attempt in range(1, max(1, rounds) + 1):
            plan = self.plan(brief, feedback)
            verdict = approve(plan)
            if verdict is True:
                return plan.approve()
            if isinstance(verdict, str) and verdict.strip():
                feedback = verdict
                plan.revise(feedback)
                self._emit("plan_revision", round=attempt, note=feedback)
                continue
            self._emit("plan_rejected", round=attempt)
            return plan
        return plan

    def run(self, brief: str, approve=auto_approve, rounds: int = MAX_PLAN_ROUNDS):
        """Settle on a plan, then build it if it was approved."""
        plan = self.settle(brief, approve, rounds)
        if not plan.approved:
            return {"plan": plan, "result": None, "built": False}
        return {"plan": plan, "result": self.build(plan, brief), "built": True}
