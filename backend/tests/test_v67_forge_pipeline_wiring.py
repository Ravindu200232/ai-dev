"""The server: its API, its socket protocol, and the run it drives."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from forge.llm import ScriptedModel, tool_call
from forge.qa import e2e, unit
from forge.server import api, events, runs, state, ws
from forge.server.gate import PlanGate

PLAN_MD = """## Current state
A fresh scaffold.

## Files to change
- `lib/items.ts` — the item store

## Steps
1. Write lib/items.ts

## Risks
- none worth naming
"""


def _script():
    return ScriptedModel([
        tool_call("list_files", path=""), PLAN_MD,
        tool_call("write_file", path="lib/items.ts",
                  content="export const items = [];"),
        "Built the store.",
        tool_call("write_file", path="tests/unit/items.test.ts", content="// unit"),
        "Unit suite written.",
    ])


def _unit_report(root):
    Path(root, unit.REPORT).write_text(json.dumps({"testResults": [
        {"name": "tests/unit/items.test.ts", "status": "passed",
         "assertionResults": [{"fullName": "works", "status": "passed"}]}]}))


def _shell(root, command, timeout=None):
    if "vitest" in command:
        _unit_report(root)
    return f"$ {command}\n[exit 0]"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """The server pointed at a temp projects dir, with nothing real running."""
    sent = []
    monkeypatch.setattr(events, "emit", sent.append)
    monkeypatch.setattr(state, "projects_dir", lambda: tmp_path)
    monkeypatch.setattr(runs, "PLAN_TIMEOUT", 0.2)
    monkeypatch.setattr(runs, "Model", lambda name, *a, **k: _script())
    monkeypatch.setattr(unit, "run_command", _shell)
    monkeypatch.setattr(e2e, "run_command", _shell)
    monkeypatch.setattr("forge.tools.shell.run_command", _shell)
    runs.CURRENT.update(project="", gate=None, cancelled=False)
    return sent


# --- the entrypoint ---------------------------------------------------------

def test_the_entrypoint_starts_the_forge_server_and_nothing_else():
    body = Path("server.py").read_text(encoding="utf-8")
    assert "from forge.server import run" in body
    for gone in ("server_runtime", "server_modules", "agents", "qa_agent"):
        assert gone not in body, f"server.py still mentions {gone}"


def test_the_old_tree_is_gone():
    for gone in ("agents", "qa_agent", "server_modules", "server_runtime.py"):
        assert not Path(gone).exists(), f"{gone} is still here"


# --- the JSON API -----------------------------------------------------------

def test_the_studio_can_list_projects(server, tmp_path):
    (tmp_path / "shop" / "app").mkdir(parents=True)
    status, body = api.get("/projects")
    assert status == 200
    assert [p["name"] for p in body["projects"]] == ["shop"]
    assert body["projects"][0]["has_app"] is True


def test_a_projects_source_comes_back_without_the_noise(server, tmp_path):
    root = tmp_path / "shop"
    (root / "app").mkdir(parents=True)
    (root / "app" / "page.tsx").write_text("export default () => null;")
    (root / "node_modules" / "next").mkdir(parents=True)
    (root / "node_modules" / "next" / "index.js").write_text("// vendored")
    status, body = api.get("/files/shop")
    assert status == 200
    assert list(body["files"]) == ["app/page.tsx"]


def test_an_unknown_route_is_a_404_not_a_crash(server):
    assert api.get("/nope")[0] == 404
    assert api.post("/nope", {})[0] == 404


def test_a_file_can_be_saved_back(server, tmp_path):
    (tmp_path / "shop" / "app").mkdir(parents=True)
    status, body = api.post("/save-file", {"project": "shop",
                                           "path": "app/page.tsx",
                                           "content": "export default () => 1;"})
    assert status == 200 and body["saved"] == "app/page.tsx"
    assert (tmp_path / "shop" / "app" / "page.tsx").read_text() == "export default () => 1;"


def test_a_save_outside_the_project_is_refused(server, tmp_path):
    (tmp_path / "shop").mkdir()
    status, body = api.post("/save-file", {"project": "shop",
                                           "path": "../escape.txt", "content": "x"})
    assert status == 400 and "escapes" in body["error"]
    assert not (tmp_path / "escape.txt").exists()


def test_a_project_can_be_deleted_but_not_while_it_is_building(server, tmp_path):
    (tmp_path / "shop").mkdir()
    runs.CURRENT["project"] = "shop"
    assert api.post("/delete-project", {"project": "shop"})[0] == 409
    runs.CURRENT["project"] = ""
    assert api.post("/delete-project", {"project": "shop"})[0] == 200
    assert not (tmp_path / "shop").exists()
    assert api.post("/delete-project", {"project": "shop"})[0] == 404


def test_the_api_never_echoes_the_api_key_back(server, monkeypatch):
    monkeypatch.setattr("forge.server.state.load_settings",
                        lambda: {"ollama_api_key": "secret-key", "model": "m"})
    body = api.get("/settings")[1]
    assert body == {"model": "m", "has_api_key": True}
    assert "secret-key" not in json.dumps(body)


# --- the socket protocol ----------------------------------------------------

def test_a_build_message_starts_a_run(server, tmp_path, monkeypatch):
    started = {}
    monkeypatch.setattr(runs, "start",
                        lambda prompt, **kw: started.update(prompt=prompt, **kw))
    ws.handle({"type": "forge_build", "prompt": "an item tracker",
               "model": "m", "qa_model": "q"})
    assert started["prompt"] == "an item tracker"
    assert started["model"] == "m" and started["qa_model"] == "q"


def test_an_empty_prompt_starts_nothing(server, monkeypatch):
    monkeypatch.setattr(runs, "start", lambda *a, **k: pytest.fail("started"))
    ws.handle({"type": "forge_build", "prompt": "   "})


def test_a_chat_message_edits_the_named_project(server, monkeypatch):
    asked = {}
    monkeypatch.setattr(runs, "edit",
                        lambda prompt, project, **kw: asked.update(
                            prompt=prompt, project=project))
    ws.handle({"type": "chat", "project": "shop", "prompt": "make it blue"})
    assert asked == {"prompt": "make it blue", "project": "shop"}


def test_an_unknown_message_is_ignored_rather_than_fatal(server):
    ws.handle({"type": "something_new"})
    ws.handle({})


def test_answering_a_plan_nobody_asked_about_says_so(server):
    ws.handle({"type": "plan_decision", "verdict": "approve"})
    assert any("no run is waiting" in str(m.get("text", "")) for m in server)


def test_cancel_with_nothing_running_says_so(server):
    ws.handle({"type": "cancel"})
    assert any("nothing is running" in str(m.get("text", "")) for m in server)


# --- the gate ---------------------------------------------------------------

def test_the_gate_only_accepts_an_answer_to_a_question_it_asked():
    gate = PlanGate(timeout=1, on_timeout="reject")
    assert gate.approve() is False, "an answer before the question must not stick"

    from forge.plan import parse
    plan = parse(PLAN_MD, brief="x")
    threading.Timer(0.05, gate.approve).start()
    assert gate.gate(plan) is True


def test_a_plan_nobody_answers_falls_to_the_timeout_rule_out_loud():
    from forge.plan import parse
    sent = []
    assert PlanGate(sent.append, timeout=0.2, on_timeout="reject").gate(
        parse(PLAN_MD, brief="x")) is False
    assert "rejected by the timeout rule" in sent[-1]["text"]


# --- a whole run ------------------------------------------------------------

def test_a_run_builds_verifies_and_reports_done(server, tmp_path):
    runs._run("an item tracker", "test-model", "", "", ("unit",))

    kinds = [m["type"] for m in server]
    assert "project" in kinds and "done" in kinds
    assert "error" not in kinds, [m for m in server if m["type"] == "error"]

    result = next(m for m in server if m["type"] == "forge_result")["result"]
    assert result["built"] and result["green"]
    assert result["files"] == ["lib/items.ts"]
    assert (tmp_path / "an-item-tracker" / "lib" / "items.ts").is_file()


def test_a_finished_run_leaves_nothing_behind(server):
    runs._run("an item tracker", "test-model", "", "", ("unit",))
    assert runs.CURRENT == {"project": "", "gate": None, "cancelled": False}
    assert runs.busy() is False


def test_a_second_build_is_refused_while_one_is_running(server):
    runs.CURRENT["project"] = "busy-one"
    assert runs.start("another", model="m") is False
    assert any("already running" in str(m.get("text", "")) for m in server)


def test_a_run_can_be_cancelled(server):
    runs.CURRENT.update(project="shop", gate=PlanGate())
    assert runs.cancel() is True
    assert runs.cancelled() is True


def test_the_project_name_comes_from_the_prompt():
    assert runs.slug("Build me a Shop!") == "build-me-a-shop"
    assert runs.slug("   ") == "app"
    assert len(runs.slug("x" * 200)) <= 40


def test_a_second_project_with_the_same_name_gets_its_own_directory(
        server, tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "app").mkdir()
    assert runs.project_dir_for("shop").name == "shop-2"
