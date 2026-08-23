"""Wiring `forge` into the running server: models, events and the plan gate."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("server.forge")

APPROVAL_TIMEOUT = 900


class PlanGate:
    """The human-in-the-loop approval, driven from the websocket.

    The pipeline blocks on `gate` until the UI sends approve, revise or
    reject. Nobody answering is not a reason to hang forever, so
    `on_timeout` decides what silence means — and whichever way it falls, it
    is announced rather than assumed.
    """

    def __init__(self, emit=None, *, timeout: int = APPROVAL_TIMEOUT,
                 on_timeout: str = "approve"):
        self.emit = emit
        self.timeout = timeout
        self.on_timeout = on_timeout
        self._answered = threading.Event()
        self._verdict = None

    def _say(self, message: dict) -> None:
        if self.emit:
            self.emit(message)

    def approve(self) -> None:
        self._verdict = True
        self._answered.set()

    def revise(self, note: str) -> None:
        self._verdict = str(note or "").strip() or "revise the plan"
        self._answered.set()

    def reject(self) -> None:
        self._verdict = False
        self._answered.set()

    def gate(self, plan):
        """Ask, wait, and return True / a revision note / False."""
        self._answered.clear()
        self._verdict = None
        self._say({"type": "plan_review", "plan": plan.as_dict()})
        if not self._answered.wait(self.timeout):
            approved = self.on_timeout == "approve"
            self._say({"type": "log", "level": "WARN",
                       "text": f"   🧭 no answer in {self.timeout}s — the plan "
                               f"was {'approved' if approved else 'rejected'} "
                               f"by the timeout rule"})
            return approved
        return self._verdict


def build_pipeline(project_dir, *, emit=None, model_name: str = "",
                   qa_model_name: str = "", budget: int = 24_000,
                   should_stop=None):
    """A pipeline pointed at one project, talking to one Ollama model."""
    from forge import Pipeline
    from forge.llm import Model

    model = Model(model_name) if model_name else None
    if model is None:
        raise ValueError("build_pipeline needs a model name")
    qa_model = Model(qa_model_name) if qa_model_name else model
    return Pipeline(project_dir, model, emit=emit, qa_model=qa_model,
                    budget=budget, should_stop=should_stop)


def run_build(project_dir, brief: str, *, emit=None, model_name: str = "",
              qa_model_name: str = "", gate=None, name: str = "app",
              title: str = "New App", kinds=("unit", "e2e"),
              should_stop=None) -> dict:
    """Scaffold, plan, approve, build and verify — the whole thing, once."""
    pipeline = build_pipeline(project_dir, emit=emit, model_name=model_name,
                              qa_model_name=qa_model_name,
                              should_stop=should_stop)
    approver = (gate or PlanGate(emit)).gate
    result = pipeline.run(brief, name=name, title=title, approve=approver,
                          kinds=kinds)
    if emit:
        emit({"type": "build_done", "result": result.as_dict()})
    return result.as_dict()
