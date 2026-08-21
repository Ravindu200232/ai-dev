"""v25 — the questions no journey asks.

Every gate checked whether the app does what it promises. None asked what it
does with a wrong password or a link to a row that is gone, which are the two
answers a real user finds first. Both run inside the browser context
`global_integrity` already opened, so they cost navigations, not journeys.
"""
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent


ACCOUNTS = [{"email": "member@demo.com", "password": "password123",
             "role": "member"}]
ROUTES = {
    "/": {"kind": "page", "file": "app/page.jsx", "dynamic": False},
    "/books": {"kind": "page", "file": "app/books/page.jsx", "dynamic": False},
    "/books/[id]": {"kind": "page", "file": "app/books/[id]/page.jsx",
                    "dynamic": True},
    "/loans/[loanId]": {"kind": "page", "file": "app/loans/[loanId]/page.jsx",
                        "dynamic": True},
    "/api/books": {"kind": "api", "file": "app/api/books/route.js",
                   "dynamic": False},
}


class AZ:
    def enumerate_routes(self):
        return ROUTES


class FakePage:
    """Just enough Playwright to answer the two probes."""

    def __init__(self, *, sign_in_status=401, statuses=None, session=False):
        self.sign_in_status = sign_in_status
        self.statuses = statuses or {}
        self.session = session
        self.visited = []
        self.cleared = 0
        self.context = SimpleNamespace(clear_cookies=self._clear)

    def _clear(self):
        self.cleared += 1

    def evaluate(self, js, arg=None):
        return self.sign_in_status

    def goto(self, url, **kw):
        self.visited.append(url)
        path = url.split("http://localhost:5173", 1)[-1]
        return SimpleNamespace(status=self.statuses.get(path, 200))


def agent_for(**kw):
    arch = SimpleNamespace(project_dir=Path("."), files={},
                           plan={"demo_accounts": ACCOUNTS})
    a = E2EAgent(arch, Path("."), analyzer=AZ())
    a._session_alive = lambda page, timeout_ms=0: kw.get("session", False)
    return a


class CredentialTests(unittest.TestCase):
    def test_a_refused_password_is_the_expected_answer(self):
        a = agent_for()
        self.assertEqual([], a.credential_integrity(FakePage(sign_in_status=401)))

    def test_a_wrong_password_that_is_accepted_is_reported(self):
        a = agent_for()
        found = a.credential_integrity(FakePage(sign_in_status=200))
        self.assertEqual(2, len(found))
        self.assertIn("a wrong password", found[0][0])
        self.assertIn("must refuse", found[0][1])

    def test_a_refusal_that_still_leaves_a_session_is_reported(self):
        a = agent_for(session=True)
        found = a.credential_integrity(FakePage(sign_in_status=401))
        self.assertEqual(2, len(found))
        self.assertIn("session exists afterwards", found[0][1])

    def test_an_app_with_no_demo_accounts_is_not_probed(self):
        arch = SimpleNamespace(project_dir=Path("."), files={}, plan={})
        a = E2EAgent(arch, Path("."), analyzer=AZ())
        page = FakePage(sign_in_status=200)
        self.assertEqual([], a.credential_integrity(page))
        self.assertEqual(0, page.cleared)

    def test_the_probe_leaves_no_session_behind(self):
        a = agent_for()
        page = FakePage(sign_in_status=401)
        a.credential_integrity(page)
        self.assertGreaterEqual(page.cleared, 3)


class AbsentRecordTests(unittest.TestCase):
    def test_every_dynamic_route_is_opened_with_an_id_that_cannot_exist(self):
        a = agent_for()
        page = FakePage()
        a.absent_record_integrity(page)
        self.assertEqual(
            ["http://localhost:5173/books/" + a.ABSENT_ID,
             "http://localhost:5173/loans/" + a.ABSENT_ID],
            page.visited)

    def test_a_not_found_page_is_a_correct_answer(self):
        a = agent_for()
        page = FakePage(statuses={"/books/" + E2EAgent.ABSENT_ID: 404})
        self.assertEqual([], a.absent_record_integrity(page))

    def test_a_five_hundred_is_not(self):
        a = agent_for()
        page = FakePage(statuses={"/books/" + E2EAgent.ABSENT_ID: 500})
        found = a.absent_record_integrity(page)
        self.assertEqual(1, len(found))
        self.assertEqual("/books/[id]", found[0][0])
        self.assertIn("HTTP 500", found[0][1])

    def test_an_app_with_no_detail_pages_is_not_probed(self):
        arch = SimpleNamespace(project_dir=Path("."), files={},
                               plan={"demo_accounts": ACCOUNTS})

        class Flat:
            def enumerate_routes(self):
                return {"/": {"kind": "page", "file": "app/page.jsx",
                              "dynamic": False}}

        a = E2EAgent(arch, Path("."), analyzer=Flat())
        page = FakePage()
        self.assertEqual([], a.absent_record_integrity(page))
        self.assertEqual([], page.visited)

    def test_the_id_cannot_be_a_seeded_row(self):
        self.assertTrue(re.fullmatch(r"0{24}", E2EAgent.ABSENT_ID))


class WiringTests(unittest.TestCase):
    def test_global_integrity_runs_both_checks(self):
        body = Path("qa_agent/e2e_evidence.py").read_text(encoding="utf-8")
        block = body.split("def global_integrity")[1].split("\n    def ")[0]
        self.assertIn("absent_record_integrity(page)", block)
        self.assertIn("credential_integrity(page)", block)

    def test_the_credential_probe_runs_last_because_it_clears_cookies(self):
        body = Path("qa_agent/e2e_evidence.py").read_text(encoding="utf-8")
        block = body.split("def global_integrity")[1].split("\n    def ")[0]
        self.assertLess(block.index("role_separation(page)"),
                        block.index("absent_record_integrity(page)"))
        self.assertLess(block.index("absent_record_integrity(page)"),
                        block.index("credential_integrity(page)"))


if __name__ == "__main__":
    unittest.main()
