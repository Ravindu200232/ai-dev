"""Running Playwright and reading its report."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..tools.shell import run_command
from .report import Failure, QAReport

log = logging.getLogger("forge.qa.e2e")

REPORT = "test-results/e2e.json"
COMMAND = "npx playwright test --reporter=json"
INSTALL = "npx playwright install --with-deps chromium"
TIMEOUT = 1_200


def _specs(suite: dict, prefix: str = ""):
    """Walk Playwright's nested suites and yield `(file, title, spec)`."""
    name = str(suite.get("file") or suite.get("title") or prefix or "e2e")
    for spec in suite.get("specs") or []:
        yield name, str(spec.get("title") or "?"), spec
    for child in suite.get("suites") or []:
        yield from _specs(child, name)


def _error_text(spec: dict) -> str:
    """Every message Playwright attached to a failing spec, joined."""
    parts = []
    for test in spec.get("tests") or []:
        for result in test.get("results") or []:
            error = result.get("error") or {}
            parts.append(str(error.get("message") or ""))
            for extra in result.get("errors") or []:
                parts.append(str((extra or {}).get("message") or ""))
            if result.get("status") in ("timedOut", "interrupted"):
                parts.append(f"the run {result['status']} after "
                             f"{result.get('duration', 0)}ms")
    return "\n".join(p for p in parts if p.strip()) or "the spec failed"


def parse(data: dict) -> QAReport:
    """Playwright's JSON → a report in the same shape as the unit one."""
    report = QAReport(kind="e2e", ran=True)
    for suite in data.get("suites") or []:
        for file_name, title, spec in _specs(suite):
            if spec.get("ok"):
                report.passed += 1
            else:
                report.failures.append(
                    Failure.make(Path(file_name).name, title, _error_text(spec)))
    return report


def read_report(project_dir, path: str = REPORT) -> dict:
    """The JSON Playwright just wrote, or `{}`."""
    try:
        return json.loads((Path(project_dir) / path).read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}


def report_from_output(output: str) -> dict:
    """The report Playwright printed instead of writing.

    `--reporter=json` on the command line overrides whatever `outputFile` the
    project's config asked for, so the run that was told to write a file
    prints to stdout instead. Read it from there rather than calling a run
    that happened a run that did not.
    """
    text = str(output or "")
    start = text.find("{")
    while start >= 0:
        try:
            data = json.loads(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        return data if isinstance(data, dict) and "suites" in data else {}
    return {}


def browser_ready(project_dir, runner=None) -> bool:
    """Install Chromium once. A failure here is an environment problem."""
    output = (runner or run_command)(project_dir, INSTALL, 900)
    return "[exit 0]" in output


def run(project_dir, *, command: str = COMMAND, timeout: int = TIMEOUT,
        runner=None) -> QAReport:
    """Run the e2e suite and read what came back."""
    output = (runner or run_command)(project_dir, command, timeout)
    data = read_report(project_dir) or report_from_output(output)
    if not data:
        report = QAReport(kind="e2e", ran=False,
                          note="playwright wrote no report — see the output")
        report.failures.append(Failure.make("playwright", "(the run itself)", output))
        return report
    report = parse(data)
    if not report.passed and not report.failures:
        report.note = "no e2e specs were collected"
    return report
