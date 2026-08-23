"""Action feedback stays explicit from planning through build and E2E."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLANNER_A = (ROOT / "agents/planner/prompt_a.py").read_text(encoding="utf-8")
PLANNER_B = (ROOT / "agents/planner/prompt_b.py").read_text(encoding="utf-8")
BUILDER_A = (ROOT / "agents/builder/prompts/part_a.py").read_text(encoding="utf-8")
BUILDER_B = (ROOT / "agents/builder/prompts/part_b.py").read_text(encoding="utf-8")
E2E = (ROOT / "qa_agent/e2e_common.py").read_text(encoding="utf-8")
SRS_GENERATORS = ROOT / "srs-agent/srs_agent/app/generators"
SRS_PROMPT = (SRS_GENERATORS / "builder_prompt.py").read_text(encoding="utf-8")
SRS_CONTRACT = (SRS_GENERATORS / "builder_contract.py").read_text(encoding="utf-8")


class PlannerToastContractTests(unittest.TestCase):
    def test_toasts_are_feedback_not_an_invented_product_feature(self):
        self.assertIn("Transient in-app toast feedback is", PLANNER_A)
        self.assertIn("do not invent the email/SMS/inbox", PLANNER_B)

    def test_every_real_operation_plans_both_outcomes(self):
        self.assertIn("has BOTH an exact success-toast message", PLANNER_B)
        self.assertIn("an exact error-toast message", PLANNER_B)
        self.assertIn("success is\n    emitted only after", PLANNER_B)

    def test_toast_is_not_the_only_business_proof(self):
        self.assertIn("a toast alone", PLANNER_A.lower())
        self.assertIn("is not proof", PLANNER_A.lower())
        self.assertIn("never the sole\nbusiness proof", PLANNER_A)

    def test_shared_accessible_host_is_planned_once(self):
        self.assertIn("components/ToastProvider.jsx", PLANNER_B)
        self.assertIn("Put it in task 1 before any consumer", PLANNER_B)
        self.assertIn("role=status", PLANNER_B)
        self.assertIn("role=alert", PLANNER_B)

    def test_machine_readable_examples_include_the_toast_contract(self):
        self.assertIn("show '<<Child>> created'", PLANNER_A)
        self.assertIn("failure toast 'Could not update task'", PLANNER_A)


class BuilderToastContractTests(unittest.TestCase):
    def test_builder_waits_for_confirmed_success(self):
        merged = BUILDER_A + BUILDER_B
        self.assertIn("success toast only\n     after confirmed completion", BUILDER_A)
        self.assertIn("emits the exact planned error toast", BUILDER_B)
        self.assertIn("never use `window.alert`", merged.lower())

    def test_layout_may_host_provider_but_not_auth_chrome(self):
        self.assertIn("optional ToastProvider wrapper", BUILDER_A)
        self.assertIn("NEVER render Navbar/Header/Sidebar", BUILDER_A)

    def test_accessible_semantics_and_durable_proof_survive(self):
        self.assertIn("role=status", BUILDER_B)
        self.assertIn("role=alert", BUILDER_B)
        self.assertIn("toast complements those states and never", BUILDER_B)


class HandoffAndE2EToastTests(unittest.TestCase):
    def test_srs_handoff_carries_the_invariant(self):
        self.assertIn("Every non-navigation user operation", SRS_PROMPT)
        self.assertIn("Every non-navigation user operation", SRS_CONTRACT)
        self.assertIn("distinct from required transient action toasts", SRS_PROMPT)

    def test_e2e_observes_toast_then_durable_result(self):
        self.assertIn("assert the observed success toast with EXPECT_TEXT", E2E)
        self.assertIn("A toast alone is not business proof", E2E)


if __name__ == "__main__":
    unittest.main()
