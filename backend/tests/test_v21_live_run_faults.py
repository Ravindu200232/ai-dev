"""v21 — three faults read straight off one live run.

The repair rewrote a page into `await getCollection('bookings').find(...)` and
the next journey met a 500; the reseed announced the fixtures were back while
the demo users were not, and the journey's own sign-in came back 401; and the
change planner read its own format keyword as a dependency and opened with
`npm install FILE`.
"""
import importlib
import re
import unittest
from types import SimpleNamespace

from qa_agent.e2e_invariants import validate_repair_invariants


MONGODB = "export async function getCollection(name){ return db.collection(name) }"


class PromiseUsedAsValueTests(unittest.TestCase):
    """An async helper's promise is never read as if it were the value."""

    def _check(self, after_body, before_body="const rows = []"):
        before = {"lib/mongodb.js": MONGODB,
                  "app/my-bookings/page.jsx": before_body}
        after = dict(before)
        after["app/my-bookings/page.jsx"] = after_body
        return validate_repair_invariants(
            before, after, ["app/my-bookings/page.jsx"], "APP_FIX", {})

    def test_the_exact_regression_from_the_run_is_rejected(self):
        problems = self._check(
            "const bookings = await getCollection('bookings')\n"
            "  .find({ user: user._id })\n"
            "  .sort({ createdAt: -1 })\n"
            "  .toArray()")
        self.assertEqual(1, len(problems))
        self.assertIn("getCollection(...).find", problems[0])
        self.assertIn("promise", problems[0])

    def test_awaiting_the_call_first_is_accepted(self):
        self.assertEqual([], self._check(
            "const c = await getCollection('bookings')\n"
            "const bookings = await c.find({}).sort({ createdAt: -1 }).toArray()"))

    def test_parenthesised_await_is_accepted(self):
        self.assertEqual([], self._check(
            "const bookings = await (await getCollection('bookings')).find({}).toArray()"))

    def test_then_is_a_legitimate_use_of_a_promise(self):
        self.assertEqual([], self._check(
            "getCollection('bookings').then(c => c.find({}))"))

    def test_a_method_on_something_else_is_not_ours(self):
        self.assertEqual([], self._check(
            "const rows = db.getCollection('bookings').find({})"))

    def test_a_fault_the_repair_did_not_introduce_is_not_blamed_on_it(self):
        # Already broken before the patch: the guard reports what this repair
        # changed, not what it inherited.
        broken = ("const bookings = await getCollection('bookings')"
                  ".find({}).toArray()")
        self.assertEqual([], self._check(broken + "\nconst x = 1", broken))

    def test_a_sync_helper_is_left_alone(self):
        before = {"lib/format.js": "export function money(n){ return n }",
                  "app/p/page.jsx": "const a = 1"}
        after = dict(before)
        after["app/p/page.jsx"] = "const s = money(12).toFixed(2)"
        self.assertEqual([], validate_repair_invariants(
            before, after, ["app/p/page.jsx"], "APP_FIX", {}))


class SeededAccountWaitTests(unittest.TestCase):
    """"Fixtures reseeded" means the accounts can sign in."""

    def test_the_reseed_waits_for_the_accounts_before_it_says_so(self):
        stage = importlib.import_module("server")
        source = stage._reseed_for_journey.__doc__ or ""
        self.assertTrue(source)
        body = (importlib.import_module("pathlib").Path(
            "server_modules/qa/e2e_stage.py").read_text(encoding="utf-8"))
        reseed = body.split("def _reseed_for_journey")[1].split("\ndef ")[0]
        wait = reseed.index("_wait_for_seeded_accounts(agent)")
        announce = reseed.index("fixtures reseeded")
        self.assertLess(wait, announce)

    def test_a_project_with_no_demo_accounts_does_not_wait(self):
        server = importlib.import_module("server")
        agent = SimpleNamespace(accounts=lambda: [])
        self.assertFalse(server._wait_for_seeded_accounts(agent, timeout=5))

    def test_an_agent_that_cannot_list_accounts_does_not_raise(self):
        server = importlib.import_module("server")
        def boom():
            raise RuntimeError("no plan")
        self.assertFalse(server._wait_for_seeded_accounts(
            SimpleNamespace(accounts=boom), timeout=5))

    def test_the_reseed_also_drops_the_dom_evidence_it_invalidated(self):
        body = (importlib.import_module("pathlib").Path(
            "server_modules/qa/e2e_stage.py").read_text(encoding="utf-8"))
        reseed = body.split("def _reseed_for_journey")[1].split("\ndef ")[0]
        self.assertIn("agent.invalidate_runtime_evidence()", reseed)


class PackageNameTests(unittest.TestCase):
    """The change format's own keywords are not dependencies."""

    def _spec_for(self, plan_text):
        from agents.features_planning import FeaturesAgentPlanningMixin

        class Arch:
            PKG_NAME_RE = re.compile(r"^(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*$",
                                     re.I)
            NODE_BUILTINS = {"fs", "path"}
            files = {}

        agent = object.__new__(FeaturesAgentPlanningMixin)
        agent.arch = Arch()
        return agent._parse(plan_text)

    def test_the_format_keyword_is_not_installed(self):
        spec = self._spec_for("PACKAGE FILE\nSUMMARY do a thing")
        self.assertEqual([], spec.packages)

    def test_an_uppercase_name_is_not_an_npm_name(self):
        spec = self._spec_for("PACKAGE React\nSUMMARY do a thing")
        self.assertEqual([], spec.packages)

    def test_a_real_dependency_still_gets_through(self):
        spec = self._spec_for("PACKAGE date-fns\nPACKAGE @tanstack/react-query\n"
                              "SUMMARY do a thing")
        self.assertEqual(["date-fns", "@tanstack/react-query"], spec.packages)

    def test_a_node_builtin_is_still_refused(self):
        self.assertEqual([], self._spec_for("PACKAGE path").packages)


if __name__ == "__main__":
    unittest.main()


class SourceRequirementTests(unittest.TestCase):
    """A noun that shares a spelling with a verb is not a requirement."""

    @staticmethod
    def _reqs(text):
        from agents.architect_planning import ArchitectPlanningMixin
        return ArchitectPlanningMixin._source_requirements(text)

    def test_a_sentence_that_names_the_app_is_not_a_requirement(self):
        # `book-borrowing` matched the `book` verb and blocked two builds.
        self.assertEqual([], self._reqs(
            "A library book-borrowing app with these screens."))

    def test_a_noun_after_a_determiner_is_not_a_verb(self):
        self.assertEqual([], self._reqs("The booking engine for a hotel."))

    def test_the_real_clauses_still_survive(self):
        got = self._reqs(
            "A library book-borrowing app with these screens. "
            "Catalogue (/books) lists every book with a photo. "
            "Seed five books, each with its own photo.")
        self.assertEqual(2, len(got))
        self.assertTrue(got[0].startswith("Catalogue"))
        self.assertTrue(got[1].startswith("Seed"))

    def test_an_actor_clause_keeps_its_actor(self):
        got = self._reqs("A member borrows a book and views their loans.")
        self.assertTrue(any("view" in r for r in got), got)
