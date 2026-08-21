from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from qa_agent.debugger import AgenticE2EDebugger
from qa_agent.e2e import E2EAgent
from qa_agent.flows import Scenario, Selector, Step


class Arch:
    def __init__(self, root, *, files=None, plan=None):
        self.project_dir = Path(root)
        self.files = files or {}
        self.plan = plan or {}


class E2EEvidenceRepairTests(unittest.TestCase):
    def make_agent(self, td, *, files=None, plan=None):
        return E2EAgent(Arch(td, files=files, plan=plan), Path(td))

    def test_checkpoint_route_can_preserve_business_query_state(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            page = SimpleNamespace(
                url="http://localhost:5173/checkout?bookingId=abc123&source=room"
            )
            self.assertEqual(agent._route_from_page(page), "/checkout")
            self.assertEqual(
                agent._route_from_page(page, include_query=True),
                "/checkout?bookingId=abc123&source=room",
            )

    def test_resume_point_keeps_exact_query_string(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            sc = Scenario(
                title="booking",
                role="guest",
                steps=[
                    Step("GOTO", value="/room/abc"),
                    Step("CLICK", Selector(kind="role", role="button", pattern="book", is_regex=False)),
                    Step("CLICK", Selector(kind="role", role="button", pattern="pay now", is_regex=False)),
                ],
            )
            failure = SimpleNamespace(name=sc.steps[2].describe())
            agent._last_run_evidence = {
                "failed_index": 2,
                "route_before": "/checkout?bookingId=abc123",
                "route": "/checkout?bookingId=abc123",
                "storage_state": {"cookies": [{"name": "session", "value": "x"}]},
                "step_trace": [],
            }
            start, route, state = agent._resume_point(sc, failure)
            self.assertEqual(start, 2)
            self.assertEqual(route, "/checkout?bookingId=abc123")
            self.assertTrue(state)

    def test_visitor_null_session_does_not_force_full_replay(self):
        with TemporaryDirectory() as td:
            base = self.make_agent(td)

            class ResumeAgent(E2EAgent):
                def __init__(self, original):
                    self.__dict__.update(original.__dict__)
                    self.calls = []
                def run(self, sc, journey=None, **kwargs):
                    self.calls.append(kwargs)
                    self._last_run_evidence = {
                        "route": "/register",
                        "session": {"status": 200, "body": None},
                    }
                    return [SimpleNamespace(message="nothing matched field 'name'")]

            agent = ResumeAgent(base)
            sc = Scenario(title="registration", role="", steps=[Step("GOTO", value="/register"), Step("FILL", Selector(kind="field", pattern="email"), "a@b.com")])
            failure = SimpleNamespace(name=sc.steps[1].describe())
            agent._last_run_evidence = {
                "failed_index": 1,
                "route_before": "/register",
                "route": "/register",
                "storage_state": {"cookies": []},
                "step_trace": [],
            }
            agent.run_resume(sc, journey={"role": "visitor"}, failure=failure)
            self.assertEqual(len(agent.calls), 1)
            self.assertEqual(agent.calls[0]["initial_url"], "/register")

    def test_authenticated_null_session_does_not_replay_completed_business_steps(self):
        with TemporaryDirectory() as td:
            base = self.make_agent(td)

            class ResumeAgent(E2EAgent):
                def __init__(self, original):
                    self.__dict__.update(original.__dict__)
                    self.calls = []
                def run(self, sc, journey=None, **kwargs):
                    self.calls.append(kwargs)
                    self._last_run_evidence = {
                        "route": "/checkout?bookingId=abc",
                        "session": {"status": 200, "body": None},
                    }
                    return [SimpleNamespace(message="protected action missing")]

            agent = ResumeAgent(base)
            sc = Scenario(title="booking", role="guest", steps=[Step("GOTO", value="/checkout"), Step("CLICK", Selector(kind="role", role="button", pattern="pay now", is_regex=False))])
            failure = SimpleNamespace(name=sc.steps[1].describe())
            agent._last_run_evidence = {
                "failed_index": 1,
                "route_before": "/checkout?bookingId=abc",
                "route": "/checkout?bookingId=abc",
                "storage_state": {"cookies": [{"name": "session", "value": "x"}]},
                "step_trace": [],
            }
            agent.run_resume(sc, journey={"role": "guest"}, failure=failure)
            self.assertEqual(len(agent.calls), 1)
            self.assertEqual(agent.calls[0]["initial_url"], "/checkout?bookingId=abc")

    def test_invented_form_field_is_rejected_before_browser(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            agent.runtime_evidence = lambda journey=None: (
                "DOM /register\n"
                "fields: input type=email name=email id=email | "
                "input type=password name=password id=password\n"
                "controls: button Create account"
            )
            agent._journey_pages = lambda journey=None: []
            sc = Scenario(title="registration", steps=[
                Step("GOTO", value="/register"),
                Step("FILL", Selector(kind="field", pattern="name"), "Ravindu"),
            ])
            issue = agent.grounding_issue(
                sc,
                {"title": "Registration", "steps": ["enter email", "enter password", "submit registration"]},
            )
            self.assertIn("ungrounded form field", issue)
            self.assertIn("field=name", issue)

    def test_workflow_required_field_is_left_for_real_app_diagnosis(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            agent.runtime_evidence = lambda journey=None: (
                "fields: input type=email name=email | input type=password name=password"
            )
            agent._journey_pages = lambda journey=None: []
            sc = Scenario(title="registration", steps=[
                Step("GOTO", value="/register"),
                Step("FILL", Selector(kind="field", pattern="name"), "Ravindu"),
            ])
            issue = agent.grounding_issue(
                sc,
                {"title": "Registration", "steps": ["enter full name", "enter email", "submit registration"]},
            )
            self.assertEqual(issue, "")

    def test_noop_test_patch_is_not_counted_as_a_repair(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            step = Step("FILL", Selector(kind="field", pattern="email", is_regex=False), "a@b.com")
            sc = Scenario(title="registration", steps=[step])
            failure = SimpleNamespace(name=step.describe())
            changed = agent.patch_scenario_step(sc, failure, "FILL :: field=email :: a@b.com")
            self.assertFalse(changed)
            self.assertFalse(getattr(agent, "_scenario_changed", False))

    def test_debugger_uses_dom_grounded_field_patch_without_llm(self):
        failure = SimpleNamespace(
            kind="SELECTOR", target="app/register/page.jsx",
            name="FILL :: field=name :: Ravindu",
            message="nothing matched field 'name'",
        )
        sc = Scenario(title="registration", steps=[
            Step("FILL", Selector(kind="field", pattern="name"), "Ravindu")
        ])

        class Agent:
            _last_run_evidence = {"last_good_index": 0}
            def incident_evidence(self, failures, journey=None, dev_trace=""):
                return "## DOM at failure\n  fields: input type=text name=fullName id=fullName"
            def dom_grounded_test_patch(self, failure, scenario=None):
                return "FILL :: field=fullName :: Ravindu"
            def _render(self, scenario):
                return ""

        class NoLLM(AgenticE2EDebugger):
            def _ask(self, prompt):
                raise AssertionError("exact DOM field repair must bypass the LLM")

        dbg = NoLLM(Agent(), SimpleNamespace(files={}, plan={}))
        result = dbg.investigate([failure], scenario=sc)
        self.assertEqual(result["verdict"], "TEST_FIX")
        self.assertEqual(result["evidence_rule"], "dom-grounded-field-locator")
        self.assertIn("field=fullName", result["test_patch"])

    def test_field_selector_arbitration_does_not_use_unrelated_page_copy(self):
        arch = SimpleNamespace(
            files={"app/register/page.jsx": "const profileName = 'Create account'; export default function Page(){}"},
            plan={"workflows": [{"name": "Registration", "steps": ["enter email", "enter password", "create account"]}]},
        )
        failure = SimpleNamespace(
            kind="SELECTOR", target="app/register/page.jsx", name="Registration user name",
            message="nothing matched field 'name'",
        )
        packet = (
            "## DOM at failure\n"
            "  headings: Join the Elite\n"
            "  visible: Create your account and manage your profile name later\n"
            "  fields: input type=email name=email id=email | input type=password name=password id=password\n"
            "  controls: button Create account\n"
            "## Source\nconst name = 'not a form control'"
        )
        dbg = AgenticE2EDebugger(SimpleNamespace(), arch)
        dbg.notebook.goal = "Registration"
        result = dbg._selector_arbitration([failure], packet)
        self.assertEqual(result["verdict"], "TEST_FIX")
        self.assertNotIn("rendered DOM contains", result["root"])
        self.assertIn("not backed", result["root"])

    def test_missing_required_button_is_app_fix_even_if_visible_prose_mentions_it(self):
        arch = SimpleNamespace(
            files={"app/checkout/page.jsx": "export default function Checkout(){}"},
            plan={"workflows": [{"name": "Managing Payments", "steps": ["click Pay Now", "confirm payment"]}]},
        )
        failure = SimpleNamespace(
            kind="SELECTOR", target="app/checkout/page.jsx", name="payment",
            message="nothing matched button role /pay now/i",
        )
        packet = (
            "## DOM at failure\n"
            "  headings: Booking Error\n"
            "  visible: Your booking is unavailable. Pay now cannot continue.\n"
            "  fields: none\n"
            "  controls: link Back to rooms href=/\n"
        )
        dbg = AgenticE2EDebugger(SimpleNamespace(), arch)
        dbg.notebook.goal = "Managing Payments"
        result = dbg._selector_arbitration([failure], packet)
        self.assertEqual(result["verdict"], "APP_FIX")
        self.assertIn("workflow requires", result["root"])
        self.assertNotIn("rendered DOM contains", result["root"])


if __name__ == "__main__":
    unittest.main()


class E2EFewerRoundRegressionTests(E2EEvidenceRepairTests):
    def test_url_shorthand_is_real_regex_in_python_and_exported_js(self):
        from qa_agent.flows import parse_scenario, to_playwright_js
        sc = parse_scenario("FLOW :: room detail\nGOTO :: /rooms\nEXPECT_URL :: /rooms/.*\nDONE")
        sel = sc.steps[1].selector
        self.assertTrue(sel.is_regex)
        self.assertTrue(sel.compiled().search("http://localhost:5173/rooms/abc123"))
        js = to_playwright_js(sc)
        self.assertIn(r"toHaveURL(/\/rooms\/.*/)", js)

    def test_checkpoint_resume_remembers_prior_business_action(self):
        with TemporaryDirectory() as td:
            base = self.make_agent(td)

            class ProbeAgent(E2EAgent):
                def __init__(self, original):
                    self.__dict__.update(original.__dict__)
                    self.seen = None
                def _step(self, page, st, next_step=None):
                    self.seen = bool(self._business_started)
                    return ("BEHAVIOR" if self._business_started else "ASSERTION", "missing proof")

            agent = ProbeAgent(base)
            sc = Scenario(title="price", role="admin", steps=[
                Step("GOTO", value="/admin/rooms"),
                Step("FILL", Selector(kind="field", pattern="price"), "999"),
                Step("CLICK", Selector(kind="role", role="button", pattern="save", is_regex=False)),
                Step("EXPECT_TEXT", Selector(kind="text", pattern=r"\\$999", is_regex=True)),
            ])
            self.assertTrue(agent._business_state_at(sc, 3))
            self.assertFalse(agent._business_state_at(sc, 1))

    def test_missing_testid_for_required_cancel_action_is_app_fix(self):
        arch = SimpleNamespace(
            files={"app/my-bookings/page.jsx": "export default function Page(){}"},
            plan={"workflows": [{"name": "Cancelling a Booking", "steps": ["cancel booking"]}]},
        )
        failure = SimpleNamespace(
            kind="SELECTOR", target="app/my-bookings/page.jsx", name="cancel",
            message="nothing matched testid 'cancel-booking'",
        )
        packet = "## DOM at failure\n  controls: link View booking href=/bookings/1"
        dbg = AgenticE2EDebugger(SimpleNamespace(), arch)
        dbg.notebook.goal = "Cancelling a Booking"
        result = dbg._selector_arbitration([failure], packet)
        self.assertEqual(result["verdict"], "APP_FIX")

    def test_dynamic_user_copy_is_grounded_after_fill(self):
        with TemporaryDirectory() as td:
            agent = self.make_agent(td)
            agent.runtime_evidence = lambda journey=None: "fields: textarea name=review\ncontrols: button Submit"
            agent._journey_pages = lambda journey=None: []
            sc = Scenario(title="review", steps=[
                Step("GOTO", value="/review"),
                Step("FILL", Selector(kind="field", pattern="review"), "The stay was wonderful"),
                Step("CLICK", Selector(kind="role", role="button", pattern="submit", is_regex=False)),
                Step("EXPECT_TEXT", Selector(kind="text", pattern="The stay was wonderful", is_regex=False)),
            ])
            self.assertEqual(agent.grounding_issue(sc, {"title": "Leaving a Review"}), "")


class E2EDeterministicDiagnosisTests(unittest.TestCase):
    def test_relevant_api_400_is_app_fix_before_selector_guess(self):
        from qa_agent.debugger_rules import diagnose_api_http_error
        arch = SimpleNamespace(files={
            "app/my-bookings/page.jsx": "fetch('/api/bookings')",
            "app/api/bookings/route.js": "export async function GET(){}",
        })
        agent = SimpleNamespace(_last_run_evidence={
            "step_network_errors": [
                "HTTP 400 GET http://localhost:5173/api/bookings — response={\\\"error\\\":\\\"bad query\\\"}"
            ]
        })
        failure = SimpleNamespace(target="app/my-bookings/page.jsx")
        result = diagnose_api_http_error(agent, arch, [failure], "")
        self.assertEqual(result["verdict"], "APP_FIX")
        self.assertIn("app/api/bookings/route.js", result["files"])

    def test_failed_proof_after_mutation_is_app_fix_without_llm(self):
        from qa_agent.debugger_rules import diagnose_post_mutation_proof
        with TemporaryDirectory() as td:
            agent = E2EAgent(Arch(td, files={
                "app/rooms/[id]/page.jsx": "export default function Page(){}",
                "app/admin/rooms/page.jsx": "export default function Admin(){}",
            }), Path(td))
            sc = Scenario(title="price", steps=[
                Step("GOTO", value="/admin/rooms"),
                Step("CLICK", Selector(kind="role", role="button", pattern="Save price", is_regex=False)),
                Step("EXPECT_TEXT", Selector(kind="text", pattern=r"\\$999", is_regex=True)),
            ])
            agent._last_run_evidence = {
                "failed_index": 2,
                "route_before": "/rooms/abc",
            }
            failure = SimpleNamespace(kind="BEHAVIOR", target="app/rooms/[id]/page.jsx")
            result = diagnose_post_mutation_proof(agent, agent.arch, [failure], sc)
            self.assertEqual(result["verdict"], "APP_FIX")
            self.assertEqual(result["evidence_rule"], "post-mutation-behavior-proof")


def test_runtime_reference_error_outranks_dom_grounded_selector_test_fix():
    failure = SimpleNamespace(
        kind="SELECTOR", target="app/rooms/[id]/page.jsx",
        name="FILL :: field=start :: 2026-08-25",
        message="nothing matched field 'start'",
    )
    sc = Scenario(title="booking", steps=[
        Step("FILL", Selector(kind="field", pattern="start"), "2026-08-25")
    ])
    body = "\n".join([
        "import { getCollection } from '@/lib/mongodb'",
        "export default async function RoomDetailPage(){",
        "  const rooms = await getCollection('rooms')",
        "  const docs = await rooms.find({}).toArray()",
        "  const x = docs.length",
        "  const y = x + 1",
        "  const z = y + 1",
        "  const a = z + 1",
        "  const b = a + 1",
        "  const c = b + 1",
        "  const d = c + 1",
        "  const e = d + 1",
        "  const f = e + 1",
        "  const g = f + 1",
        "  const h = g + 1",
        "  const i = h + 1",
        "  const j = i + 1",
        "  const k = j + 1",
        "  const l = k + 1",
        "  const m = l + 1",
        "  const n = m + 1",
        "  const o = n + 1",
        "  const p = o + 1",
        "  const q = p + 1",
        "  const r = q + 1",
        "  const s = r + 1",
        "  const t = s + 1",
        "  const u = t + 1",
        "  const v = u + 1",
        "  const w = v + 1",
        "  const user = await getSessionUser()",
        "  return <main>{user?.name}</main>",
        "}",
    ])

    class Agent:
        _last_run_evidence = {"last_good_index": 0}
        def incident_evidence(self, failures, journey=None, dev_trace=""):
            return (
                "## DOM at failure\n  fields: input type=date name=checkIn id=checkIn\n"
                "PageError: ReferenceError: getSessionUser is not defined\n"
                "at RoomDetailPage (app/rooms/[id]/page.jsx:31:22)"
            )
        def dom_grounded_test_patch(self, failure, scenario=None):
            return "FILL :: field=checkIn :: 2026-08-25"
        def _render(self, scenario):
            return "booking scenario"
        def source_of(self, rel, cap=7000):
            return body

    class FixedLLM(AgenticE2EDebugger):
        def _ask(self, prompt):
            return (
                "VERDICT :: TEST_FIX\n"
                "ROOT :: selector could target checkIn\n"
                "FILES :: NONE\n"
                "TEST_STEP :: FILL :: field=start :: 2026-08-25\n"
                "TEST_PATCH :: FILL :: field=checkIn :: 2026-08-25\n"
                "HYPOTHESIS :: selector mismatch\n"
                "NEXT :: patch selector\n"
                "CONFIDENCE :: high"
            )

    arch = SimpleNamespace(files={"app/rooms/[id]/page.jsx": body}, plan={})
    result = FixedLLM(Agent(), arch).investigate([failure], scenario=sc)
    assert result["verdict"] == "APP_FIX"
    assert result["files"] == ["app/rooms/[id]/page.jsx"]
    assert result.get("locations")
