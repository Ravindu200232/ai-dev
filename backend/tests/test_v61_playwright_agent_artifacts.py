"""Playwright-style plans, fixtures and incremental scenario generation."""
from __future__ import annotations

import json
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent
from qa_agent.flows import Scenario, Selector, Step, to_playwright_js
from agents.builder.scaffolding.base import ArchitectScaffoldMixin


def _agent(tmp_path):
    arch = SimpleNamespace(
        project_dir=tmp_path,
        files={"app/orders/page.jsx": "export default function Orders() { return <h1>Orders</h1> }"},
        plan={
            "demo_accounts": [
                {"role": "owner", "email": "owner@example.com", "password": "Owner123!"},
                {"role": "customer", "email": "customer@example.com", "password": "Customer123!"},
            ],
            "capabilities": [],
        },
    )
    return E2EAgent(arch, tmp_path), arch


def _journey():
    return {
        "title": "Customer checks an order",
        "role": "customer",
        "covers": ["CAP-001"],
        "steps": ["Open /orders", "Read the persisted order status"],
        "routes": ["/orders"],
        "served_routes": ["/orders"],
        "preconditions": ["The customer owns a seeded order"],
        "pre_journey": {
            "database": "fresh_deterministic_seed",
            "account": {"role": "customer", "never_reuse_another_role": True},
            "required_records": [{"entity": "orders", "minimum": 1,
                                  "stable_real_id": True}],
        },
        "expected_results": ["The persisted status is visible"],
        "contract": {
            "title": "Customer checks an order",
            "actor": "customer",
            "requires_session": True,
            "workflow_steps": ["Open /orders", "Read the persisted order status"],
            "requirements": ["Customers can inspect their own order"],
            "proofs": ["The persisted status is visible"],
            "source_files": ["app/orders/page.jsx"],
            "missing_source_files": [],
            "handoffs": [],
            "expects_mutation": False,
        },
    }


def _scenario():
    return Scenario(
        title="Customer checks an order",
        role="customer",
        steps=[
            Step("GOTO", value="/orders"),
            Step("EXPECT_TEXT", selector=Selector(
                kind="text", pattern="pending|complete", flags="i", is_regex=True)),
        ],
    )


def test_shared_seed_fixture_and_markdown_plan_are_persistent(tmp_path):
    agent, _ = _agent(tmp_path)
    journey = _journey()

    agent.ensure_playwright_artifacts([journey])

    fixture = (tmp_path / "tests/e2e/fixtures.js").read_text(encoding="utf-8")
    seed = (tmp_path / "tests/e2e/seed.spec.js").read_text(encoding="utf-8")
    plan = (tmp_path / "specs/customer-checks-an-order.md").read_text(encoding="utf-8")
    assert '"owner"' in fixture and '"customer"' in fixture
    assert "No seeded account exists for exact role" in fixture
    assert "|| roleAccounts" not in fixture
    assert "seed environment is ready" in seed
    assert "fresh_deterministic_seed" in plan
    assert "stable_real_id" in plan
    assert "The persisted status is visible" in plan
    assert "Skip, block, or an unexecuted step is not a pass" in plan


def test_generated_specs_resolve_the_exact_role_fixture():
    body = to_playwright_js(_scenario())

    assert "from './fixtures.js'" in body
    assert "test.use({ agentforgeRole: 'customer' })" in body
    assert "async ({ page, roleAccount })" in body


def test_generated_projects_pin_current_playwright_and_expose_agent_refresh():
    pinned = ArchitectScaffoldMixin.NEXT_PINNED

    assert pinned["devDependencies"]["@playwright/test"] == "^1.62.1"
    assert pinned["scripts"]["test:e2e"] == "playwright test"
    assert (pinned["scripts"]["test:e2e:agents"] ==
            "playwright init-agents --loop=vscode")


def test_only_unchanged_green_journey_reuses_its_scenario(tmp_path):
    agent, arch = _agent(tmp_path)
    journey = _journey()
    scenario = _scenario()

    agent.remember_generated_scenario(journey, scenario)
    cached = agent.cached_scenario(journey)
    assert cached is not None
    assert cached.title == scenario.title
    assert [step.verb for step in cached.steps] == ["GOTO", "EXPECT_TEXT"]

    arch.files["app/orders/page.jsx"] += "\n// changed behavior"
    assert agent.cached_scenario(journey) is None


def test_srs_testing_handoff_wins_over_the_plain_workflow(tmp_path):
    agent, arch = _agent(tmp_path)
    arch.plan["workflows"] = [{
        "name": "Customer checks an order",
        "who": "customer",
        "steps": ["Open /orders"],
    }]
    handoff = {
        "testing_contract": {"e2e": [{
            "id": "E2E-001",
            "name": "Customer checks an order",
            "actor": "customer",
            "covers": ["CAP-001"],
            "routes": ["/orders"],
            "steps": ["Open /orders", "Read the status"],
            "proofs": ["The persisted status is visible"],
            "required_actions": ["Inspect the owned order"],
            "pre_journey": {
                "database": "fresh_deterministic_seed",
                "explicit_preconditions": ["The customer owns an order"],
                "account": {"role": "customer", "never_reuse_another_role": True},
            },
        }]},
    }
    fp = tmp_path / ".agentforge/srs/handoff.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps(handoff), encoding="utf-8")

    journey = agent.journeys()[0]

    assert journey["testing_contract_id"] == "E2E-001"
    assert journey["pre_journey"]["database"] == "fresh_deterministic_seed"
    assert journey["preconditions"] == ["The customer owns an order"]
    assert journey["expected_results"] == ["The persisted status is visible"]
