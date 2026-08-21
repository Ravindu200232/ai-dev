"""The chain that turned "add an admin button" into a rolled-back build."""
import inspect
import tempfile
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from agents.architect import ArchitectAgent

BUILDER_A = Path("agents/architect_next_builder_prompt_a.py")
STACK = Path("agents/architect_stack_rules.py")
SYMBOLS = Path("agents/architect_symbols.py")


def builder_prompt(idea="a portfolio site for one person"):
    a = ArchitectAgent.__new__(ArchitectAgent)
    a.plan, a.user_prompt = {}, idea
    a._vocab_cache = a._app_noun_cache = None
    a.stack, a.cb = "next", None
    a.project_dir = Path(tempfile.gettempdir())
    return a._builder_sys()


class AuthFilesOnlyExistWithAuthTests(unittest.TestCase):
    """The prompt claimed lib/auth.js was always there. It is not."""

    def setUp(self):
        self.prompt = builder_prompt()

    def test_the_unconditional_claim_is_gone(self):
        self.assertNotIn("AUTHENTICATION IS ALREADY BUILT", self.prompt)
        self.assertIn("AUTHENTICATION, WHEN THIS APP HAS IT", self.prompt)

    def test_it_says_plainly_that_a_portfolio_has_none(self):
        self.assertIn("THEY EXIST ONLY IN AN APP WITH SIGN-IN", self.prompt)
        self.assertIn("importing it is a build error", self.prompt)

    def test_the_file_list_is_named_as_the_source_of_truth(self):
        self.assertIn("The file list you were given is the truth", self.prompt)

    def test_it_warns_that_creating_it_is_refused(self):
        self.assertIn("creating it is refused", self.prompt)

    def test_the_stack_rules_agree(self):
        body = STACK.read_text(encoding="utf-8")
        self.assertIn("AN APP WITH NO SIGN-IN HAS NO `lib/auth.js` EITHER", body)
        self.assertIn("Sign-in, IF this app has one", body)

    def test_an_app_with_auth_still_gets_the_usage_example(self):
        self.assertIn("import { getSessionUser } from '@/lib/auth'", self.prompt)
        self.assertIn("IN AN APP THAT HAS AUTH", self.prompt)


class ScaffoldOwnedFilesAreNeverGeneratedTests(unittest.TestCase):
    def test_the_owned_set_names_the_files_the_model_may_not_write(self):
        for rel in ("lib/auth.js", "lib/auth-client.js", "lib/mongodb.js",
                    "app/api/auth/[...all]/route.js"):
            self.assertIn(rel, ArchitectAgent.OWNED_FILES, rel)

    def test_an_ordinary_component_is_not_owned(self):
        self.assertNotIn("components/Card.jsx", ArchitectAgent.OWNED_FILES)

    def test_the_generator_drops_them_before_asking(self):
        body = SYMBOLS.read_text(encoding="utf-8")
        self.assertIn("forbidden = sorted(m for m in missing "
                      "if m in self.OWNED_FILES)", body)
        self.assertIn("The import is the defect", body)

    def test_it_stops_entirely_when_only_owned_files_are_missing(self):
        body = SYMBOLS.read_text(encoding="utf-8")
        gap = body[body.index("forbidden = sorted("):
                   body.index("imported file(s) missing — generating")]
        self.assertIn("if not missing:\n                return 0", gap)


class ALinkToNowhereIsReportedTests(unittest.TestCase):
    """The navbar got an /admin button and /admin did not exist."""

    def setUp(self):
        import importlib
        self.src = inspect.getsource(
            importlib.import_module("server").verify_after_edit)

    def test_every_edit_now_checks_for_dead_links(self):
        self.assertIn("dead_links", self.src)
        self.assertIn("point at a page that does not", self.src)

    def test_it_runs_before_the_build_rather_than_after(self):
        self.assertLess(self.src.index("dead_links"),
                        self.src.index("run_build_fix_loop"))

    def test_a_scan_that_fails_does_not_stop_the_edit(self):
        self.assertIn("log.debug(f\"dead link scan: {e}\")", self.src)
        self.assertIn("dead = []", self.src)

    def test_the_result_is_reported_back_to_the_caller(self):
        self.assertIn('out["dead_links"] = dead', self.src)


if __name__ == "__main__":
    unittest.main()
