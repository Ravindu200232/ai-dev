"""v26 — the author gets the fields the app has, the way it gets the test ids.

Measured on a library build: the journey that registers a member failed on
`nothing matched field 'name'` against a register form that has no such
control, after every other gate had proved the app correct. The prompt already
said to choose only fields present in the evidence — and the evidence was
thousands of characters of JSX. A closed list is checkable; a source dump is
not, which is exactly why the test ids got a list.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace

from qa_agent.e2e import E2EAgent


REGISTER = """export default function Register(){ return <form>
  <input type="email" name="email" placeholder="you@example.com" />
  <input type="password" name="password" />
  <input type="password" name="confirmPassword" />
  <button type="submit">Sign Up</button>
</form> }"""

DOM = ("DOM /register\n"
       "fields: input type=email name=email id=email | "
       "input type=password name=password id=password\n"
       "controls: button Sign Up")


def agent_for(*, source=REGISTER, dom=DOM):
    arch = SimpleNamespace(project_dir=Path("."), files={}, plan={},
                           convo=[], plan_md="")
    a = E2EAgent(arch, Path("."))
    a.runtime_evidence = lambda journey=None, limit=4: dom
    a._journey_pages = lambda journey=None: []
    a.journey_source_bundle = lambda journey=None, page="", budget=0: (
        ("--- app/register/page.jsx ---\n" + source) if source else "")
    return a


JOURNEY = {"title": "Onboarding", "steps": ["register a member"]}


class FieldInventoryTests(unittest.TestCase):
    def test_the_fields_the_form_really_has(self):
        got = [f.split(" ")[0] for f in agent_for().fields_in_journey(JOURNEY)]
        self.assertEqual(["email", "password", "confirmPassword"], got)

    def test_the_field_that_broke_the_journey_is_not_offered(self):
        got = [f.split(" ")[0].lower()
               for f in agent_for().fields_in_journey(JOURNEY)]
        self.assertNotIn("name", got)

    def test_the_control_type_is_named_so_the_author_can_choose(self):
        got = agent_for().fields_in_journey(JOURNEY)
        self.assertIn("email (email)", got)
        self.assertIn("password (password)", got)

    def test_a_field_only_the_live_dom_knows_about_still_counts(self):
        a = agent_for(source="")
        got = [f.split(" ")[0] for f in a.fields_in_journey(JOURNEY)]
        self.assertEqual(["email", "password"], got)

    def test_an_app_with_no_form_offers_nothing(self):
        a = agent_for(source="export default () => <p>hello</p>", dom="DOM /\n")
        self.assertEqual([], a.fields_in_journey(JOURNEY))

    def test_a_key_field_css_cannot_bind_is_dropped(self):
        a = agent_for(source='<input name="" /><input name="a b c!" />', dom="")
        self.assertEqual([], a.fields_in_journey(JOURNEY))

    def test_one_control_contributes_one_key(self):
        a = agent_for(source='<input type="email" name="email" id="email" '
                             'placeholder="email" />', dom="")
        self.assertEqual(1, len(a.fields_in_journey(JOURNEY)))


class PromptTests(unittest.TestCase):
    def test_the_list_reaches_the_author_prompt(self):
        source = Path("qa_agent/e2e_journeys.py").read_text(encoding="utf-8")
        block = source.split("def author(")[1]
        self.assertIn("self.fields_in_journey(journey, page=page)", block)
        self.assertIn("FORM FIELDS THIS APP REALLY HAS", block)

    def test_a_missing_control_is_still_reported_as_an_app_defect(self):
        source = Path("qa_agent/e2e_journeys.py").read_text(encoding="utf-8")
        block = source.split("FORM FIELDS THIS APP REALLY HAS")[1][:600]
        self.assertIn("APP defect", block)
        self.assertIn("do not invent", block)


if __name__ == "__main__":
    unittest.main()
