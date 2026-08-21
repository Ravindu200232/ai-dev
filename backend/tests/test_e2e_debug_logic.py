from types import SimpleNamespace
import unittest

from qa_agent.debugger import AgenticE2EDebugger
from qa_agent.debugger_rules import diagnose_request_contract
from qa_agent.e2e_progress import failure_signature, measure_progress, stop_after_no_progress


class FakeArch:
    def __init__(self, files):
        self.files = files


class E2EDebugLogicTests(unittest.TestCase):
    def failure(self, *, kind="BEHAVIOR", target="app/checkout/page.jsx", message="failed"):
        return SimpleNamespace(kind=kind, target=target, name="journey", message=message)

    def test_form_post_to_request_json_is_diagnosed_without_llm(self):
        arch = FakeArch({
            "app/api/orders/route.js": "export async function POST(request) { const body = await request.json(); }",
            "app/checkout/page.jsx": "export default function Checkout() {}",
        })
        packet = (
            "POST http://localhost:5173/api/orders :: "
            "HTTP 500 POST http://localhost:5173/api/orders | "
            "content-type=application/x-www-form-urlencoded | "
            "request-body=full_name=Jane&address=Colombo — "
            "response=Unexpected token 'u'"
        )
        result = diagnose_request_contract(None, arch, [self.failure()], packet)
        self.assertIsNotNone(result)
        self.assertEqual(result["verdict"], "APP_FIX")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["evidence_rule"], "request-content-type-vs-request.json")
        self.assertEqual(result["files"][0], "app/api/orders/route.js")


    def test_debugger_uses_contract_rule_before_llm_round(self):
        arch = FakeArch({
            "app/api/orders/route.js": "export async function POST(request) { return request.json(); }",
            "app/checkout/page.jsx": "export default function Checkout() {}",
        })
        packet = (
            "HTTP 500 POST http://localhost:5173/api/orders | "
            "content-type=application/x-www-form-urlencoded | "
            "request-body=full_name=Jane — response=invalid JSON"
        )

        class FakeAgent:
            _last_run_evidence = {"last_good_index": 6}
            def incident_evidence(self, failures, journey=None, dev_trace=""):
                return packet
            def _render(self, scenario):
                return ""

        class NoLLMDebugger(AgenticE2EDebugger):
            def _ask(self, prompt):
                raise AssertionError("LLM should not be called for deterministic contract mismatch")

        result = NoLLMDebugger(FakeAgent(), arch).investigate([self.failure()])
        self.assertEqual(result["verdict"], "APP_FIX")
        self.assertEqual(result["evidence_rule"], "request-content-type-vs-request.json")

    def test_http_400_payload_schema_mismatch_is_app_fix(self):
        arch = FakeArch({
            "app/api/orders/route.js": (
                "export async function POST(request) { const body = await request.json(); "
                "if (!body.items) return Response.json({error: 'missing items'}, {status: 400}); }"
            ),
            "app/checkout/page.jsx": "export default function Checkout() {}",
        })
        packet = (
            "POST http://localhost:5173/api/orders :: "
            "HTTP 400 POST http://localhost:5173/api/orders | "
            "content-type=application/json | request-body={\"fullName\":\"Jane\"} — "
            "response={\"error\":\"missing required items\"}"
        )
        result = diagnose_request_contract(None, arch, [self.failure()], packet)
        self.assertIsNotNone(result)
        self.assertEqual(result["evidence_rule"], "http400-request-schema")
        self.assertEqual(result["verdict"], "APP_FIX")

    def test_same_step_lower_severity_is_not_progress(self):
        before = [self.failure(kind="BEHAVIOR", message="POST /api/orders returned 500")]
        after = [self.failure(kind="SELECTOR", message="submit button not found")]
        seen = {failure_signature(before)}
        progressed, reason = measure_progress(
            before, after, seen, before_step=7, after_step=7
        )
        self.assertFalse(progressed)
        self.assertIn("same scenario step", reason)

    def test_actual_step_advance_is_progress(self):
        before = [self.failure(kind="SELECTOR", message="rating missing")]
        after = [self.failure(kind="ASSERTION", message="review not visible")]
        progressed, reason = measure_progress(
            before, after, {failure_signature(before)}, before_step=7, after_step=8
        )
        self.assertTrue(progressed)
        self.assertIn("scenario step", reason)

    def test_regression_is_never_progress(self):
        before = [self.failure(kind="SELECTOR", message="later obstruction")]
        after = [self.failure(kind="SELECTOR", message="earlier obstruction")]
        progressed, reason = measure_progress(
            before, after, set(), before_step=8, after_step=5
        )
        self.assertFalse(progressed)
        self.assertIn("regressed", reason)

    def test_exhausted_rejected_hypothesis_stops_before_round_floor(self):
        self.assertTrue(stop_after_no_progress(2, False, 4, exhausted=True))
        self.assertFalse(stop_after_no_progress(2, False, 4, exhausted=False))


