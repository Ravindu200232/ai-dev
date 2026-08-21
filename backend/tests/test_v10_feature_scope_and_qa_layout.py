import unittest
from pathlib import Path
from types import SimpleNamespace

import server
from agents.security import SecurityAgent as CompatSecurityAgent
from qa_agent.security import SecurityAgent
from qa_agent.api_verification import reprobe_affected_gets


class FeatureScopeTests(unittest.TestCase):
    def test_new_page_plus_shared_nav_does_not_expand_to_whole_app(self):
        arch = SimpleNamespace(files={
            "app/about/page.jsx": "import Navbar from '@/components/Navbar'",
            "app/page.jsx": "import Navbar from '@/components/Navbar'",
            "app/cart/page.jsx": "import Navbar from '@/components/Navbar'",
            "app/checkout/page.jsx": "import Navbar from '@/components/Navbar'",
            "components/Navbar.jsx": "export default function Navbar(){}",
        })
        pages, apis = server._feature_focus_scope(
            arch, {"app/about/page.jsx", "components/Navbar.jsx"},
            declared_routes=["/about"], route_hint="/shopping-cart")
        self.assertEqual(pages, ["/about"])
        self.assertEqual(apis, [])

    def test_component_only_change_uses_representative_pages(self):
        arch = SimpleNamespace(files={
            "app/page.jsx": "import Navbar from '@/components/Navbar'",
            "app/cart/page.jsx": "import Navbar from '@/components/Navbar'",
            "app/checkout/page.jsx": "import Navbar from '@/components/Navbar'",
            "components/Navbar.jsx": "export default function Navbar(){}",
        })
        pages, _ = server._feature_focus_scope(
            arch, {"components/Navbar.jsx"}, route_hint="/cart")
        self.assertEqual(pages, ["/", "/cart"])

    def test_changed_api_checks_only_it_and_its_calling_page(self):
        arch = SimpleNamespace(files={
            "app/shopping-cart/page.jsx": "fetch('/api/cart')",
            "app/catalogue/page.jsx": "fetch('/api/products')",
            "app/api/cart/route.js": "export async function GET(){}",
            "app/api/products/route.js": "export async function GET(){}",
        })
        pages, apis = server._feature_focus_scope(
            arch, {"app/api/cart/route.js"})
        self.assertEqual(pages, ["/shopping-cart"])
        self.assertEqual(apis, ["/api/cart"])


class QALayoutTests(unittest.TestCase):
    def test_security_agent_lives_under_qa_agent_with_compatibility_shim(self):
        self.assertIs(CompatSecurityAgent, SecurityAgent)
        self.assertTrue(Path("qa_agent/security.py").is_file())
        self.assertTrue(Path("qa_agent/api_verification.py").is_file())

    def test_api_reprobe_touches_only_affected_get_source(self):
        calls = []
        analyzer = SimpleNamespace(
            base_url="http://localhost:5173",
            _get_status=lambda url: calls.append(url) or 200,
        )
        report = SimpleNamespace(routes={
            "/api/cart": {"kind":"api", "dynamic":False,
                          "file":"app/api/cart/route.js", "methods":["GET"]},
            "/api/products": {"kind":"api", "dynamic":False,
                              "file":"app/api/products/route.js", "methods":["GET"]},
        }, findings=[])
        reprobe_affected_gets(
            analyzer, report, {"app/api/cart/route.js"},
            lambda *a, **k: SimpleNamespace(args=a, kwargs=k))
        self.assertEqual(calls, ["http://localhost:5173/api/cart"])


if __name__ == "__main__":
    unittest.main()
