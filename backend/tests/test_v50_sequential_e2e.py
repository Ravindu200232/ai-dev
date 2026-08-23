"""Sequential planner-driven E2E orchestration contracts."""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace


def test_journeys_run_one_after_another(monkeypatch, tmp_path):
    server = importlib.import_module("server")
    journeys = [{"title": f"journey {n}", "role": ""}
                for n in range(1, 7)]
    state = {"active": 0, "peak": 0}
    order = []

    class Coordinator:
        _accepted_scenarios = {}
        _journey_outcomes = {}

        def journeys(self):
            return list(journeys)

        def accounts(self):
            return []

        def global_integrity(self):
            return []

    class Baseline:
        def restore(self):
            raise AssertionError("a green sequential run must not roll back")

    def one_flow(agent, arch, proj_dir, qa, analyzer, out, journey):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        order.append(journey["title"])
        state["active"] -= 1
        return {**out, "ran": True, "flow": journey["title"], "failed": 0}

    monkeypatch.setattr(server, "_e2e_one_flow", one_flow)
    monkeypatch.setattr(server, "_prepare_app_before_journeys",
                        lambda *a, **k: ([], Baseline()))
    monkeypatch.setattr(server, "_warm_routes_async", lambda *a, **k: None)
    monkeypatch.setattr(server, "_dev_alive", lambda *a, **k: True)
    monkeypatch.setattr(server, "_reseed_for_journey", lambda *a, **k: None)
    monkeypatch.setattr(server, "_e2e_final_clean_room", lambda *a, **k: None)
    monkeypatch.setattr(server, "terminal_faults", lambda *a, **k: [])
    monkeypatch.setattr(server, "dev_log_mark", lambda: 0)
    monkeypatch.setattr(server, "dev_log_since", lambda *a, **k: [])
    monkeypatch.setattr(server, "elog", lambda *a, **k: None)
    monkeypatch.setattr(server, "E2E_GLOBAL_REPAIR_ATTEMPTS", 0)

    out = {"ran": False, "passed": 0, "failed": 0, "fixed": 0,
           "unwritable": 0, "blocked": 0}
    result = server._e2e_rounds(
        Coordinator(), SimpleNamespace(files={}), tmp_path,
        SimpleNamespace(), SimpleNamespace(), out)

    assert state["peak"] == 1
    assert order == [row["title"] for row in journeys]
    assert result["passed"] == 6
    assert len(result["flows"]) == 6


def test_parallel_coordinator_was_removed():
    root = Path(__file__).parents[1]
    stage = (root / "server_modules" / "qa" / "e2e_stage.py").read_text(
        encoding="utf-8")
    final = (root / "server_modules" / "qa" / "e2e_final.py").read_text(
        encoding="utf-8")

    assert not (root / "server_modules" / "qa" / "e2e_parallel.py").exists()
    assert "run_all_at_once" not in stage + final
    assert "ThreadPoolExecutor" not in stage + final
    assert "walked one after another" in stage


def test_each_failed_journey_has_exactly_two_repair_rounds():
    policy = importlib.import_module("server_modules.qa.e2e_policy")
    assert policy.E2E_REPAIR_ROUNDS == 2
    assert policy.E2E_BASE_FIX == 2
    assert policy.E2E_HARD_FIX == 2
    assert policy.E2E_PROGRESS_BONUS == 0
    assert policy.E2E_AUTHOR_REWRITE_ATTEMPTS == 1
    assert policy.E2E_FINAL_REPAIR_ATTEMPTS == 0


def test_two_round_limit_stays_visible_and_is_not_counted_as_pass(monkeypatch):
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "elog", lambda *a, **k: None)
    monkeypatch.setattr(server, "emit", lambda *a, **k: None)
    flows = [{
        "title": "Checkout", "role": "customer", "ran": True,
        "failed": 1, "blocked": 0, "blocked_upstream": False,
        "unwritable": 0,
    }]
    out = {
        "ran": True, "passed": 0, "failed": 1, "blocked": 0,
        "unwritable": 0,
        "failures": [{"case": "Pay", "message": "selector still failed"}],
    }

    changed = server._e2e_pass_with_warnings(out, flows, 1)

    assert changed is True
    assert out["status"] == "incomplete"
    assert out["passed"] == 0 and out["failed"] == 1
    assert out["test_issue"] == 1
    assert flows[0]["status"] == "failed"
    assert flows[0]["failed"] == 1 and flows[0]["test_issue"] == 1
    assert flows[0]["ran"] is True


def test_live_events_and_planner_keep_identity_and_logout_contracts():
    root = Path(__file__).parents[1]
    execution = (root / "qa_agent" / "e2e_execution.py").read_text(encoding="utf-8")
    planner = (root / "agents" / "planner" / "prompt_b.py").read_text(encoding="utf-8")
    builder = (root / "agents" / "builder" / "prompts" / "part_b.py").read_text(
        encoding="utf-8")

    assert '"journey_id"' in execution
    assert '"journey_count"' in execution
    assert "clicks logout" in planner
    assert "lands on `/login`" in planner
    assert "router.replace('/login'); router.refresh()" in builder
