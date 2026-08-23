"""Plan mode, the build loop, and QA that repairs what it catches."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from forge import Pipeline
from forge.builder import BuilderAgent
from forge.builder.scaffold import scaffold
from forge.context import Conversation
from forge.events import to_ws
from forge.llm import ScriptedModel, normalise, tool_call
from forge.loop import AgentLoop
from forge.modes import BUILD, PLAN
from forge.plan import parse
from forge.qa import QAAgent, e2e, unit
from forge.qa.report import classify
from forge.tools import build_registry
from forge.server.gate import PlanGate

PLAN_MD = """## Current state
A fresh scaffold with app/page.tsx only.

## Files to change
- `lib/items.ts` — the item store
- `components/ItemList.tsx` — renders items with an empty state

## Steps
1. Write lib/items.ts because the component imports it
2. Write components/ItemList.tsx

## Risks
- the empty state needs a testid or no test can find it
"""


def _conversation(system="builder", goal="build it"):
    return Conversation(system=system, goal=goal, budget=20_000)


def _unit_report(root, failure=None, passed=2):
    cases = [{"fullName": f"case {i}", "status": "passed"} for i in range(passed)]
    if failure:
        cases.append({"fullName": "empty state", "status": "failed",
                      "failureMessages": [failure]})
    Path(root, unit.REPORT).write_text(json.dumps({"testResults": [
        {"name": "tests/unit/list.test.tsx",
         "status": "failed" if failure else "passed",
         "assertionResults": cases}]}))


def _e2e_report(root, ok=True):
    path = Path(root, e2e.REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = {"title": "adds an item", "ok": ok, "tests": [] if ok else [
        {"results": [{"status": "failed", "error": {
            "message": 'Timeout 5000ms exceeded waiting for locator("[data-testid=row]")'}}]}]}
    path.write_text(json.dumps({"suites": [
        {"file": "tests/e2e/items.spec.ts", "specs": [spec]}]}))


# --- the loop ---------------------------------------------------------------

def test_the_loop_runs_what_the_model_asks_for_and_stops_when_it_answers(tmp_path):
    model = ScriptedModel([
        tool_call("list_files", path=""),
        tool_call("write_file", path="app/page.tsx", content="export default () => null;"),
        "Wrote the page.",
    ])
    result = AgentLoop(model, build_registry(tmp_path), _conversation()).run()
    assert result.ok and result.text == "Wrote the page."
    assert result.files == ["app/page.tsx"]
    assert (tmp_path / "app" / "page.tsx").is_file()


def test_the_loop_stops_itself_rather_than_looping_forever(tmp_path):
    model = ScriptedModel([tool_call("list_files", path="")] * 30)
    result = AgentLoop(model, build_registry(tmp_path), _conversation(),
                       max_turns=5).run()
    assert result.stopped == "max_turns" and result.turns == 5


def test_asking_the_same_thing_over_and_over_is_answered_with_a_nudge(tmp_path):
    model = ScriptedModel([tool_call("list_files", path="app")] * 8)
    events = []
    AgentLoop(model, build_registry(tmp_path), _conversation(), max_turns=8,
              on_event=events.append, repeat_limit=2).run()
    nudges = [e for e in events if e["type"] == "tool_repeat"]
    assert nudges, "a repeated identical call should be refused, not re-run"


def test_a_cancelled_run_stops_between_turns(tmp_path):
    model = ScriptedModel([tool_call("list_files", path="")] * 5)
    result = AgentLoop(model, build_registry(tmp_path), _conversation(),
                       should_stop=lambda: True).run()
    assert result.stopped == "cancelled" and result.turns == 0


def test_the_loop_compacts_instead_of_overflowing(tmp_path):
    convo = Conversation(system="builder", goal="build it", budget=300)
    model = ScriptedModel(
        [tool_call("read_file", path=f"f{i}.txt") for i in range(6)] + ["done"])
    for index in range(6):
        (tmp_path / f"f{index}.txt").write_text("noise " * 400)
    result = AgentLoop(model, build_registry(tmp_path), convo, max_turns=8).run()
    assert result.compactions, "a run this long has to compact"
    assert convo.tokens() <= convo.budget


def test_a_model_reply_is_normalised_whatever_shape_it_arrives_in():
    reply = normalise({"message": {"content": "", "tool_calls": [
        {"function": {"name": "grep", "arguments": '{"pattern": "db"}'}}]}})
    assert reply["tool_calls"][0]["function"]["arguments"] == {"pattern": "db"}
    assert normalise({"message": {"content": "hello"}})["tool_calls"] == []


# --- plan mode --------------------------------------------------------------

def test_a_plan_is_read_out_of_the_architects_markdown():
    plan = parse(PLAN_MD, brief="an item tracker")
    assert plan.paths == ["lib/items.ts", "components/ItemList.tsx"]
    assert len(plan.steps) == 2 and plan.steps[0].order == 1
    assert plan.risks and "Current state" in plan.render()
    assert not plan.approved


def test_planning_writes_nothing_at_all(tmp_path):
    scaffold(tmp_path)
    model = ScriptedModel([
        tool_call("list_files", path=""),
        tool_call("write_file", path="sneaky.ts", content="x"),
        PLAN_MD,
    ])
    plan = BuilderAgent(tmp_path, model).plan("an item tracker")
    assert plan.paths and not (tmp_path / "sneaky.ts").exists()


def test_plan_mode_is_told_it_has_no_write_tools(tmp_path):
    model = ScriptedModel([PLAN_MD])
    BuilderAgent(tmp_path, model).plan("an item tracker")
    offered = model.seen[0]["tools"]
    assert "write_file" not in offered and "list_files" in offered
    assert "PLAN MODE" in model.seen[0]["messages"][0]["content"]


def test_building_an_unapproved_plan_is_refused(tmp_path):
    plan = parse(PLAN_MD, brief="an item tracker")
    with pytest.raises(PermissionError):
        BuilderAgent(tmp_path, ScriptedModel(["never reached"])).build(plan)


def test_a_rejected_plan_leaves_the_project_untouched(tmp_path):
    model = ScriptedModel([PLAN_MD, PLAN_MD, PLAN_MD])
    outcome = BuilderAgent(tmp_path, model).run("a tracker", approve=lambda p: False)
    assert outcome["built"] is False and not outcome["plan"].approved
    assert not (tmp_path / "lib").exists()


def test_a_revision_note_reaches_the_next_planning_turn(tmp_path):
    model = ScriptedModel([PLAN_MD, PLAN_MD, tool_call("write_file", path="a.ts",
                                                       content="x"), "built"])
    verdicts = ["split lib/items.ts into two files", True]
    outcome = BuilderAgent(tmp_path, model).run(
        "a tracker", approve=lambda plan: verdicts.pop(0))
    assert outcome["built"] is True
    second_plan_prompt = model.seen[1]["messages"][-1]["content"]
    assert "split lib/items.ts into two files" in second_plan_prompt


# --- the builder ------------------------------------------------------------

def test_the_scaffold_is_a_project_that_can_build_test_and_run(tmp_path):
    written = scaffold(tmp_path, name="shop", title="Shop")
    package = json.loads((tmp_path / "package.json").read_text())
    assert package["scripts"]["build"] == "next build"
    assert package["scripts"]["test:e2e"] == "playwright test"
    assert {"vitest", "@playwright/test"} <= set(package["devDependencies"])
    assert "app/layout.tsx" in written and "playwright.config.ts" in written
    assert (tmp_path / "tests" / "e2e").is_dir()
    # Running it twice must not overwrite what the agent has written since.
    (tmp_path / "app" / "page.tsx").write_text("// mine\n")
    scaffold(tmp_path)
    assert (tmp_path / "app" / "page.tsx").read_text() == "// mine\n"


def test_the_builder_gets_the_nextjs_skill_in_its_prompt(tmp_path):
    model = ScriptedModel([PLAN_MD, "built nothing"])
    agent = BuilderAgent(tmp_path, model)
    agent.build(parse(PLAN_MD, brief="a next.js app router page").approve(),
                "a next.js app router page")
    assert "skill: nextjs" in model.seen[0]["messages"][0]["content"]


# --- QA ---------------------------------------------------------------------

@pytest.mark.parametrize("blob, kind", [
    ('Unable to find an element by: [data-testid="empty"]', "MISSING_TESTID"),
    ("Found multiple elements with the role", "AMBIGUOUS"),
    ("Cannot find module '@/lib/db'", "IMPORT"),
    ("Test timed out in 5000ms", "TIMEOUT"),
    ("net::ERR_CONNECTION_REFUSED at http://127.0.0.1:3000", "SERVER_DOWN"),
    ("expected 500 to be 200", "HANDLER_ERROR"),
])
def test_failures_are_classified_so_the_repair_knows_where_to_start(blob, kind):
    assert classify(blob) == kind


def test_the_unit_report_is_read_out_of_vitests_json(tmp_path):
    _unit_report(tmp_path, 'Unable to find an element by: [data-testid="empty"]')
    report = unit.run(tmp_path, runner=lambda *a: "$ vitest\n[exit 1]")
    assert report.ran and report.passed == 2 and report.failed == 1
    assert report.kinds() == [("MISSING_TESTID", 1)]
    assert not report.green


def test_a_run_that_wrote_no_report_is_not_called_green(tmp_path):
    report = unit.run(tmp_path, runner=lambda *a: "vitest: not found")
    assert not report.ran and not report.green and report.failures


def test_the_e2e_report_walks_playwrights_nested_suites(tmp_path):
    _e2e_report(tmp_path, ok=False)
    report = e2e.run(tmp_path, runner=lambda *a: "$ playwright\n[exit 1]")
    assert report.failed == 1 and report.kinds() == [("MISSING_TESTID", 1)]
    assert "items.spec.ts" in report.prompt()


def test_qa_repairs_what_it_finds_and_runs_again(tmp_path):
    runs = []

    def runner(root, command, timeout=None):
        runs.append(command)
        _unit_report(root, 'Unable to find an element by: [data-testid="empty"]'
                     if len(runs) == 1 else None)
        return f"$ {command}\n[exit {1 if len(runs) == 1 else 0}]"

    model = ScriptedModel([
        tool_call("read_file", path="components/ItemList.tsx"),
        tool_call("write_file", path="components/ItemList.tsx",
                  content='export default () => <ul data-testid="empty" />;'),
        "Added the missing testid.",
    ])
    report = QAAgent(tmp_path, model, runner=runner).verify("unit")
    assert report.green and len(runs) == 2


def test_qa_stops_and_says_so_when_a_repair_changes_nothing(tmp_path):
    def runner(root, command, timeout=None):
        _unit_report(root, 'Unable to find an element by: [data-testid="empty"]')
        return f"$ {command}\n[exit 1]"

    report = QAAgent(tmp_path, ScriptedModel(["I cannot see it"] * 20),
                     runner=runner).verify("unit", rounds=5)
    assert not report.green
    assert "still fail" in report.note


def test_the_qa_author_gets_the_matching_skill(tmp_path):
    model = ScriptedModel(["wrote them", "wrote them"])
    agent = QAAgent(tmp_path, model, runner=lambda *a: "")
    agent.author("unit", "an item tracker")
    agent.author("e2e", "an item tracker")
    assert "skill: vitest" in model.seen[0]["messages"][0]["content"]
    assert "skill: playwright" in model.seen[1]["messages"][0]["content"]


# --- the pipeline and its events -------------------------------------------

def test_the_pipeline_runs_scaffold_plan_build_then_both_suites(tmp_path):
    def runner(root, command, timeout=None):
        if "vitest" in command:
            _unit_report(root)
        if "playwright" in command:
            _e2e_report(root, ok=True)
        return f"$ {command}\n[exit 0]"

    model = ScriptedModel([
        tool_call("list_files", path=""), PLAN_MD,
        tool_call("write_file", path="lib/items.ts", content="export const items = [];"),
        tool_call("write_file", path="components/ItemList.tsx", content="export default () => null;"),
        "Built both files.",
        tool_call("write_file", path="tests/unit/list.test.tsx", content="// unit"),
        "Unit suite written.",
        tool_call("write_file", path="tests/e2e/items.spec.ts", content="// e2e"),
        "E2E suite written.",
    ])
    events = []
    result = Pipeline(tmp_path, model, emit=events.append, qa_runner=runner).run(
        "an item tracker", name="tracker", title="Tracker")

    assert result.built and result.green
    assert result.files == ["lib/items.ts", "components/ItemList.tsx"]
    steps = [(e["step"], e["status"]) for e in events if e["type"] == "step"]
    assert steps[:2] == [("scaffold", "run"), ("scaffold", "done")]
    assert ("build", "done") in steps and ("e2e", "done") in steps
    assert result.as_dict()["reports"]["unit"]["green"] is True


def test_a_pipeline_stopped_at_the_plan_never_reaches_qa(tmp_path):
    events = []
    result = Pipeline(tmp_path, ScriptedModel([PLAN_MD] * 4),
                      emit=events.append).run("a tracker", approve=lambda p: False)
    assert result.stopped_at == "plan" and not result.built
    assert [e for e in events if e["type"] == "test_result"] == []


@pytest.mark.parametrize("event, expected", [
    ({"type": "tool", "name": "read_file", "args": {"path": "a.ts"}}, "log"),
    ({"type": "compact", "saved": 900, "after": 100}, "log"),
    ({"type": "stage", "step": "build", "status": "done"}, "step"),
    ({"type": "plan", "plan": {"files": [], "steps": []}}, "plan"),
])
def test_agent_events_become_the_websocket_messages_the_ui_knows(event, expected):
    assert to_ws(event)[0]["type"] == expected


def test_a_suite_event_carries_the_pass_or_fail_the_ui_shows():
    message = to_ws({"type": "suite", "report": {
        "kind": "unit", "green": False, "passed": 3, "failed": 1, "failures": []}})[0]
    assert message == {"type": "test_result", "status": "fail", "kind": "unit",
                       "passed": 3, "failed": 1, "failures": []}


def test_an_unknown_event_is_dropped_rather_than_forwarded():
    assert to_ws({"type": "something_new"}) == []


def test_the_plan_gate_blocks_until_the_ui_answers():
    sent = []
    gate = PlanGate(sent.append, timeout=2, on_timeout="reject")
    plan = parse(PLAN_MD, brief="a tracker")

    threading.Timer(0.05, gate.approve).start()
    assert gate.gate(plan) is True
    assert sent[0]["type"] == "plan_review"

    threading.Timer(0.05, lambda: gate.revise("split the store")).start()
    assert gate.gate(plan) == "split the store"


def test_a_plan_nobody_answers_falls_to_the_timeout_rule_out_loud():
    sent = []
    assert PlanGate(sent.append, timeout=1, on_timeout="reject").gate(
        parse(PLAN_MD, brief="a tracker")) is False
    assert "rejected by the timeout rule" in sent[-1]["text"]


# --- what a live run against real npm and playwright turned up --------------

def test_the_pipeline_installs_before_it_tries_to_test(tmp_path):
    """Nothing else puts node_modules there, and QA is useless without it."""
    ran = []

    def runner(root, command, timeout=None):
        ran.append(command)
        _unit_report(root)
        return f"$ {command}\n[exit 0]"

    model = ScriptedModel([PLAN_MD, tool_call("write_file", path="a.ts",
                                              content="export const a = 1;"),
                           "built", tool_call("write_file",
                                              path="tests/unit/a.test.ts",
                                              content="// t"), "tests"])
    Pipeline(tmp_path, model, qa_runner=runner).run("a thing", kinds=("unit",))
    assert any(c.startswith("npm install") for c in ran), ran
    assert ran.index(next(c for c in ran if c.startswith("npm install"))) == 0


def test_an_install_is_skipped_when_the_toolchain_is_already_there(tmp_path):
    (tmp_path / "node_modules").mkdir()
    ran = []
    pipeline = Pipeline(tmp_path, ScriptedModel([]),
                        qa_runner=lambda root, c, t=None: ran.append(c) or "[exit 0]")
    assert pipeline.install() is True
    assert ran == []


def test_a_suite_that_could_not_run_never_reports_green(tmp_path):
    """A blocked end-to-end stage must not leave the run looking green."""
    def runner(root, command, timeout=None):
        if "playwright install" in command:
            return f"$ {command}\n[exit 1]\nFailed to download Chrome"
        _unit_report(root)
        return f"$ {command}\n[exit 0]"

    model = ScriptedModel([PLAN_MD, tool_call("write_file", path="a.ts",
                                              content="export const a = 1;"),
                           "built", tool_call("write_file",
                                              path="tests/unit/a.test.ts",
                                              content="// t"), "tests"])
    result = Pipeline(tmp_path, model, qa_runner=runner).run("a thing")
    assert result.reports["unit"].green
    assert "e2e" in result.reports, "a skipped suite still has to be reported"
    assert not result.reports["e2e"].ran
    assert result.green is False, "a run that never tested end to end is not green"
    assert "could not run" in result.summary()


def test_a_run_missing_a_requested_suite_entirely_is_not_green(tmp_path):
    from forge.service import PipelineResult
    from forge.qa import QAReport
    done = PipelineResult(brief="x", built=True, requested=("unit", "e2e"),
                          reports={"unit": QAReport(kind="unit", ran=True, passed=3)})
    assert done.green is False
    done.reports["e2e"] = QAReport(kind="e2e", ran=True, passed=1)
    assert done.green is True


def test_playwright_json_is_read_from_stdout_when_no_file_was_written(tmp_path):
    """`--reporter=json` overrides the config's outputFile and prints instead."""
    printed = json.dumps({"suites": [{"file": "tests/e2e/a.spec.ts", "specs": [
        {"title": "works", "ok": True, "tests": []}]}]})
    report = e2e.run(tmp_path, runner=lambda *a: f"Running 1 test\n{printed}")
    assert report.ran and report.passed == 1 and report.green


def test_a_missing_browser_is_an_environment_problem_not_a_test_failure():
    from forge.qa.report import Failure, QAReport, classify
    blob = "browserType.launch: Executable doesn't exist at /opt/pw-browsers"
    assert classify(blob) == "BROWSER_MISSING"
    report = QAReport(kind="e2e", ran=True,
                      failures=[Failure.make("a.spec.ts", "works", blob)])
    assert report.environmental()


def test_qa_does_not_try_to_repair_an_environment_failure(tmp_path):
    """Rewriting a test cannot install a browser, so it must not try."""
    rounds = []

    def runner(root, command, timeout=None):
        rounds.append(command)
        path = Path(root, e2e.REPORT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"suites": [{"file": "a.spec.ts", "specs": [
            {"title": "works", "ok": False, "tests": [{"results": [
                {"status": "failed", "error": {
                    "message": "browserType.launch: Executable doesn't exist"}}]}]}]}]}))
        return f"$ {command}\n[exit 1]"

    model = ScriptedModel(["I would rewrite the test"] * 10)
    report = QAAgent(tmp_path, model, runner=runner).verify("e2e", rounds=5)
    assert not report.green
    assert "could not run" in report.note
    assert len(rounds) == 1, "it must not re-run after an environment failure"
