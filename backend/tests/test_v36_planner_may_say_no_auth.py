"""A plan is allowed to say that nobody signs in."""
import tempfile
import unittest
from pathlib import Path

from agents.architect import ArchitectAgent

PROMPT_A = Path("agents/architect_next_planner_prompt_a.py")
PROMPT_B = Path("agents/architect_next_planner_prompt_b.py")


def architect(idea="a unit converter for length and weight", plan=None):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan = plan or {}
    a.user_prompt = idea
    a._vocab_cache = None
    a._app_noun_cache = None
    a.stack = "next"
    a.project_dir = Path(tempfile.gettempdir())
    a.cb = None
    return a


class TheQuestionIsAskedFirstTests(unittest.TestCase):
    def setUp(self):
        self.prompt = architect()._planner_sys()

    def test_the_decision_comes_before_the_sections_that_assume_it(self):
        self.assertIn("DECIDE THIS BEFORE ANYTHING ELSE", self.prompt)
        self.assertLess(self.prompt.index("DECIDE THIS BEFORE ANYTHING ELSE"),
                        self.prompt.index("## Accounts"))

    def test_it_names_what_to_leave_out(self):
        head = self.prompt[:self.prompt.index("OUTPUT FORMAT")]
        for omitted in ("## Accounts", "## Demo Accounts", "signup_role",
                        "demo_accounts", "/login"):
            self.assertIn(omitted, head, omitted)

    def test_an_empty_list_is_not_an_answer(self):
        head = self.prompt[:self.prompt.index("OUTPUT FORMAT")]
        self.assertIn("has not\nanswered no", head)

    def test_nothing_claims_a_login_unconditionally(self):
        self.assertNotIn("`/login` is always on it", self.prompt)
        self.assertNotIn("on every page except login and signup", self.prompt)

    def test_the_json_template_marks_the_auth_keys_as_conditional(self):
        block = self.prompt[self.prompt.index('"signup_role"') - 220:
                            self.prompt.index('"signup_role"')]
        self.assertIn("ONLY when people sign in", block)

    def test_no_image_key_presumes_a_sign_in_page(self):
        self.assertNotIn("login-bg", self.prompt)


class TheGatesLetItThroughTests(unittest.TestCase):
    """Nothing downstream may push a login-less plan back into having one."""

    NO_AUTH = {"phases": [{"files": [{"path": "app/page.jsx"}]}]}
    EMPTY = {"demo_accounts": [], "phases": [{"files": [{"path": "app/page.jsx"}]}]}

    def test_a_plan_with_no_accounts_does_not_need_auth(self):
        for plan in (self.NO_AUTH, self.EMPTY):
            self.assertFalse(architect(plan=plan)._needs_auth())

    def test_the_role_gate_does_not_fire_without_roles(self):
        for plan in (self.NO_AUTH, self.EMPTY):
            self.assertIsNone(architect()._roles_without_areas(plan))

    def test_the_role_gate_still_fires_when_roles_really_are_short(self):
        crowded = {"demo_accounts": [{"role": "a"}, {"role": "b"}, {"role": "c"}],
                   "phases": [{"files": [{"path": "app/admin/page.jsx"}]}]}
        self.assertIsNotNone(architect()._roles_without_areas(crowded))

    def test_an_app_that_does_sign_in_is_unaffected(self):
        plan = {"demo_accounts": [{"email": "a@x", "password": "p", "role": "admin"}],
                "phases": []}
        self.assertTrue(architect(plan=plan)._needs_auth())


class TheE2EAgreesTests(unittest.TestCase):
    def test_a_login_less_plan_produces_a_login_less_scenario_prompt(self):
        from qa_agent.e2e_common import system_prompt
        self.assertIn("THIS APP HAS NO SIGN-IN", system_prompt([]))


if __name__ == "__main__":
    unittest.main()
