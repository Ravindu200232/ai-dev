"""Two checks that judged an app they had not looked at."""
import unittest
from pathlib import Path

from agents.analyzer_data import AnalyzerDataMixin
from agents.features_common import FeatureSpec
from agents.features_planning import FeaturesAgentPlanningMixin

DATA = Path("agents/analyzer_data.py")
PLANNING = Path("agents/features_planning.py")

CONTACT_FORM = """
export default function ContactForm() {
  const [email, setEmail] = useState('hello@example.com')
  return <input value={email} onChange={e => setEmail(e.target.value)} />
}
"""


def creds(accounts, files=None):
    a = AnalyzerDataMixin.__new__(AnalyzerDataMixin)
    a.demo_credentials = lambda: accounts
    a.code_files = lambda: files or {"components/ContactForm.jsx": CONTACT_FORM}
    return [f.code for f in a.credentials_exposed()]


class ALeakNeedsSomethingToLeakTests(unittest.TestCase):
    def test_an_app_with_no_accounts_cannot_leak_one(self):
        self.assertEqual(creds([]), [])

    def test_a_default_that_is_not_a_seeded_address_is_the_apps_own_copy(self):
        self.assertEqual(creds([("admin@demo.com", "password123")]), [])

    def test_the_seeded_address_is_still_caught(self):
        self.assertEqual(creds([("hello@example.com", "password123")]),
                         ["CREDS_IN_UI"])

    def test_a_seeded_password_in_the_markup_is_still_caught(self):
        files = {"app/login/page.jsx": "const p = 'password123'"}
        self.assertEqual(creds([("a@x.com", "password123")], files),
                         ["CREDS_IN_UI"])

    def test_a_demo_accounts_panel_is_still_caught(self):
        files = {"app/page.jsx": "<div>Demo Accounts: guest@demo.com</div>"}
        self.assertEqual(creds([("guest@demo.com", "pw")], files),
                         ["CREDS_IN_UI"])

    def test_the_reason_names_the_seeded_account(self):
        a = AnalyzerDataMixin.__new__(AnalyzerDataMixin)
        a.demo_credentials = lambda: [("hello@example.com", "pw")]
        a.code_files = lambda: {"components/ContactForm.jsx": CONTACT_FORM}
        self.assertIn("seeded", a.credentials_exposed()[0].message)


FILES = {"app/page.jsx": "export default function P(){return null}"}
FULL = {"current": "/admin 404s", "gap": "the page is not there",
        "cause": "app/admin/page.jsx was never written",
        "verify": "the page renders", "confidence": "high"}


def issues(files, ctx=None):
    a = FeaturesAgentPlanningMixin.__new__(FeaturesAgentPlanningMixin)
    a.arch = type("A", (), {"files": FILES})()
    spec = FeatureSpec(files=files)
    spec.context = ctx if ctx is not None else FULL
    return a._analysis_issues(spec, "repair", None)


class AFileThatDoesNotExistHasNoSourceToQuoteTests(unittest.TestCase):
    def test_creating_a_missing_page_needs_no_evidence(self):
        self.assertEqual(issues([{"path": "app/admin/page.jsx",
                                  "action": "create"}]), [])

    def test_a_path_not_in_the_project_counts_as_creating(self):
        self.assertEqual(issues([{"path": "app/admin/page.jsx",
                                  "action": "edit"}]), [])

    def test_editing_a_real_file_still_needs_evidence(self):
        got = issues([{"path": "app/page.jsx", "action": "edit"}])
        self.assertIn("no source EVIDENCE was cited", got)
        self.assertIn("planned edit(s) not yet evidenced: app/page.jsx", got)

    def test_a_plan_with_no_files_still_needs_evidence(self):
        self.assertIn("no source EVIDENCE was cited", issues([]))

    def test_the_other_protocol_gaps_are_untouched(self):
        got = issues([{"path": "app/admin/page.jsx", "action": "create"}],
                     ctx={"confidence": "high"})
        for key in ("CURRENT", "GAP", "CAUSE", "VERIFY"):
            self.assertIn(f"missing {key} analysis", got)

    def test_low_confidence_is_still_a_gap(self):
        got = issues([{"path": "app/admin/page.jsx", "action": "create"}],
                     ctx={**FULL, "confidence": "low"})
        self.assertIn("analysis confidence is low", got)


class TheReasoningIsWrittenDownTests(unittest.TestCase):
    def test_the_creds_check_says_why_it_needs_an_account(self):
        body = DATA.read_text(encoding="utf-8")
        self.assertIn("Every check here needs an account to leak", body)
        self.assertIn("if not creds:\n            return out", body)

    def test_the_evidence_rule_says_why_a_new_file_is_exempt(self):
        body = PLANNING.read_text(encoding="utf-8")
        self.assertIn("A page that does not exist has no source to quote",
                      body)
        self.assertIn("if not evidence_paths and not creating:", body)


if __name__ == "__main__":
    unittest.main()
