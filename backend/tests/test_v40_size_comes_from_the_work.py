"""Nothing tells the planner how big to be. The jobs do."""
import re
import tempfile
import unittest
from pathlib import Path

from agents.architect import ArchitectAgent

PLANNERS = ["agents/architect_next_planner_prompt_a.py",
            "agents/architect_next_planner_prompt_b.py"]


def prompt_for(idea="a unit converter for length and weight"):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan, a.user_prompt = {}, idea
    a._vocab_cache = a._app_noun_cache = None
    a.stack, a.cb = "next", None
    a.project_dir = Path(tempfile.gettempdir())
    return a._planner_sys()


class TheCountIsDerivedTests(unittest.TestCase):
    def setUp(self):
        self.prompt = prompt_for()

    def test_the_file_count_is_named_as_an_output(self):
        self.assertIn("THE FILE COUNT IS AN OUTPUT, NOT AN INPUT", self.prompt)
        self.assertIn("Whatever\n    that comes to is the plan", self.prompt)

    def test_both_directions_of_planning_to_a_number_are_refused(self):
        self.assertIn("padding to look\n    thorough and trimming to look tidy "
                      "are the same mistake", self.prompt)

    def test_a_task_is_a_piece_of_work_not_a_slice_of_a_quota(self):
        self.assertIn("A TASK IS ONE COHERENT PIECE OF WORK", self.prompt)
        self.assertIn("Do not decide a\n    number first", self.prompt)
        self.assertIn("A tool with one screen has one\n    task", self.prompt)

    def test_the_one_surviving_limit_states_its_reason(self):
        self.assertIn("ONE LIMIT, AND IT IS MECHANICAL", self.prompt)
        self.assertIn("one task per turn", self.prompt)
        self.assertIn("they all come out thin", self.prompt)

    def test_seed_volume_follows_the_screen(self):
        self.assertIn("enough on it to look like the real thing", self.prompt)
        self.assertNotIn("three or four rows", self.prompt)


class CompleteIsNotBigTests(unittest.TestCase):
    def setUp(self):
        self.prompt = prompt_for()

    def test_completeness_is_defined_by_jobs_finishing(self):
        self.assertIn("COMPLETE IS ABOUT THE JOBS FINISHING, NOT ABOUT HOW "
                      "MUCH THERE IS", self.prompt)

    def test_half_an_app_is_named_concretely(self):
        for shape in ("a list with no way to add to it",
                      "a form\nthat saves nowhere",
                      "an admin area with one page in it"):
            self.assertIn(shape, self.prompt, shape)

    def test_a_job_runs_all_the_way_through(self):
        self.assertIn("the control that starts it", self.prompt)
        self.assertIn("the state the person\nsees afterwards", self.prompt)
        self.assertIn("when it is empty or goes wrong", self.prompt)

    def test_neither_padding_nor_stopping_short_is_allowed(self):
        self.assertIn("Never add a page to make a\nshort plan look more "
                      "serious", self.prompt)
        self.assertIn("never leave a job half-done", self.prompt)

    def test_a_small_app_may_be_finished(self):
        self.assertIn("A small app can be finished and a large one can be "
                      "half-built", self.prompt)


class NoImposedSizeSurvivesTests(unittest.TestCase):
    NUMBERS = re.compile(
        r"(?i)(at most \d+ files"
        r"|\d+ files is the ceiling"
        r"|total \d+[–-]\d+ files"
        r"|\d+ to \d+ tasks"
        r"|(?:two|three|four|five|six|seven) to (?:two|three|four|five|six|seven|nine) "
        r"(?:tasks|workflows|files|pages)"
        r"|at least (?:two|three|four|five|six|seven|\d+) (?:tasks|files|pages))")

    def test_no_planner_line_dictates_a_size(self):
        for rel in PLANNERS:
            body = Path(rel).read_text(encoding="utf-8")
            found = [m.group(0) for m in self.NUMBERS.finditer(body)]
            self.assertEqual(found, [], f"{rel} still dictates {found}")

    def test_the_rules_that_stop_under_building_survive(self):
        prompt = prompt_for()
        for rule in ("THE WHOLE APP, NOT THE INTERESTING HALF",
                     "AND NOT MORE THAN THE REQUEST",
                     "NO STUBS", "THE REQUEST DECIDES",
                     "DESIGN THE FLOW BEFORE YOU LIST THE SCREENS"):
            self.assertIn(rule, prompt, rule)


if __name__ == "__main__":
    unittest.main()
