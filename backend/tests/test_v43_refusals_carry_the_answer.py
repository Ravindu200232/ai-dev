"""A gate that only says no teaches the model nothing."""
import unittest
from pathlib import Path

from qa_agent.e2e_grounding import E2EGroundingMixin
from qa_agent.e2e_locators import E2ELocatorsMixin

STAGE = Path("server_modules/qa/e2e_stage.py")

SOURCE = {
    "components/Hero.jsx": """
      <h1 className="text-6xl">I'm ravindu bandara</h1>
      <p>Full-stack developer</p>
      <button data-testid="home-download-cv">Download CV</button>
    """,
    "components/Work.jsx": '<h2>Selected Projects</h2><span>View all</span>',
    "lib/util.js": "export const x = 1",
}


def grounding(files=None):
    g = E2EGroundingMixin.__new__(E2EGroundingMixin)
    g.arch = type("A", (), {"files": files if files is not None else SOURCE})()
    return g


def locators(evidence):
    a = E2ELocatorsMixin.__new__(E2ELocatorsMixin)
    a._last_run_evidence = evidence
    return a


class TheRefusalNamesWhatIsThereTests(unittest.TestCase):
    def test_it_reads_the_copy_the_app_really_renders(self):
        got = grounding().app_copy()
        self.assertIn("Selected Projects", got)
        self.assertIn("Download CV", got)

    def test_it_skips_code_that_is_not_copy(self):
        got = grounding().app_copy()
        self.assertFalse([c for c in got if "className" in c or "export" in c])

    def test_the_hint_is_a_list_of_usable_assertions(self):
        hint = grounding()._copy_hint()
        self.assertIn("The app DOES render these", hint)
        self.assertIn("/Selected Projects/i", hint)

    def test_an_app_it_cannot_read_gets_no_invented_hint(self):
        self.assertEqual(grounding({})._copy_hint(), "")
        self.assertEqual(grounding({"lib/x.js": "const a = 1"})._copy_hint(), "")

    def test_the_refusal_itself_carries_it(self):
        body = Path("qa_agent/e2e_grounding.py").read_text(encoding="utf-8")
        self.assertEqual(body.count("+ self._copy_hint()"), 2)
        self.assertNotIn("Assert something \nthe app actually renders", body)


class TheModelsOwnPatchIsTrustedWhenObservedTests(unittest.TestCase):
    EVIDENCE = {"fields": [
        {"id": "name", "labels": ["Full Name"], "tag": "input", "type": "text"},
        {"id": "email", "labels": ["Email"], "tag": "input", "type": "email"},
    ]}

    def test_a_label_the_dom_showed_is_grounded(self):
        self.assertTrue(locators(self.EVIDENCE).patch_is_observed(
            "FILL :: role=textbox name=/full name/i :: Jane"))

    def test_an_id_the_dom_showed_is_grounded(self):
        self.assertTrue(locators(self.EVIDENCE).patch_is_observed(
            "FILL :: field=email :: a@x.com"))

    def test_something_the_dom_never_showed_is_not(self):
        for invented in ("FILL :: role=textbox name=/telephone/i :: 1",
                         "CLICK :: testid=submit-invented",
                         "CLICK :: role=button name=/checkout/i"):
            self.assertFalse(locators(self.EVIDENCE).patch_is_observed(invented),
                             invented)

    def test_a_drop_is_never_called_grounded(self):
        self.assertFalse(locators(self.EVIDENCE).patch_is_observed("DROP"))
        self.assertFalse(locators(self.EVIDENCE).patch_is_observed(""))

    def test_no_evidence_means_no_trust(self):
        self.assertFalse(locators({}).patch_is_observed(
            "FILL :: field=email :: a@x.com"))


class TheStageTakesItTests(unittest.TestCase):
    def setUp(self):
        self.body = STAGE.read_text(encoding="utf-8")

    def test_the_stage_checks_the_patch_before_discarding_it(self):
        self.assertIn("agent.patch_is_observed(own)", self.body)
        self.assertLess(self.body.index("patch_is_observed"),
                        self.body.index("named nothing the DOM"))

    def test_the_refusal_now_says_what_was_missing(self):
        self.assertIn("named nothing the DOM ", self.body)
        self.assertIn("exposed and there is no app-side defect", self.body)

    def test_an_ungrounded_patch_still_preserves_the_scenario(self):
        self.assertIn("failures = before", self.body)


if __name__ == "__main__":
    unittest.main()
