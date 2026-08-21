"""The plan is what was asked for — no less, and no more."""
import tempfile
import unittest
from pathlib import Path

from agents.architect import ArchitectAgent

PLANNER_A = Path("agents/architect_next_planner_prompt_a.py")
PLANNER_B = Path("agents/architect_next_planner_prompt_b.py")


def prompt_for(idea="a unit converter for length and weight"):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan, a.user_prompt = {}, idea
    a._vocab_cache = a._app_noun_cache = None
    a.stack, a.cb = "next", None
    a.project_dir = Path(tempfile.gettempdir())
    return a._planner_sys()


class TheRequestIsTheCeilingTests(unittest.TestCase):
    def setUp(self):
        self.prompt = prompt_for()

    def test_the_rule_comes_before_every_section_that_could_inflate_it(self):
        self.assertIn("THE REQUEST DECIDES", self.prompt)
        self.assertLess(self.prompt.index("THE REQUEST DECIDES"),
                        self.prompt.index("## Core Features"))

    def test_no_kind_of_app_is_forbidden_up_front(self):
        for banned in ("not a landing page", "not a demo stub",
                       "REAL, multi-screen"):
            self.assertNotIn(banned, self.prompt, banned)

    def test_it_does_not_ask_for_ambition(self):
        self.assertNotIn("ambitious", self.prompt)

    def test_a_feature_with_no_source_is_deleted_not_kept(self):
        self.assertIn("cannot point at the words in the", self.prompt)
        self.assertIn("delete it", self.prompt)

    def test_the_common_extras_are_named_so_they_are_recognisable(self):
        head = self.prompt[:self.prompt.index("OUTPUT FORMAT")]
        for extra in ("accounts", "admin area", "payments", "reviews",
                      "notifications"):
            self.assertIn(extra, head, extra)

    def test_the_size_of_the_request_is_the_size_of_the_plan(self):
        self.assertIn("A one-screen converter is a one-screen", self.prompt)

    def test_silence_is_answered_with_the_smallest_thing_that_works(self):
        self.assertIn("choose the smallest thing that works", self.prompt)


class TheRequestIsStillTheFloorTests(unittest.TestCase):
    """Guarding against over-build must not start an under-build."""

    def setUp(self):
        self.prompt = prompt_for()

    def test_nothing_may_be_left_for_a_later_phase(self):
        self.assertIn("There is no later", self.prompt)
        self.assertIn("THE WHOLE APP, NOT THE INTERESTING HALF", self.prompt)

    def test_both_directions_are_counted(self):
        body = PLANNER_B.read_text(encoding="utf-8")
        self.assertIn("AND NOT MORE THAN THE REQUEST", body)
        self.assertIn("fewer pages than the request has screens is unfinished",
                      body)
        self.assertIn("pages\n    the request never named is a different app", body)

    def test_the_counterweight_sits_beside_the_rule_it_balances(self):
        body = PLANNER_B.read_text(encoding="utf-8")
        self.assertLess(body.index("THE WHOLE APP, NOT THE INTERESTING HALF"),
                        body.index("AND NOT MORE THAN THE REQUEST"))

    def test_the_no_stubs_rule_survived(self):
        self.assertIn("NO STUBS", self.prompt)


class TheUserWordsStayAuthoritativeTests(unittest.TestCase):
    def test_the_planner_message_still_leads_with_the_request(self):
        body = Path("agents/architect_planning.py").read_text(encoding="utf-8")
        self.assertIn("AUTHORITATIVE PRODUCT REQUIREMENTS", body)
        self.assertIn("these and only these define", body)

    def test_build_context_is_never_turned_into_features(self):
        body = Path("agents/architect_planning.py").read_text(encoding="utf-8")
        self.assertIn("DO NOT turn them into Core Features", body)

    def test_the_action_clauses_of_the_request_are_still_extracted(self):
        got = ArchitectAgent._source_requirements(
            "customers browse books and place orders, staff edit stock")
        self.assertTrue(got)


if __name__ == "__main__":
    unittest.main()
