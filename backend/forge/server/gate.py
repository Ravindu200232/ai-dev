"""The human-in-the-loop approval a build waits on."""
from __future__ import annotations

import threading

APPROVAL_TIMEOUT = 900


class PlanGate:
    """Asks the studio to approve a plan, and blocks until it answers.

    Nobody answering is not a reason to hang forever, so `on_timeout` decides
    what silence means — and whichever way it falls, it is announced rather
    than assumed.
    """

    def __init__(self, emit=None, *, timeout: int = APPROVAL_TIMEOUT,
                 on_timeout: str = "approve"):
        self.emit = emit
        self.timeout = timeout
        self.on_timeout = on_timeout
        self._answered = threading.Event()
        self._verdict = None
        # An answer only means something once the plan has been put to the
        # user. Accepting one earlier would let a stale verdict decide the
        # next plan.
        self._asked = False

    def _say(self, message: dict) -> None:
        if self.emit:
            self.emit(message)

    def _answer(self, verdict) -> bool:
        """Record a verdict. False when no plan is waiting for one."""
        if not self._asked:
            return False
        self._verdict = verdict
        self._answered.set()
        return True

    def approve(self) -> bool:
        return self._answer(True)

    def revise(self, note: str) -> bool:
        return self._answer(str(note or "").strip() or "revise the plan")

    def reject(self) -> bool:
        return self._answer(False)

    def gate(self, plan):
        """Ask, wait, and return True / a revision note / False."""
        self._answered.clear()
        self._verdict = None
        self._asked = True
        self._say({"type": "plan_review", "plan": plan.as_dict()})
        answered = self._answered.wait(self.timeout)
        self._asked = False
        if not answered:
            approved = self.on_timeout == "approve"
            self._say({"type": "log", "level": "WARN",
                       "text": f"   🧭 no answer in {self.timeout}s — the plan "
                               f"was {'approved' if approved else 'rejected'} "
                               f"by the timeout rule"})
            return approved
        return self._verdict
