from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from qa_agent.debugger_rules import diagnose_checkpoint_auth
from qa_agent.e2e import E2EAgent
from qa_agent.e2e_invariants import validate_repair_invariants
from qa_agent.e2e_contract import capability_contract, runtime_contract_issue, scenario_contract_issue
from qa_agent.e2e_normalize import normalize_scenario_selectors
from qa_agent.flows import Scenario, Selector, Step


class UltraE2EConvergenceTests(unittest.TestCase):
    def make_agent(self, td, *, files=None, plan=None):
        arch = SimpleNamespace(project_dir=Path(td), files=files or {}, plan=plan or {})
        return E2EAgent(arch, Path(td))

    def test_role_grammar_wrapped_as_text_is_normalized_before_browser(self):
        sc = Scenario(title="admin", steps=[
            Step("EXPECT_TEXT", Selector(kind="text", pattern="role=combobox", is_regex=False))
        ])
        notes = normalize_scenario_selectors(sc)
        self.assertTrue(notes)
        self.assertEqual(sc.steps[0].verb, "WAIT_FOR")
        self.assertEqual(sc.steps[0].selector.kind, "role")
        self.assertEqual(sc.steps[0].selector.role, "combobox")

    def test_field_grammar_wrapped_as_text_is_normalized(self):
        sc = Scenario(steps=[
            Step("EXPECT_TEXT", Selector(kind="text", pattern="field=promoCode", is_regex=False))
        ])
        normalize_scenario_selectors(sc)
        self.assertEqual(sc.steps[0].verb, "WAIT_FOR")
        self.assertEqual(sc.steps[0].selector.kind, "field")
        self.assertEqual(sc.steps[0].selector.pattern, "promocode")

    def test_app_fix_cannot_silently_add_login_redirect_without_auth_evidence(self):
        before = {"app/checkout/page.jsx": "export default function Page(){return <button>Confirm payment</button>}"}
        after = {"app/checkout/page.jsx": "import { redirect } from 'next/navigation'\nexport default function Page(){redirect('/login')}"}
        failure = SimpleNamespace(name="CLICK :: button role /confirm payment/i", message="nothing matched button role /confirm payment/i")
        violations = validate_repair_invariants(
            before, after, ["app/checkout/page.jsx"], "APP_FIX",
            {"root": "required payment control is missing", "hypothesis": "checkout omitted the control"},
            failure=failure, journey={"role": "guest"})
        self.assertTrue(any("authentication/login" in v for v in violations))

    def test_field_repair_must_preserve_selector_semantics(self):
        before = {"app/checkout/page.jsx": "<input placeholder='Promo code' />"}
        after = {"app/checkout/page.jsx": "<input name='amount' placeholder='Promo code' />"}
        failure = SimpleNamespace(name="FILL :: field=promoCode :: SAVE10", message="nothing matched field 'promoCode'")
        violations = validate_repair_invariants(
            before, after, ["app/checkout/page.jsx"], "APP_FIX",
            {"root": "promo code field is inaccessible"}, failure=failure)
        self.assertTrue(any("does not match" in v for v in violations))

    def test_semantically_correct_accessibility_repair_is_allowed(self):
        before = {"app/checkout/page.jsx": "<input placeholder='Promo code' />"}
        after = {"app/checkout/page.jsx": "<input name='promoCode' placeholder='Promo code' />"}
        failure = SimpleNamespace(name="FILL :: field=promoCode :: SAVE10", message="nothing matched field 'promoCode'")
        violations = validate_repair_invariants(
            before, after, ["app/checkout/page.jsx"], "APP_FIX",
            {"root": "promo code field is inaccessible"}, failure=failure)
        self.assertEqual(violations, [])

    def test_checkpoint_auth_failure_is_deterministic_auth_fix(self):
        agent = SimpleNamespace(_last_run_evidence={"resume_auth_error": "could not restore guest demo session"})
        arch = SimpleNamespace(files={
            "app/checkout/page.jsx": "",
            "lib/auth.js": "",
            "app/api/auth/[...all]/route.js": "",
        })
        failure = SimpleNamespace(target="app/checkout/page.jsx")
        got = diagnose_checkpoint_auth(agent, arch, [failure], "")
        self.assertEqual(got["verdict"], "AUTH_FIX")
        self.assertEqual(got["evidence_rule"], "checkpoint-auth-restore")

    def test_select_alone_is_setup_not_a_business_mutation(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            step = Step("SELECT", Selector(kind="field", pattern="status"), "Clean")
            self.assertFalse(agent._is_business_step(step))


    def test_capability_contract_carries_role_proof_and_handoff(self):
        arch = SimpleNamespace(plan={
            "capabilities": [{"id": "CAP-1", "who": "guest",
                              "requirement": "book a room",
                              "proof": "booking is persisted",
                              "files": ["app/checkout/page.jsx"]}],
            "contracts": [{"from": "app/checkout/page.jsx",
                           "target": "/api/bookings",
                           "trigger": "confirm payment",
                           "effect": "create booking"}],
        })
        contract = capability_contract(arch, {"title": "Booking", "role": "guest",
                                              "steps": ["confirm payment"],
                                              "covers": ["CAP-1"]})
        self.assertTrue(contract["requires_session"])
        self.assertTrue(contract["expects_mutation"])
        self.assertIn("booking is persisted", contract["proofs"])
        self.assertEqual(contract["handoffs"][0]["target"], "/api/bookings")

    def test_mutating_contract_requires_proof_after_business_action(self):
        contract = {"actor": "guest", "expects_mutation": True}
        sc = Scenario(role="guest", steps=[
            Step("GOTO", value="/checkout"),
            Step("CLICK", Selector(kind="role", role="button", pattern="confirm payment", is_regex=False)),
        ])
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            issue = scenario_contract_issue(contract, sc, agent._is_business_step)
        self.assertIn("never proves", issue)

    def test_runtime_contract_requires_non_auth_mutation(self):
        contract = {"expects_mutation": True}
        sc = Scenario(steps=[
            Step("CLICK", Selector(kind="role", role="button", pattern="confirm payment", is_regex=False)),
            Step("EXPECT_TEXT", Selector(kind="text", pattern="paid", is_regex=False)),
        ])
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            issue = runtime_contract_issue(contract, sc, {"mutation_events": [
                {"method": "POST", "url": "http://localhost/api/auth/sign-in/email",
                 "status": 200, "step_index": 0}
            ]}, agent._is_business_step)
        self.assertIn("no successful non-auth mutation", issue)

    def test_runtime_contract_accepts_real_business_mutation(self):
        contract = {"expects_mutation": True}
        sc = Scenario(steps=[
            Step("CLICK", Selector(kind="role", role="button", pattern="confirm payment", is_regex=False)),
            Step("EXPECT_TEXT", Selector(kind="text", pattern="paid", is_regex=False)),
        ])
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            issue = runtime_contract_issue(contract, sc, {"mutation_events": [
                {"method": "POST", "url": "http://localhost/api/bookings",
                 "status": 201, "step_index": 0}
            ]}, agent._is_business_step)
        self.assertEqual(issue, "")

    def test_last_green_scenario_is_copied_for_clean_room(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            sc = Scenario(title="booking", steps=[Step("GOTO", value="/rooms")])
            journey = {"title": "Booking"}
            agent.remember_scenario(journey, sc)
            sc.steps[0].value = "/changed"
            saved = agent.accepted_scenario(journey)
            self.assertEqual(saved.steps[0].value, "/rooms")


if __name__ == "__main__":
    unittest.main()
