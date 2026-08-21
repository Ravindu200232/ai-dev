"""The tuner used to see four typed words and nothing else."""
import json
import shutil
import tempfile
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ACTIONS = Path("server_modules/agent/feature_actions.py")
HANDLER = Path("server_modules/ui/http_handler.py")
STUDIO = Path("../../studio")


def server():
    import importlib
    return importlib.import_module("server")


class ItReadsWhatTheAppReallyIsTests(unittest.TestCase):
    def setUp(self):
        self.s = server()
        self.tmp = Path(tempfile.mkdtemp())
        self.old = self.s.PROD_DIR
        self.s.PROD_DIR = self.tmp
        self.proj = self.tmp / "demo"
        for rel in ("app/page.jsx", "app/projects/page.jsx",
                    "app/contact/page.jsx"):
            fp = self.proj / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text("export default function P(){return null}",
                          encoding="utf-8")

    def tearDown(self):
        self.s.PROD_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plan(self, data):
        fp = self.proj / ".agentforge" / "plan.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(data), encoding="utf-8")

    def test_it_lists_the_pages_that_exist(self):
        got = self.s.project_facts("demo", "/")
        self.assertEqual(got["routes"], ["/", "/contact", "/projects"])
        self.assertTrue(got["ok"])

    def test_an_app_with_no_accounts_has_no_auth(self):
        self._plan({"demo_accounts": []})
        self.assertFalse(self.s.project_facts("demo", "/")["has_auth"])

    def test_an_app_with_accounts_reports_its_roles(self):
        self._plan({"demo_accounts": [{"email": "a@x", "role": "admin"},
                                      {"email": "b@x", "role": "member"}]})
        got = self.s.project_facts("demo", "/")
        self.assertTrue(got["has_auth"])
        self.assertEqual(got["roles"], ["admin", "member"])

    def test_a_scaffolded_auth_file_counts_even_with_no_plan(self):
        (self.proj / "lib").mkdir(parents=True, exist_ok=True)
        (self.proj / "lib" / "auth.js").write_text("x", encoding="utf-8")
        self.assertTrue(self.s.project_facts("demo", "/")["has_auth"])

    def test_it_names_the_collections_the_plan_declared(self):
        self._plan({"phases": [{"files": [
            {"writes": ["projects"], "reads": ["skills"]}]}]})
        self.assertEqual(self.s.project_facts("demo", "/")["collections"],
                         ["projects", "skills"])

    def test_a_project_that_does_not_exist_says_nothing(self):
        got = self.s.project_facts("nope", "/")
        self.assertFalse(got["ok"])
        self.assertEqual(self.s._facts_block(got, "/"), "")

    def test_a_missing_project_name_is_survivable(self):
        self.assertFalse(self.s.project_facts("", "/")["ok"])


class TheFactsReachTheModelTests(unittest.TestCase):
    FACTS = {"ok": True, "routes": ["/", "/projects"], "has_auth": False,
             "roles": [], "collections": ["projects"], "page": "/"}

    def setUp(self):
        self.s = server()

    def test_no_sign_in_is_stated_as_a_fact_not_a_gap(self):
        block = self.s._facts_block(self.FACTS, "/")
        self.assertIn("Sign-in: NONE", block)
        self.assertIn("cannot be met by an edit", block)

    def test_a_route_that_is_not_served_is_flagged(self):
        block = self.s._facts_block({**self.FACTS, "page": ""}, "/admin")
        self.assertIn("not a page this app serves", block)

    def test_it_tells_the_tuner_to_name_what_is_missing(self):
        block = self.s._facts_block(self.FACTS, "/")
        self.assertIn("IF THE REQUEST NAMES SOMETHING THAT IS NOT ABOVE", block)
        self.assertIn("must be created as part of this change", block)

    def test_roles_are_listed_when_there_are_some(self):
        block = self.s._facts_block(
            {**self.FACTS, "has_auth": True, "roles": ["admin", "member"]}, "/")
        self.assertIn("roles are admin, member", block)


class TheRuleIsInThePromptTests(unittest.TestCase):
    def setUp(self):
        self.body = ACTIONS.read_text(encoding="utf-8")

    def test_the_tuner_is_told_to_check_the_request(self):
        self.assertIn("CHECK IT AGAINST THE APP", self.body)
        self.assertIn("which does not exist yet", self.body)

    def test_it_may_not_invent_a_route_or_a_role(self):
        self.assertIn("NEVER invent a route, a role, a collection", self.body)

    def test_the_facts_are_folded_into_the_ask(self):
        self.assertIn("+ _facts_block(project_facts(project, route), route)",
                      self.body)


class AllThreeToolsSendTheProjectTests(unittest.TestCase):
    def test_the_endpoint_forwards_it(self):
        self.assertIn('project=str(body.get("project") or "")',
                      HANDLER.read_text(encoding="utf-8"))

    def test_every_studio_caller_sends_it(self):
        import re
        if not STUDIO.is_dir():
            self.skipTest("the studio tree is not beside the backend here")
        calls = 0
        for rel in ("components/PreviewPane.jsx", "app/page.jsx"):
            body = (STUDIO / rel).read_text(encoding="utf-8")
            for m in re.finditer(r"api\.tune\(\{", body):
                chunk = body[m.start():m.start() + 260]
                chunk = chunk[:chunk.index("})")] if "})" in chunk else chunk
                self.assertIn("project", chunk, f"{rel}: {chunk[:70]}")
                calls += 1
        self.assertEqual(calls, 4)


if __name__ == "__main__":
    unittest.main()
