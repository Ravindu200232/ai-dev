"""The checkout that navigated instead of saving, and the round that decided nothing."""
import unittest
from pathlib import Path

from qa_agent.debugger_rules import diagnose_native_form_submit


class Arch:
    files = {
        "app/checkout/page.jsx": "<form>{guestName}{method}<button/></form>",
        "components/Navbar.jsx": "<nav/>",
    }


class Failure:
    def __init__(self, message):
        self.message = message
        self.kind = "ASSERTION"


# The exact shape the real run produced.
PACKET = """
HTTP 200 GET http://localhost:5173/checkout?roomId=6a87ec4d&start=2025-12-01&end=2025-12-05
HTTP 200 GET http://localhost:5173/checkout?guestName=Luxury+Traveler&method=Credit+Card
HTTP 200 GET http://localhost:5173/api/auth/get-session
"""
STAYED = Failure("the url stayed at http://localhost:5173/checkout"
                 "?guestName=Luxury+Traveler&method=Credit+Card, expected text /")


class NativeFormSubmitTests(unittest.TestCase):
    def test_the_real_checkout_failure_now_has_a_root_cause(self):
        decision = diagnose_native_form_submit(None, Arch(), [STAYED], PACKET)
        self.assertIsNotNone(decision)
        self.assertEqual(decision["verdict"], "APP_FIX")
        self.assertEqual(decision["evidence_rule"], "native-form-get-submit")

    def test_it_names_the_fields_and_the_page_that_owns_them(self):
        decision = diagnose_native_form_submit(None, Arch(), [STAYED], PACKET)
        self.assertIn("guestname", decision["root"])
        self.assertIn("method", decision["root"])
        self.assertIn("app/checkout/page.jsx", decision["files"])

    def test_it_points_at_the_missing_preventdefault(self):
        decision = diagnose_native_form_submit(None, Arch(), [STAYED], PACKET)
        self.assertIn("preventDefault", decision["hypothesis"])

    def test_a_form_that_really_saved_is_not_accused(self):
        wrote = PACKET + "\nHTTP 201 POST http://localhost:5173/api/bookings"
        self.assertIsNone(diagnose_native_form_submit(None, Arch(), [STAYED], wrote))

    def test_ordinary_query_parameters_are_not_a_form_submit(self):
        for query in ("?page=2&sort=price", "?tab=upcoming", "?q=deluxe&limit=20"):
            packet = f"HTTP 200 GET http://localhost:5173/rooms{query}"
            self.assertIsNone(
                diagnose_native_form_submit(None, Arch(), [], packet), query)

    def test_a_clean_run_says_nothing(self):
        self.assertIsNone(diagnose_native_form_submit(
            None, Arch(), [], "HTTP 200 GET http://localhost:5173/rooms"))

    def test_it_runs_before_the_vaguer_checks(self):
        text = Path("qa_agent/debugger_investigate.py").read_text(encoding="utf-8")
        self.assertLess(text.index("diagnose_native_form_submit"),
                        text.index("diagnose_broken_images"))


class UndecidedRoundTests(unittest.TestCase):
    """`UNKNOWN: no root text` is the debugger failing, not the app passing."""

    def test_an_undecided_round_does_not_count_as_no_progress(self):
        text = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        undecided = text.index("undecided = (str(diag.get(\"verdict\")")
        counted = text.index("no_progress += 1", undecided)
        handed_back = text.index("round_budget += 1", undecided)
        self.assertLess(handed_back, counted)

    def test_it_says_whose_fault_the_round_was(self):
        text = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        self.assertIn("that is the debugger, not the app", text)

    def test_it_still_stops_at_the_hard_cap(self):
        text = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        self.assertIn("if undecided and rnd < E2E_HARD_FIX:", text)
        self.assertIn("if round_budget < E2E_HARD_FIX:", text)


if __name__ == "__main__":
    unittest.main()
