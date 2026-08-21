"""The planner designs a flow for the request, at the size of the request."""
import tempfile
import unittest
from pathlib import Path

from agents.architect import ArchitectAgent

PLANNER_A = Path("agents/architect_next_planner_prompt_a.py")


def prompt_for(idea="a unit converter for length and weight"):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan, a.user_prompt = {}, idea
    a._vocab_cache = a._app_noun_cache = None
    a.stack, a.cb = "next", None
    a.project_dir = Path(tempfile.gettempdir())
    return a._planner_sys()


class DesignComesFirstTests(unittest.TestCase):
    def setUp(self):
        self.prompt = prompt_for()

    def test_the_flow_is_designed_before_the_screens_are_listed(self):
        self.assertIn("DESIGN THE FLOW BEFORE YOU LIST THE SCREENS", self.prompt)
        self.assertLess(self.prompt.index("DESIGN THE FLOW"),
                        self.prompt.index("## Page Flow"))

    def test_scope_is_settled_before_design_begins(self):
        self.assertLess(self.prompt.index("THE REQUEST DECIDES"),
                        self.prompt.index("DESIGN THE FLOW"))

    def test_the_size_is_counted_in_jobs_not_features(self):
        self.assertIn("COUNT THE JOBS, NOT THE FEATURES", self.prompt)
        self.assertIn("has not understood the request, it has decorated it",
                      self.prompt)

    def test_a_screen_has_to_earn_its_place(self):
        self.assertIn("A SCREEN EXISTS BECAUSE SOMETHING WILL NOT FIT",
                      self.prompt)
        self.assertIn("it belongs on the page that led to", self.prompt)

    def test_dead_ends_are_called_defects(self):
        self.assertIn("EVERY SCREEN ANSWERS THREE QUESTIONS", self.prompt)
        self.assertIn("and a dead end is a defect", self.prompt)

    def test_the_landing_page_shows_the_app_not_a_banner(self):
        self.assertIn("THE FIRST SCREEN SHOWS THE THING THE APP IS FOR",
                      self.prompt)
        self.assertIn("`/` converts units", self.prompt)

    def test_an_action_has_to_show_that_it_worked(self):
        self.assertIn("THEY SEE THAT IT HAPPENED", self.prompt)
        self.assertIn("submits into\n    silence", self.prompt)

    def test_the_request_supplies_the_words_on_the_screen(self):
        self.assertIn("THE REQUEST'S WORDS ARE THE LABELS", self.prompt)
        self.assertIn("Inventory Management", self.prompt)


class DesignIsNotALicenceToAddTests(unittest.TestCase):
    def setUp(self):
        self.prompt = prompt_for()

    def test_it_says_so_in_the_same_breath(self):
        self.assertIn("design INSIDE the request, never a reason to add",
                      self.prompt)
        self.assertIn("The good\ndesign of a small app is a small app.",
                      self.prompt)

    def test_a_screen_no_job_needed_is_removed(self):
        self.assertIn("If a screen appears here that no\n"
                      "                           job needed, remove the screen.",
                      self.prompt)

    def test_the_scope_rules_from_before_are_still_there(self):
        for rule in ("Build nothing it did not ask for",
                     "A one-screen converter is a one-screen",
                     "delete it"):
            self.assertIn(rule, self.prompt, rule)

    def test_nothing_dropped_is_still_a_failure_too(self):
        self.assertIn("THE WHOLE APP, NOT THE INTERESTING HALF", self.prompt)
        self.assertIn("AND NOT MORE THAN THE REQUEST", self.prompt)


class TheMinimumsStayConditionalTests(unittest.TestCase):
    """A floor may only apply when the request asked for the thing."""

    def test_the_seven_route_figure_is_tied_to_what_the_request_names(self):
        body = PLANNER_A.read_text(encoding="utf-8")
        window = body[body.index("GIVE EACH ROLE ITS OWN SECTION"):
                      body.index("at least seven routes")]
        self.assertIn("If the request", window)

    def test_no_unconditional_page_count_is_demanded(self):
        prompt = prompt_for()
        for demanded in ("at least five pages", "minimum of four screens",
                         "every app needs a dashboard"):
            self.assertNotIn(demanded, prompt, demanded)


class SlotsCannotEatJsxTests(unittest.TestCase):
    """A `{children}` slot silently replaced React's own children prop."""

    FILES = ["agents/architect_next_planner_prompt_a.py",
             "agents/architect_next_planner_prompt_b.py",
             "agents/architect_next_builder_prompt_a.py",
             "agents/architect_next_builder_prompt_b.py",
             "agents/architect_stack_rules.py"]

    def test_no_prompt_uses_brace_slots_any_more(self):
        import re
        brace = re.compile(r"\{(thing|things|child|children|actor)\}")
        for rel in self.FILES:
            body = Path(rel).read_text(encoding="utf-8")
            for m in brace.finditer(body):
                if m.group(1) == "children":
                    continue          # the real JSX prop, which must survive
                self.fail(f"{rel} still uses {m.group(0)}")

    def test_the_react_children_prop_is_left_alone(self):
        for rel in self.FILES:
            body = Path(rel).read_text(encoding="utf-8")
            if "{children}" in body:
                return
        self.fail("the JSX children prop was renamed away")

    def test_the_layout_rule_still_names_the_children_prop(self):
        body = Path("agents/architect_next_planner_prompt_b.py").read_text(
            encoding="utf-8")
        self.assertIn("`<body>` and `{children}`", body)

    def test_every_slot_is_filled(self):
        import re
        prompt = prompt_for()
        self.assertEqual(re.findall(r"<<\w+>>", prompt), [])


if __name__ == "__main__":
    unittest.main()
