"""Prose said "a small app"; the numbers demanded a big one. The numbers won."""
import re
import tempfile
import unittest
from pathlib import Path

from agents.architect import ArchitectAgent

PLANNER_B = Path("agents/architect_next_planner_prompt_b.py")


def prompt_for(idea="a unit converter for length and weight"):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan, a.user_prompt = {}, idea
    a._vocab_cache = a._app_noun_cache = None
    a.stack, a.cb = "next", None
    a.project_dir = Path(tempfile.gettempdir())
    return a._planner_sys()


class NoFloorForcesAnAppToGrowTests(unittest.TestCase):
    def setUp(self):
        self.prompt = prompt_for()

    def test_the_file_count_has_a_ceiling_and_no_floor(self):
        self.assertNotIn("Total 12–25 files", self.prompt)
        self.assertIn("THE FILE COUNT IS AN OUTPUT, NOT AN INPUT", self.prompt)

    def test_a_one_screen_tool_may_be_one_task(self):
        self.assertNotIn("4 to 7 tasks —", self.prompt)
        self.assertIn("A TASK IS ONE COHERENT PIECE OF WORK", self.prompt)
        self.assertIn("A tool with one screen has one\n    task", self.prompt)

    def test_no_number_is_decided_before_the_work_is(self):
        self.assertIn("Do not decide a\n    number first", self.prompt)

    def test_an_app_that_stores_nothing_plans_no_database(self):
        self.assertIn("WHEN THE APP STORES ANYTHING", self.prompt)
        self.assertIn("plans neither", self.prompt)

    def test_a_one_page_app_needs_no_navbar_or_loading_file(self):
        self.assertIn("a one-page app needs no navigation", self.prompt)

    def test_the_three_section_floor_is_for_record_screens(self):
        self.assertIn("that\nlists, filters or manages records", self.prompt)

    def test_one_job_may_have_one_workflow(self):
        self.assertNotIn("TWO TO FOUR workflows", self.prompt)
        self.assertIn("one is right for a one-job app", self.prompt)

    def test_a_screen_with_no_list_seeds_nothing(self):
        self.assertIn("screen that shows no list seeds nothing", self.prompt)


class TheCeilingsSurviveTests(unittest.TestCase):
    """Removing floors must not remove the limits that keep tasks thin."""

    def setUp(self):
        self.prompt = prompt_for()

    def test_a_task_still_cannot_hold_eight_files(self):
        self.assertIn("ONE LIMIT, AND IT IS MECHANICAL", self.prompt)
        self.assertIn("they all come out thin", self.prompt)

    def test_that_limit_is_a_reason_rather_than_a_figure(self):
        self.assertIn("the build writes one task per turn", self.prompt)
        self.assertIn("split the task, never the file", self.prompt)

    def test_a_real_app_still_gets_its_seed_and_api(self):
        self.assertIn("`lib/seed.js` and\n    at least one", self.prompt)

    def test_nothing_asked_for_may_still_be_dropped(self):
        for rule in ("THE WHOLE APP, NOT THE INTERESTING HALF",
                     "AND NOT MORE THAN THE REQUEST", "NO STUBS",
                     "THE REQUEST DECIDES"):
            self.assertIn(rule, self.prompt, rule)


class NoNumberOutranksTheRequestTests(unittest.TestCase):
    def test_no_lower_bound_on_size_survives_anywhere(self):
        body = PLANNER_B.read_text(encoding="utf-8")
        forbidden = re.compile(
            r"(?i)(at least (?:four|five|six|seven|eight|nine|ten|\d+) "
            r"(?:pages|files|routes|screens|tasks)"
            r"|minimum of \d+ (?:pages|files|tasks)"
            r"|Total \d+[–-]\d+ files)")
        found = [m.group(0) for m in forbidden.finditer(body)]
        self.assertEqual(found, [], f"still forces a size: {found}")

    def test_the_seven_route_figure_stays_tied_to_the_request(self):
        body = Path("agents/architect_next_planner_prompt_a.py").read_text(
            encoding="utf-8")
        window = body[body.index("GIVE EACH ROLE ITS OWN SECTION"):
                      body.index("at least seven routes")]
        self.assertIn("If the request", window)


if __name__ == "__main__":
    unittest.main()
