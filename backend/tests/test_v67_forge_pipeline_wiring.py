"""The forge pipeline, reached the way the server actually reaches it."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import server
import server_runtime
from forge.llm import ScriptedModel, tool_call
from forge.qa import e2e, unit
from server_modules.forge.bridge import UI_STAGE, ui_relay

PLAN_MD = """## Current state
A fresh scaffold.

## Files to change
- `lib/items.ts` — the item store
- `components/ItemList.tsx` — renders the items

## Steps
1. Write lib/items.ts
2. Write components/ItemList.tsx

## Risks
- the empty state needs a testid
"""


def _script():
    return ScriptedModel([
        tool_call("list_files", path=""), PLAN_MD,
        tool_call("write_file", path="lib/items.ts", content="export const items = [];"),
        tool_call("write_file", path="components/ItemList.tsx",
                  content='export default () => <ul data-testid="empty" />;'),
        "Built both files.",
        tool_call("write_file", path="tests/unit/items.test.tsx", content="// unit"),
        "Unit suite written.",
        tool_call("write_file", path="tests/e2e/items.spec.ts", content="// e2e"),
        "E2E suite written.",
    ])


def _fake_unit(root, command, timeout=None):
    Path(root, unit.REPORT).write_text(json.dumps({"testResults": [
        {"name": "tests/unit/items.test.tsx", "status": "passed",
         "assertionResults": [{"fullName": "renders", "status": "passed"}]}]}))
    return f"$ {command}\n[exit 0]"


def _fake_e2e(root, command, timeout=None):
    path = Path(root, e2e.REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"suites": [{"file": "tests/e2e/items.spec.ts",
        "specs": [{"title": "adds an item", "ok": True, "tests": []}]}]}))
    return f"$ {command}\n[exit 0]"


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """The runtime, pointed at a temp projects dir with a scripted model.

    The plan gate is given a short fuse so a test never waits on a human.
    """
    sent = []
    monkeypatch.setattr(server, "emit", sent.append)
    monkeypatch.setattr(server, "PROD_DIR", tmp_path)
    monkeypatch.setattr(server, "FORGE_PLAN_TIMEOUT", 0.2)
    monkeypatch.setattr("forge.llm.Model", lambda name, *a, **k: _script())
    monkeypatch.setattr(unit, "run_command", _fake_unit)
    monkeypatch.setattr(e2e, "run_command", _fake_e2e)
    return sent


# --- the wiring itself ------------------------------------------------------

def test_the_stage_is_loaded_by_the_runtime_assembler():
    parts = list(server_runtime._RUNTIME_PARTS)
    assert "server_modules/forge/stage.py" in parts
    assert (parts.index("server_modules/forge/stage.py")
            < parts.index("server_modules/agent/builder/project_ops.py"))


def test_the_runtime_namespace_exposes_the_forge_entry_points():
    for name in ("run_forge_pipeline", "forge_decide", "FORGE_GATES"):
        assert hasattr(server, name), f"{name} is not in the runtime namespace"


def test_the_websocket_handler_dispatches_the_forge_messages():
    source = Path("server_modules/agent/builder/project_ops.py").read_text(
        encoding="utf-8")
    assert '"forge_build"' in source and "run_forge_pipeline" in source
    assert '"plan_decision"' in source and "forge_decide" in source


# --- the stage vocabulary ---------------------------------------------------

def test_forge_stages_collapse_onto_the_two_the_overlay_draws():
    sent = []
    relay = ui_relay(sent.append)
    for step in ("scaffold", "plan", "build", "unit", "e2e"):
        relay({"type": "stage", "step": step, "status": "run"})
        relay({"type": "stage", "step": step, "status": "done"})
    relay.close()
    assert [(m["step"], m["status"]) for m in sent] == [
        ("build", "run"), ("build", "done"), ("test", "run"), ("test", "done")]
    assert set(UI_STAGE.values()) == {"build", "test"}


def test_a_failed_stage_is_reported_as_an_error_not_swallowed():
    sent = []
    relay = ui_relay(sent.append)
    relay({"type": "stage", "step": "unit", "status": "run"})
    relay({"type": "stage", "step": "unit", "status": "error"})
    relay.close()
    assert ("test", "error") in [(m["step"], m["status"]) for m in sent]


def test_non_step_events_pass_straight_through():
    sent = []
    ui_relay(sent.append)({"type": "tool", "name": "read_file",
                           "args": {"path": "a.ts"}})
    assert sent and sent[0]["type"] == "log"


# --- a whole run through the server entry point -----------------------------

def test_a_forge_run_builds_verifies_and_reports_done(wired, tmp_path):
    server.run_forge_pipeline("an item tracker", model="test-model")

    kinds = [m["type"] for m in wired]
    assert "project" in kinds and "done" in kinds
    assert "error" not in kinds, [m for m in wired if m["type"] == "error"]

    result = next(m for m in wired if m["type"] == "forge_result")["result"]
    assert result["built"] and result["green"]
    assert result["files"] == ["lib/items.ts", "components/ItemList.tsx"]
    assert result["reports"]["unit"]["green"] and result["reports"]["e2e"]["green"]

    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.ts*")}
    assert any(p.endswith("components/ItemList.tsx") for p in written)
    assert any(p.endswith("playwright.config.ts") for p in written)


def test_the_run_reports_the_two_overlay_stages_in_order(wired):
    server.run_forge_pipeline("an item tracker", model="test-model")
    assert [(m["step"], m["status"]) for m in wired if m["type"] == "step"] == [
        ("build", "run"), ("build", "done"), ("test", "run"), ("test", "done")]


def test_the_plan_is_put_to_the_user_before_anything_is_built(wired):
    server.run_forge_pipeline("an item tracker", model="test-model")
    kinds = [m["type"] for m in wired]
    review = kinds.index("plan_review")
    first_file = next(i for i, m in enumerate(wired)
                      if m["type"] == "file" and m["name"] == "lib/items.ts")
    assert review < first_file, "the plan must be reviewed before any file lands"


def test_a_finished_run_leaves_no_gate_behind(wired):
    server.run_forge_pipeline("an item tracker", model="test-model")
    assert server.FORGE_GATES == {}


def test_a_plan_decision_from_the_socket_reaches_the_waiting_run(
        tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(server, "emit", sent.append)
    monkeypatch.setattr(server, "PROD_DIR", tmp_path)
    monkeypatch.setattr(server, "FORGE_PLAN_TIMEOUT", 10)   # long: answer it
    monkeypatch.setattr("forge.llm.Model", lambda name, *a, **k: _script())
    monkeypatch.setattr(unit, "run_command", _fake_unit)
    monkeypatch.setattr(e2e, "run_command", _fake_e2e)

    answered = threading.Event()

    def approve_when_asked():
        for _ in range(1_000):
            for name in list(server.FORGE_GATES):
                if server.forge_decide(name, "approve"):
                    answered.set()
                    return
            threading.Event().wait(0.01)

    threading.Thread(target=approve_when_asked, daemon=True).start()
    server.run_forge_pipeline("an item tracker", model="test-model")

    assert answered.is_set(), "the run never registered a gate to answer"
    result = next(m for m in sent if m["type"] == "forge_result")["result"]
    assert result["built"] and result["plan"]["approved"]


def test_a_decision_with_no_run_waiting_is_refused_not_crashed():
    assert server.forge_decide("nothing-is-running", "approve") is False


def test_a_rejected_plan_writes_nothing_and_says_so(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(server, "emit", sent.append)
    monkeypatch.setattr(server, "PROD_DIR", tmp_path)
    monkeypatch.setattr(server, "FORGE_PLAN_TIMEOUT", 10)
    monkeypatch.setattr("forge.llm.Model",
                        lambda name, *a, **k: ScriptedModel([PLAN_MD] * 4))

    def reject_when_asked():
        for _ in range(1_000):
            for name in list(server.FORGE_GATES):
                if server.forge_decide(name, "reject"):
                    return
            threading.Event().wait(0.01)

    threading.Thread(target=reject_when_asked, daemon=True).start()
    server.run_forge_pipeline("an item tracker", model="test-model")

    result = next(m for m in sent if m["type"] == "forge_result")["result"]
    assert result["built"] is False and result["stopped_at"] == "plan"
    assert not list(tmp_path.rglob("lib/items.ts"))
