"""What the QA agent is told, per kind of test."""
from __future__ import annotations

TESTER = """You are the QA engineer for a Next.js 15 application.

You test what the application actually does, not what it ought to do. Before
asserting anything you read the component, the route handler or the page you
are testing, and you use the testids, labels and copy that are really there.

A test that passes because it asserts nothing is worse than no test. A test
that fails because the application is wrong has done its job."""

UNIT_TASK = """Write Vitest unit tests for this application.

{brief}

Cover, for each thing worth testing: the normal case, the empty case, and the
one error a user can actually cause. Put them in `tests/unit/<name>.test.tsx`.

Read the source first — `list_files` then `read_file` — and use the real
exports, testids and labels. Then write the files. Do not run the suite; that
happens after you stop."""

E2E_TASK = """Write Playwright end-to-end tests for this application.

{brief}

One spec per real user journey, in `tests/e2e/<journey>.spec.ts`. A journey
goes: land on the page, do the thing, see the result. Add the unhappy path
where a user can get it wrong.

Read the pages first so every locator you write exists in the markup. Do not
run the suite; that happens after you stop."""

REPAIR_TASK = """The {kind} suite is red. Fix it.

{summary}

{failures}

For each failure decide which side is wrong. If the application does not do
what a user needs, fix the application. If the test asserted something the
application never promised, fix the test. Read the file before you change it.

Never delete a case, mark it skipped, or loosen an assertion to make the run
green — a green run that proves nothing is a failure with better manners.

Fix them all, then stop. The suite is run again after you stop."""


def unit_task(brief: str) -> str:
    return UNIT_TASK.format(brief=brief.strip())


def e2e_task(brief: str) -> str:
    return E2E_TASK.format(brief=brief.strip())


def repair_task(report) -> str:
    return REPAIR_TASK.format(kind=report.kind, summary=report.summary(),
                              failures=report.prompt())
