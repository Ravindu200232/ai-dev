"""Running Vitest and reading its report."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..tools.shell import run_command
from .report import Failure, QAReport

log = logging.getLogger("forge.qa.unit")

REPORT = ".vitest-report.json"
COMMAND = f"npx vitest run --reporter=json --outputFile={REPORT}"
TIMEOUT = 900


def parse(data: dict) -> QAReport:
    """Vitest's JSON (the Jest shape) → a report."""
    report = QAReport(kind="unit", ran=True)
    for suite in data.get("testResults") or []:
        name = Path(str(suite.get("name") or "")).name or "unknown"
        cases = suite.get("assertionResults") or []
        if suite.get("status") == "failed" and not cases:
            report.failures.append(Failure.make(
                name, "(the file did not load)",
                suite.get("message") or "the test file could not be loaded"))
            continue
        for case in cases:
            title = str(case.get("fullName") or case.get("title") or "?")[:200]
            status = case.get("status")
            if status == "passed":
                report.passed += 1
            elif status not in ("skipped", "pending", "todo"):
                report.failures.append(Failure.make(
                    name, title, "\n".join(case.get("failureMessages") or [])))
    return report


def read_report(project_dir) -> dict:
    """The report file Vitest just wrote, or `{}`."""
    path = Path(project_dir) / REPORT
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def run(project_dir, *, command: str = COMMAND, timeout: int = TIMEOUT,
        runner=run_command) -> QAReport:
    """Run the unit suite and read what came back."""
    output = runner(project_dir, command, timeout)
    data = read_report(project_dir)
    if not data:
        report = QAReport(kind="unit", ran=False,
                          note="vitest wrote no report — see the output")
        report.failures.append(Failure.make("vitest", "(the run itself)", output))
        return report
    report = parse(data)
    if not report.failures and not report.passed:
        report.note = "the suite collected no test cases"
    return report
