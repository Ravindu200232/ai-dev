import types
import unittest

from agents.theme_contract import extract_theme_contract, theme_facts_markdown
from agents.themes import design_prompt
from qa_agent.debugger_rules import diagnose_page_health
from qa_agent.e2e_progress import MIN_REPAIR_ROUNDS, stop_after_no_progress


class E2EMinimumRoundTests(unittest.TestCase):
    def test_no_progress_stops_after_two_evidence_rounds(self):
        self.assertEqual(MIN_REPAIR_ROUNDS, 2)
        self.assertFalse(stop_after_no_progress(1, False))
        self.assertTrue(stop_after_no_progress(2, False))

    def test_progress_at_or_after_floor_keeps_journey_alive(self):
        self.assertFalse(stop_after_no_progress(2, True))
        self.assertFalse(stop_after_no_progress(7, True))
        self.assertTrue(stop_after_no_progress(7, False))

    def test_page_500_beats_selector_repair(self):
        arch = types.SimpleNamespace(files={
            "app/admin/rooms/page.jsx": "export default function Page(){}",
        })
        agent = types.SimpleNamespace(_page_for=lambda route: "app/admin/rooms/page.jsx")
        failure = types.SimpleNamespace(target="app/admin/rooms/page.jsx")
        packet = "HTTP 500 GET http://localhost:5173/admin/rooms — response=<html>error</html>"
        got = diagnose_page_health(agent, arch, [failure], packet)
        self.assertEqual(got["verdict"], "APP_FIX")
        self.assertEqual(got["files"], ["app/admin/rooms/page.jsx"])
        self.assertIn("before changing its E2E locator", got["root"])


class ApprovedHtmlThemeTests(unittest.TestCase):
    HTML = """<!doctype html><html><head><style>
    :root { --bg:#f7f0e6; --surface:#fffaf3; --ink:#241a14; --muted:#796b61; --accent:#9b4f2f; --line:#d7c7b8; }
    body { background:var(--bg); color:var(--ink); font-family:Georgia, serif; font-size:16px; line-height:1.65; }
    .shell { max-width:1180px; margin:0 auto; padding:24px 32px; }
    nav { height:72px; border-bottom:1px solid var(--line); background:var(--surface); }
    h1 { font-size:52px; font-weight:500; line-height:1.05; letter-spacing:-.02em; }
    .card { background:var(--surface); border:1px solid var(--line); border-radius:4px; box-shadow:6px 6px 0 #241a14; padding:24px; }
    button { background:var(--accent); color:#fffaf3; border-radius:999px; padding:12px 20px; }
    input { border:1px solid var(--line); border-radius:4px; padding:12px 14px; }
    </style></head><body><nav>Demo</nav><main class='shell'><h1>Hotel</h1><div class='card'>x</div></main></body></html>"""

    def test_contract_reads_exact_css_values(self):
        c = extract_theme_contract(self.HTML)
        self.assertEqual(c["variables"]["--accent"], "#9b4f2f")
        self.assertIn(("4px", 2), c["radii"])
        self.assertTrue(any(v == "1180px" for v, _n in c["widths"]))
        self.assertTrue(any("Georgia" in v for v, _n in c["fonts"]))
        self.assertTrue(any("6px 6px 0" in v for v, _n in c["shadows"]))

    def test_design_prompt_carries_authoritative_receipt(self):
        prompt = design_prompt(self.HTML)
        self.assertIn("CONTROLLER-EXTRACTED CSS FACTS", prompt)
        self.assertIn("--accent=#9b4f2f", prompt)
        self.assertIn("1180px", prompt)
        self.assertIn("6px 6px 0 #241a14", prompt)

    def test_receipt_is_builder_readable(self):
        md = theme_facts_markdown(self.HTML)
        self.assertIn("Approved HTML theme receipt", md)
        self.assertIn("Navigation CSS", md)
        self.assertIn("Buttons CSS", md)
        self.assertIn("Surfaces CSS", md)


if __name__ == "__main__":
    unittest.main()
