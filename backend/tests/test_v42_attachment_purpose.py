"""A picture the model can only describe is a picture with no job."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from agents.architect import ArchitectAgent

PROJECTS = Path("srs-agent/srs_agent/app/routers/projects.py")
SCHEMA = Path("srs-agent/srs_agent/app/schemas/project.py")


def build_brief():
    spec = importlib.util.spec_from_file_location(
        "brief_only", "srs-agent/srs_agent/app/extraction/brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_brief


class TheBriefCarriesTheJobTests(unittest.TestCase):
    def setUp(self):
        self.build = build_brief()
        self.sources = [
            {"mode": "image", "filename": "logo.png",
             "text": "A blue circular emblem.",
             "meta": {"purpose": "our logo, put it in the navbar",
                      "url": "/uploads/logo.png"}},
            {"mode": "image", "filename": "layout.jpg",
             "text": "A hand-drawn wireframe.",
             "meta": {"purpose": "the layout I want for the list page"}},
        ]

    def test_the_purpose_reaches_the_planner(self):
        brief = self.build("a clinic app", self.sources)
        self.assertIn("The person says this is for: our logo", brief)
        self.assertIn("the layout I want for the list page", brief)

    def test_what_it_is_for_comes_before_what_it_looks_like(self):
        brief = self.build("", self.sources)
        self.assertLess(brief.index("our logo"), brief.index("blue circular"))

    def test_a_saved_picture_names_its_real_path(self):
        brief = self.build("", self.sources)
        self.assertIn("served at `/uploads/logo.png`", brief)
        self.assertIn("do not ask for it to be generated", brief)

    def test_a_purpose_alone_is_enough_to_keep_the_source(self):
        brief = self.build("", [{"mode": "image", "filename": "x.png", "text": "",
                                 "meta": {"purpose": "the hero photo"}}])
        self.assertIn("the hero photo", brief)

    def test_a_source_with_nothing_at_all_is_still_dropped(self):
        self.assertEqual(self.build("", [{"mode": "image", "text": "", "meta": {}}]), "")

    def test_an_old_source_without_meta_still_works(self):
        brief = self.build("idea", [{"mode": "pdf", "filename": "spec.pdf",
                                     "text": "FR-001 the system shall…"}])
        self.assertIn("FR-001", brief)


class TheUploadKeepsTheFileTests(unittest.TestCase):
    def setUp(self):
        self.body = PROJECTS.read_text(encoding="utf-8")

    def test_the_request_may_carry_a_purpose(self):
        self.assertIn("purpose: Optional[str] = None",
                      SCHEMA.read_text(encoding="utf-8"))
        self.assertIn("purpose: Optional[str] = Form(None)", self.body)

    def test_a_picture_is_written_where_the_app_can_serve_it(self):
        self.assertIn('"public" / "uploads"', self.body)
        self.assertIn('public_url = f"/uploads/{safe}"', self.body)

    def test_only_pictures_get_a_public_copy(self):
        self.assertIn("PUBLIC_IMAGE_EXT", self.body)
        self.assertIn('ctype.startswith("image/")', self.body)

    def test_both_facts_are_recorded_on_the_source(self):
        self.assertIn('meta["url"] = public_url', self.body)
        self.assertIn('meta["purpose"] = purpose', self.body)

    def test_the_file_name_cannot_escape_the_folder(self):
        self.assertIn("def _public_name", self.body)
        self.assertIn('re.sub(r"[^A-Za-z0-9._-]+"', self.body)


class ThePlannerIsToldToObeyItTests(unittest.TestCase):
    def test_the_stated_purpose_outranks_the_description(self):
        a = ArchitectAgent.__new__(ArchitectAgent)
        a.plan, a.user_prompt = {}, "a clinic app"
        a._vocab_cache = a._app_noun_cache = None
        a.stack, a.cb = "next", None
        a.project_dir = Path(tempfile.gettempdir())
        prompt = a._planner_sys()
        self.assertIn("A FILE SOMEBODY ATTACHED HAS A JOB", prompt)
        self.assertIn("outranks the description underneath it", prompt)
        self.assertIn("no `## Images` entry asks for it", prompt)


if __name__ == "__main__":
    unittest.main()
