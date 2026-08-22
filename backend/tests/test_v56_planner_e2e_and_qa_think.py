"""E2E follows planner workflows, validates generated code, and honors QA Think."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent
from qa_agent.session import QASession


ROOT = Path(__file__).parents[1]


class Routes:
    def __init__(self, rows):
        self.rows = rows

    def enumerate_routes(self):
        return self.rows


def test_planned_journey_is_preflighted_against_generated_code(tmp_path):
    files = {
        "app/admin/orders/page.jsx": (
            "export default function Page(){return <form>"
            "<input name='quantity'/><select name='status'></select>"
            "<button>Save order</button></form>}"),
        "app/api/orders/route.js": "export async function POST(){}",
    }
    routes = {
        "/admin/orders": {
            "kind": "page", "file": "app/admin/orders/page.jsx",
            "dynamic": False,
        },
        "/api/orders": {
            "kind": "api", "file": "app/api/orders/route.js",
            "dynamic": False, "methods": ["POST"],
        },
    }
    arch = SimpleNamespace(
        project_dir=tmp_path,
        files=files,
        plan={
            "demo_accounts": [
                {"email": "admin@x.io", "password": "pw", "role": "admin"},
            ],
            "workflows": [{
                "name": "Manage orders", "who": "admin",
                "steps": ["go to /admin/orders", "save an order"],
                "covers": ["CAP-ORDERS"],
            }],
            "capabilities": [{
                "id": "CAP-ORDERS", "who": "admin",
                "requirement": "manage orders",
                "proof": "the saved order is visible",
                "files": ["app/admin/orders/page.jsx",
                          "app/api/orders/route.js"],
            }],
        },
    )
    agent = E2EAgent(arch, tmp_path, analyzer=Routes(routes))

    journeys = agent.journeys()

    assert journeys[0]["title"] == "Manage orders"
    assert journeys[0]["role"] == "admin"
    assert journeys[0]["covers"] == ["CAP-ORDERS"]
    assert journeys[0]["preflight"]["planner_checked"] is True
    assert journeys[0]["preflight"]["code_checked"] is True
    assert journeys[0]["preflight"]["status"] == "ready"
    assert journeys[0]["preflight"]["served_routes"] == ["/admin/orders"]
    assert journeys[0]["contract"]["source_files"] == [
        "app/admin/orders/page.jsx", "app/api/orders/route.js"]


def test_missing_planned_implementation_is_recorded_before_authoring(tmp_path):
    arch = SimpleNamespace(
        project_dir=tmp_path,
        files={"app/page.jsx": "export default function Page(){return <main/>}"},
        plan={
            "workflows": [{
                "name": "Missing flow", "steps": ["go to /missing"],
                "covers": ["CAP-MISSING"],
            }],
            "capabilities": [{
                "id": "CAP-MISSING", "requirement": "missing screen",
                "files": ["app/missing/page.jsx"],
            }],
        },
    )
    routes = {"/": {"kind": "page", "file": "app/page.jsx", "dynamic": False}}
    journey = E2EAgent(arch, tmp_path, analyzer=Routes(routes)).journeys()[0]

    assert journey["preflight"]["status"] == "gap"
    assert journey["preflight"]["missing_source_files"] == [
        "app/missing/page.jsx"]
    assert journey["preflight"]["unserved_routes"] == ["/missing"]


def test_e2e_authoring_uses_planner_and_real_code_evidence():
    journeys = (ROOT / "qa_agent" / "e2e_journeys.py").read_text(
        encoding="utf-8")
    context = (ROOT / "qa_agent" / "e2e_context.py").read_text(
        encoding="utf-8")
    prepare = (ROOT / "server_modules" / "qa" / "e2e_prepare.py").read_text(
        encoding="utf-8")

    assert 'plan.get("workflows")' in journeys
    assert "planner_code_preflight" in journeys
    assert "PLANNER AND GENERATED-CODE PREFLIGHT" in journeys
    assert "JOURNEY SOURCE CONTRACT — ACTUAL CODE ON DISK" in journeys
    assert "capability_contract" in context
    assert "_analyze_before_journeys" in prepare
    assert "analyzer.scan()" in prepare


def test_generated_seed_credentials_win_over_planner_metadata(tmp_path):
    arch = SimpleNamespace(
        project_dir=tmp_path,
        files={"lib/seed.js": (
            "const DEMO_PASSWORD = 'source-pass'\n"
            "const accounts = [{ email: 'admin@source.dev', "
            "password: DEMO_PASSWORD, role: 'admin' }]")},
        plan={"demo_accounts": [{"email": "planner@wrong.dev",
                                  "password": "wrong", "role": "ghost"}]},
    )
    agent = E2EAgent(arch, tmp_path)

    assert agent.accounts() == [{"email": "admin@source.dev",
                                 "password": "source-pass", "role": "admin"}]


def test_qa_reasoning_follows_the_shared_think_state(tmp_path):
    off = QASession(tmp_path, reasoning=False)
    on = QASession(tmp_path, reasoning=True)

    assert QASession.reasoning_for(off) is False
    assert QASession.reasoning_for(on) is True
    assert QASession.reasoning_for(None) is False

    pipeline = (ROOT / "server_modules" / "agent" / "builder" / "pipeline.py").read_text(
        encoding="utf-8")
    feature = (ROOT / "server_modules" / "agent" / "feature" / "actions.py").read_text(
        encoding="utf-8")
    assert "reasoning=bool(think)" in pipeline
    assert "reasoning=bool(think)" in feature

    for rel in (
        "qa_agent/author_write.py",
        "qa_agent/e2e_journeys.py",
        "qa_agent/debugger_investigate.py",
        "server_modules/qa/unit_stage.py",
        "server_modules/qa/runtime_repair.py",
    ):
        body = (ROOT / rel).read_text(encoding="utf-8")
        assert "QASession.reasoning_for" in body, rel


def test_studio_describes_thinking_as_builder_and_qa():
    sidebar = (ROOT.parent / "studio" / "components" / "Sidebar.jsx").read_text(
        encoding="utf-8")
    home = (ROOT.parent / "studio" / "components" / "Home.jsx").read_text(
        encoding="utf-8")

    assert "Builder and QA work" in sidebar
    assert "builder + QA thinking on" in home

