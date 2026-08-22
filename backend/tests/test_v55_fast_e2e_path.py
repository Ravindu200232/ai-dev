"""Fast E2E keeps real browser proof while avoiding duplicate work."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]


def test_dropped_executable_step_requires_a_model_rewrite(monkeypatch):
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "normalize_scenario_selectors", lambda sc: [])
    monkeypatch.setattr(server, "scenario_contract_issue", lambda *a, **k: "")
    monkeypatch.setattr(server, "elog", lambda *a, **k: None)
    scenario = SimpleNamespace(
        steps=[object()] * 24,
        dropped=[(30, "EXPECT_TEXT :: /saved/i", "over the 24-step cap")],
        is_runnable=lambda: "",
    )
    agent = SimpleNamespace(
        _is_business_step=lambda step: False,
        grounding_issue=lambda scenario, journey: "",
    )

    issue = server._e2e_scenario_issue(agent, scenario, {"contract": {}})
    assert "executable step" in issue
    assert "over the 24-step cap" in issue


def test_a_malformed_dropped_step_still_requires_a_rewrite(monkeypatch):
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "normalize_scenario_selectors", lambda sc: [])
    monkeypatch.setattr(server, "scenario_contract_issue", lambda *a, **k: "")
    scenario = SimpleNamespace(
        steps=[object()] * 4,
        dropped=[(8, "CLICK :: bad-selector", "CLICK needs a selector")],
        is_runnable=lambda: "",
    )
    agent = SimpleNamespace(_is_business_step=lambda step: False)

    issue = server._e2e_scenario_issue(agent, scenario, {"contract": {}})

    assert "executable step" in issue
    assert "CLICK needs a selector" in issue


def test_green_first_run_skips_duplicate_clean_room(monkeypatch, tmp_path):
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "E2E_FINAL_CLEAN_ROOM", True)
    monkeypatch.setattr(server, "elog", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(server, "_e2e_clean_room_once",
                        lambda *a, **k: calls.append("replayed") or {})
    agent = SimpleNamespace(
        accepted_scenario=lambda journey: object(),
        global_integrity=lambda: [],
    )
    out = {"failed": 0, "fixed": 0}

    server._e2e_final_clean_room(
        agent, SimpleNamespace(), tmp_path,
        SimpleNamespace(), SimpleNamespace(), [], [], out)

    assert calls == []
    assert out["clean_room_skipped"] is True


def test_cloud_model_calls_are_bounded_and_follow_the_qa_think_switch():
    author = (ROOT / "qa_agent" / "e2e_journeys.py").read_text(encoding="utf-8")
    debugger = (ROOT / "qa_agent" / "debugger_investigate.py").read_text(
        encoding="utf-8")
    stage = (ROOT / "server_modules" / "qa" / "e2e_stage.py").read_text(
        encoding="utf-8")

    assert "max_output_tokens=AUTHOR_OUTPUT_TOKENS" in author
    assert "reasoning=QASession.reasoning_for(self.qa)" in author
    assert "max_output_tokens=DEBUGGER_OUTPUT_TOKENS" in debugger
    assert "reasoning=QASession.reasoning_for" in debugger
    assert "agent.preground(sc, journey)" in stage
