"""Agentic E2E debugger: Claude/Codex-style inspect → reason → act loop."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .e2e_common import singular
from .debugger_rules import (
    diagnose_api_http_error,
    diagnose_broken_images,
    diagnose_native_form_submit,
    diagnose_checkpoint_auth,
    diagnose_navigation_loop,
    diagnose_page_health,
    diagnose_post_mutation_proof,
    diagnose_request_contract,
)

log = logging.getLogger("qa.debugger")

MAX_TOOL_TURNS = 5
TOOL_RESULT_CHARS = 7000
DEBUGGER_OUTPUT_CHARS = 5000


class _StopDebuggerStream(Exception):
    """Internal signal: enough protocol output has been received."""


VERDICTS = {
    "TEST_FIX", "APP_FIX", "AUTH_FIX", "DATA_PREREQUISITE",
    "BLOCKED_UPSTREAM", "ROUTE_FIX", "RETRY_TRANSIENT", "UNKNOWN",
}

SYSTEM = r"""
You are AgentForge's autonomous E2E debugging brain. Behave like Claude Code or
Codex: inspect evidence, form a falsifiable hypothesis, use a tool when needed,
then make the smallest justified decision. Never guess a code edit from UI
wording alone.

The user goal is to make THIS exact journey pass without weakening the app's
requirement and without breaking already-working behavior.

You have read-only tools. Request at most one tool per turn:
TOOL :: READ_FILE :: repo/path.js
TOOL :: SEARCH_CODE :: regex or literal text
TOOL :: ROUTE_SOURCE :: /route/or/api/path
TOOL :: ANALYZER :: repo/path.js
TOOL :: PLAN :: capability|workflow|contract text
TOOL :: DOM :: current
TOOL :: NETWORK :: current
TOOL :: AUTH :: current
TOOL :: API_BODIES :: current
TOOL :: JOURNEY_STATE :: current
TOOL :: STACK_SOURCE :: current

When enough evidence exists, decide. A decision MUST be exactly these lines:
VERDICT :: TEST_FIX|APP_FIX|AUTH_FIX|DATA_PREREQUISITE|BLOCKED_UPSTREAM|ROUTE_FIX|RETRY_TRANSIENT|UNKNOWN
ROOT :: one concrete root cause tied to evidence
FILES :: comma-separated repo paths or NONE
TEST_STEP :: exact failing scenario step to replace/drop, or NONE
TEST_PATCH :: one replacement scenario line, DROP, or NONE
HYPOTHESIS :: one short falsifiable statement
NEXT :: one sentence describing the smallest next action
CONFIDENCE :: high|medium|low

Decision rules:
- Exact runtime source locations outrank selector guesses. If PAGEERROR,
  ReferenceError, TypeError, or a Next runtime exception names a real project
  file:line:column, inspect STACK_SOURCE first and treat that production frame
  as APP evidence unless a stronger API/auth boundary proves otherwise.
- TEST_FIX: app behavior is correct AND the DOM/source shows a real equivalent
  control/value/route the scenario can target; patch only the exact bad step.
  A missing control that the accepted workflow/capability REQUIRES is NOT a
  TEST_FIX just because the browser reported a selector miss.
- APP_FIX: production behavior/code is wrong. This includes a required business
  action whose control is absent/inaccessible in the rendered DOM, and an
  icon-only required action that has no accessible name.
- AUTH_FIX: session/cookie/auth boundary is the root cause. Sign-in HTTP 200 is
  not enough; use get-session and downstream 401/403 evidence.
- DATA_PREREQUISITE: the journey expects a record/state that legitimately does
  not exist yet. Do not add fake markup/data merely to satisfy a selector.
- BLOCKED_UPSTREAM: this journey depends on an earlier business outcome that is
  currently failed/absent. Name that dependency in ROOT/NEXT.
- ROUTE_FIX: a required route/link is genuinely wrong or missing.
- RETRY_TRANSIENT: evidence is only a compile/HMR/ERR_ABORTED/stream transient.
- UNKNOWN: evidence is insufficient. Prefer another tool before UNKNOWN.

For TEST_FIX, TEST_PATCH must be one COMPLETE valid AgentForge scenario line or DROP.
Examples: `SELECT :: field=category :: Footwear`, `FILL :: field=phone :: +15550000000`,
`CLICK :: role=button name=/save/i`, `EXPECT_URL :: /confirm/i`. Never output
locator prose such as `field 'category'` or `combobox /category/i` by itself.
Never regenerate the whole test. Do not repeat a hypothesis listed in the
notebook as rejected. Do not request the same tool+argument twice.
"""


def _norm(text: str) -> str:
    text = re.sub(r"\b[0-9a-f]{24}\b", "<id>", str(text or ""), flags=re.I)
    text = re.sub(r"localhost:\d+|127\.0\.0\.1:\d+", "localhost:<port>", text)
    return re.sub(r"\s+", " ", text).strip().lower()[:1200]


@dataclass
class DebugNotebook:
    goal: str = ""
    rejected_hypotheses: list[str] = field(default_factory=list)
    # What a rejected decision DID, apart from how it was worded.
    rejected_shapes: list[str] = field(default_factory=list)
    attempted_hypotheses: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    failure_signatures: list[str] = field(default_factory=list)
    last_good_step: int = -1

    def context(self) -> str:
        rows = [f"goal={self.goal or '?'}", f"last_good_step={self.last_good_step}"]
        if self.rejected_hypotheses:
            rows.append("rejected_hypotheses=" + " | ".join(self.rejected_hypotheses[-6:]))
        if self.attempted_hypotheses:
            rows.append("attempted_hypotheses=" + " | ".join(self.attempted_hypotheses[-6:]))
        if self.failure_signatures:
            rows.append("recent_failures=" + " | ".join(self.failure_signatures[-5:]))
        if self.tool_calls:
            rows.append("tools_already_used=" + " | ".join(self.tool_calls[-10:]))
        return "\n".join(rows)

    def remember_failure(self, failures) -> None:
        if not failures:
            return
        f = failures[0]
        sig = _norm(" | ".join([
            str(getattr(f, "kind", "") or ""),
            str(getattr(f, "name", "") or ""),
            str(getattr(f, "message", "") or ""),
            str(getattr(f, "target", "") or ""),
        ]))
        if sig and (not self.failure_signatures or self.failure_signatures[-1] != sig):
            self.failure_signatures.append(sig)

    def record_decision(self, decision: dict) -> None:
        hyp = _norm(decision.get("hypothesis") or decision.get("root") or "")
        if hyp and hyp not in self.attempted_hypotheses:
            self.attempted_hypotheses.append(hyp)
        self.decisions.append(dict(decision))

    @staticmethod
    def decision_shape(decision: dict) -> str:
        """What a decision actually decided, without its wording."""
        files = [str(f or "").strip() for f in (decision.get("files") or []) if f]
        return "|".join([
            str(decision.get("verdict") or "").strip().upper(),
            (sorted(files)[0] if files else ""),
            _norm(str(decision.get("test_step") or ""))[:120],
        ])

    def record_outcome(self, decision: dict, *, progressed: bool) -> None:
        hyp = _norm(decision.get("hypothesis") or decision.get("root") or "")
        if hyp and not progressed and hyp not in self.rejected_hypotheses:
            self.rejected_hypotheses.append(hyp)
        shape = self.decision_shape(decision)
        if shape.strip("|") and not progressed and shape not in self.rejected_shapes:
            self.rejected_shapes.append(shape)

__all__ = [name for name in globals() if not name.startswith("__")]
