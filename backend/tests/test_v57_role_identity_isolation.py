"""Role-aware seeded accounts stay distinct for every planned role."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.planner.normalization import ArchitectPlanNormalizeMixin
from agents.gates.agent import AnalyzerAgent
from qa_agent.e2e import E2EAgent
from qa_agent.e2e_context import E2EContextMixin
from qa_agent.flows import parse_scenario


class _Normalizer(ArchitectPlanNormalizeMixin):
    def _log(self, *_args, **_kwargs):
        pass


def plan(accounts):
    return {
        "phases": [{"id": 1, "files": [{"path": "app/page.jsx"}]}],
        "demo_accounts": accounts,
    }


class RoleAccountNormalizationTests(unittest.TestCase):
    def test_every_role_gets_a_distinct_identity_without_named_role_rules(self):
        accounts = [
            {"email": "same@demo.com", "password": "pw1", "role": "owner"},
            {"email": "same@demo.com", "password": "pw2", "role": "customer"},
            {"email": "same@demo.com", "password": "pw3", "role": "dispatcher"},
        ]
        got = _Normalizer()._normalise_plan(plan(accounts))["demo_accounts"]
        self.assertEqual(["owner", "customer", "dispatcher"],
                         [row["role"] for row in got])
        self.assertEqual(3, len({row["email"] for row in got}))
        self.assertEqual("same@demo.com", got[0]["email"])
        self.assertEqual("customer@demo.com", got[1]["email"])
        self.assertEqual("dispatcher@demo.com", got[2]["email"])

    def test_single_role_account_is_not_rewritten(self):
        accounts = [{"email": "OWNER@EXAMPLE.TEST", "password": "pw",
                     "role": "owner"}]
        got = _Normalizer()._normalise_plan(plan(accounts))["demo_accounts"]
        self.assertEqual("OWNER@EXAMPLE.TEST", got[0]["email"])


class RoleSeedAnalyzerTests(unittest.TestCase):
    def test_multi_role_collision_is_a_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "lib").mkdir()
            (root / "lib" / "seed.js").write_text("""
                const demoUsers = [
                  { email: 'same@demo.com', password: 'pw', role: 'alpha' },
                  { email: 'same@demo.com', password: 'pw', role: 'beta' },
                  { email: 'gamma@demo.com', password: 'pw', role: 'gamma' },
                ]
            """, encoding="utf-8")
            arch = SimpleNamespace(project_dir=root, plan={"demo_accounts": [
                {"email": "a@x", "password": "pw", "role": "alpha"},
                {"email": "b@x", "password": "pw", "role": "beta"},
                {"email": "c@x", "password": "pw", "role": "gamma"},
            ]})
            analyzer = AnalyzerAgent(arch)
            got = analyzer.role_identity_collisions()
            self.assertEqual(["ROLE_IDENTITY_COLLISION"], [f.code for f in got])

    def test_rule_is_off_without_role_based_auth(self):
        arch = SimpleNamespace(project_dir=Path("."), plan={"demo_accounts": []})
        self.assertEqual([], AnalyzerAgent(arch).role_identity_collisions())


class E2ERoleLoginTests(unittest.TestCase):
    def test_planned_arbitrary_role_is_not_replaced_by_first_account(self):
        arch = SimpleNamespace(files={}, plan={
            "demo_accounts": [
                {"email": "owner@demo.com", "password": "pw", "role": "owner"},
                {"email": "dispatcher@demo.com", "password": "pw", "role": "dispatcher"},
            ],
            "workflows": [{
                "name": "Dispatch work", "who": "dispatcher",
                "steps": ["go to /jobs"],
            }],
        })
        with tempfile.TemporaryDirectory() as td:
            agent = E2EAgent(arch, td)
            journey = agent.journeys()[0]
        self.assertEqual("dispatcher", journey["role"])
        self.assertEqual("dispatcher@demo.com",
                         agent.account_for(journey["role"])["email"])

    def test_seed_property_order_does_not_mix_role_credentials(self):
        seed = """
            const DEMO_PASSWORD = 'password123'
            const users = [
              { role: 'owner', email: 'owner@demo.com', password: DEMO_PASSWORD },
              { password: DEMO_PASSWORD, role: 'customer', email: 'customer@demo.com' },
            ]
        """
        arch = SimpleNamespace(files={"lib/seed.js": seed}, plan={
            "demo_accounts": [
                {"email": "owner@demo.com", "password": "password123",
                 "role": "owner"},
                {"email": "customer@demo.com", "password": "password123",
                 "role": "customer"},
            ]})
        with tempfile.TemporaryDirectory() as td:
            ctx = E2EContextMixin(arch, td)
            self.assertEqual("owner@demo.com", ctx.account_for("owner")["email"])
            self.assertEqual("customer@demo.com",
                             ctx.account_for("customer")["email"])
            self.assertEqual({}, ctx.account_for("dispatcher"))

    def test_rendered_logout_wins_over_a_misleading_login_testid(self):
        scenario = parse_scenario(
            "FLOW :: logout\nAS :: customer\nCLICK :: testid=nav-login\nDONE")
        step = scenario.steps[0]

        class Locator:
            def evaluate(self, _script):
                return "Sign out"

        self.assertEqual("logout", E2EAgent._auth_action_kind(step, Locator()))

    def test_401_after_intentional_logout_is_not_a_login_failure(self):
        agent = E2EAgent.__new__(E2EAgent)
        agent._active_journey = {"role": "customer", "logout": True}
        agent._expected_signed_out = False
        error = "HTTP 401 GET http://localhost:5173/api/cart"
        self.assertEqual(error, agent._authenticated_401([error]))
        agent._expected_signed_out = True
        self.assertEqual("", agent._authenticated_401([error]))


if __name__ == "__main__":
    unittest.main()