if __name__ == "__main__":
    unittest.main()

class NavigationLoopRegressionTests(unittest.TestCase):
    def test_loop_guard_stops_same_page_burst_and_supports_dynamic_routes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from qa_agent.e2e import E2EAgent

        class Analyzer:
            def enumerate_routes(self):
                return {
                    "/": {"kind": "page", "file": "app/page.jsx"},
                    "/catalogue": {"kind": "page", "file": "app/catalogue/page.jsx"},
                    "/product/[id]": {"kind": "page", "file": "app/product/[id]/page.jsx"},
                    "/api/products": {"kind": "api", "file": "app/api/products/route.js"},
                }

        class Ctx:
            def route(self, matcher, handler):
                self.matcher, self.handler = matcher, handler

        class Route:
            def __init__(self):
                self.aborted = 0
                self.continued = 0
            def continue_(self):
                self.continued += 1
            def abort(self, _reason):
                self.aborted += 1

        class Req:
            method = "GET"
            headers = {}
            resource_type = "fetch"
            def __init__(self, url):
                self.url = url

        with TemporaryDirectory() as td:
            arch = SimpleNamespace(project_dir=Path(td), files={}, plan={})
            agent = E2EAgent(arch, Path(td), analyzer=Analyzer())
            ctx = Ctx()
            agent._install_route_loop_guard(ctx, burst=5, window_seconds=2.0)
            self.assertRegex("http://localhost:5173/catalogue?_rsc=x", ctx.matcher)
            self.assertRegex("http://localhost:5173/product/abc123?_rsc=x", ctx.matcher)
            self.assertNotRegex("http://localhost:5173/api/products", ctx.matcher)

            route = Route()
            for _ in range(5):
                ctx.handler(route, Req("http://localhost:5173/catalogue?_rsc=x"))
            self.assertGreaterEqual(route.aborted, 1)
            fault = agent._consume_route_loop_fault()
            self.assertIn("NAVIGATION_LOOP: /catalogue", fault)

    def test_navigation_loop_is_diagnosed_without_selector_guessing(self):
        from qa_agent.debugger_rules import diagnose_navigation_loop

        arch = FakeArch({
            "app/catalogue/page.jsx": (
                "import RefreshClient from '@/components/RefreshClient'\n"
                "export default function Page(){ return <RefreshClient/> }"
            ),
            "components/RefreshClient.jsx": (
                "'use client'\nimport { useRouter } from 'next/navigation'\n"
                "export default function RefreshClient(){ const router=useRouter(); "
                "router.refresh(); return null }"
            ),
        })
        class Agent:
            def _page_for(self, route):
                return "app/catalogue/page.jsx" if route == "/catalogue" else ""
        failure = SimpleNamespace(
            kind="BEHAVIOR", target="app/catalogue/page.jsx", name="journey",
            message="NAVIGATION_LOOP: /catalogue was requested 10 times")
        result = diagnose_navigation_loop(Agent(), arch, [failure], failure.message)
        self.assertIsNotNone(result)
        self.assertEqual(result["verdict"], "APP_FIX")
        self.assertEqual(result["evidence_rule"], "same-page-request-burst")
        self.assertIn("app/catalogue/page.jsx", result["files"])
        self.assertIn("components/RefreshClient.jsx", result["files"])
