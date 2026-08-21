import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import server
from agents.features import FeaturesAgent


class RouteGroundingTests(unittest.TestCase):
    def test_issue_route_prefers_stack_source_over_root(self):
        arch = SimpleNamespace(files={
            "app/page.jsx": "export default function Home(){}",
            "app/shopping-cart/page.jsx": "export default function Cart(){}",
        })
        analyzer = SimpleNamespace(enumerate_routes=lambda: {
            "/": {"kind": "page", "dynamic": False, "file": "app/page.jsx"},
            "/shopping-cart": {"kind": "page", "dynamic": False,
                                "file": "app/shopping-cart/page.jsx"},
        })
        route = server._infer_issue_route(
            "/", "shopping cart is broken",
            "Uncaught TypeError at app/shopping-cart/page.jsx:65:27", "",
            arch, analyzer)
        self.assertEqual(route, "/shopping-cart")

    def test_changed_api_routes_map_back_to_calling_page(self):
        arch = SimpleNamespace(files={
            "app/shopping-cart/page.jsx": "fetch('/api/cart')",
            "app/api/cart/route.js": "export async function GET(){}",
            "app/page.jsx": "export default function Home(){}",
        })
        got = server._routes_affected_by_paths(arch, {"app/api/cart/route.js"})
        self.assertEqual(got, ["/shopping-cart"])


class BugConvergenceTests(unittest.TestCase):
    def test_downstream_runtime_error_is_repaired_in_same_bug_turn(self):
        arch = SimpleNamespace(files={"app/shopping-cart/page.jsx": "x"})
        analyzer = SimpleNamespace()
        seen_bad = SimpleNamespace(ran=True, clicked="", changed=False,
                                   signature=lambda: {"Cannot read price"},
                                   as_prompt=lambda: "toFixed crash")
        seen_clean = SimpleNamespace(ran=True, clicked="", changed=False,
                                     signature=lambda: set(),
                                     as_prompt=lambda: "clean")
        with patch.object(server, "_reproduce_state", side_effect=[
            (seen_bad, "Cannot read properties of undefined", {"Cannot read price"}),
            (seen_clean, "", set()),
        ]), patch.object(server, "_repair_runtime",
                         return_value=["app/api/cart/route.js"]) as repair, \
             patch.object(server, "verify_after_edit", return_value={
                 "build_ok": True, "syntax_broken": [], "broken_imports": 0}):
            ok, writes, _ = server._stabilize_bug_repair(
                arch, Path("."), "demo", analyzer,
                route="/shopping-cart", complaint="cart broken", model="m",
                baseline_signature={"old cart failure"})
        self.assertTrue(ok)
        self.assertEqual(writes, ["app/api/cart/route.js"])
        self.assertEqual(repair.call_count, 1)


class CompactEditMemoryTests(unittest.TestCase):
    def test_feature_memory_is_compact_and_source_authoritative(self):
        convo = [{"role": "system", "content": "sys"}]
        for i in range(20):
            convo.append({"role": "user" if i % 2 == 0 else "assistant",
                          "content": (f"turn-{i} " * 1200)})
        arch = SimpleNamespace(project_dir=Path("."), convo=convo)
        analyzer = SimpleNamespace(_budget_chars=lambda: 40000)
        agent = FeaturesAgent(arch, analyzer=analyzer)
        memory = agent._memory()
        chars = sum(len(m.get("content", "")) for m in memory)
        self.assertLessEqual(chars, agent.MEMORY_CHARS + 500)
        self.assertIn("CURRENT SOURCE", memory[0]["content"])


if __name__ == "__main__":
    unittest.main()
