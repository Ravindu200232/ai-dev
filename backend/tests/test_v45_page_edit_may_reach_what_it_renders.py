"""A page edit that cannot touch the page's own Navbar is not a page edit."""
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

PENCIL = Path("server_modules/agent/pencil_page.py")
FEATURES = Path("agents/features_apply.py")


def rendered_by(files, roots):
    """The real helper, from the runtime namespace it lives in."""
    import importlib
    server = importlib.import_module("server")
    return server._rendered_by(type("A", (), {"files": files})(), roots)


PAGE = {
    "app/page.jsx": ("import Navbar from '@/components/Navbar'\n"
                     "import Hero from '@/components/Hero'\n"
                     "import Gone from '@/components/Gone'\n"),
    "components/Navbar.jsx": "export default function N(){return null}",
    "components/Hero.jsx": "export default function H(){return null}",
}


class ThePageReachesWhatItRendersTests(unittest.TestCase):
    def test_the_components_the_page_imports_are_writable(self):
        self.assertEqual(rendered_by(PAGE, {"app/page.jsx"}),
                         {"components/Navbar.jsx", "components/Hero.jsx"})

    def test_an_import_of_something_that_does_not_exist_is_ignored(self):
        self.assertNotIn("components/Gone.jsx", rendered_by(PAGE, {"app/page.jsx"}))

    def test_a_relative_import_resolves(self):
        files = {"app/x/page.jsx": "import C from './Card'", "app/x/Card.jsx": "z"}
        self.assertEqual(rendered_by(files, {"app/x/page.jsx"}), {"app/x/Card.jsx"})

    def test_a_parent_relative_import_resolves(self):
        files = {"app/x/page.jsx": "import C from '../Shared'", "app/Shared.jsx": "z"}
        self.assertEqual(rendered_by(files, {"app/x/page.jsx"}), {"app/Shared.jsx"})

    def test_a_package_import_is_not_a_project_file(self):
        files = {"app/page.jsx": "import { motion } from 'framer-motion'"}
        self.assertEqual(rendered_by(files, {"app/page.jsx"}), set())

    def test_a_page_that_imports_nothing_is_fine(self):
        self.assertEqual(rendered_by({"app/page.jsx": "export default 1"},
                                     {"app/page.jsx"}), set())


class TheRuleIsWiredInTests(unittest.TestCase):
    def setUp(self):
        self.body = PENCIL.read_text(encoding="utf-8")

    def test_the_writable_set_includes_what_the_page_renders(self):
        self.assertIn("writable |= _rendered_by(arch, writable)", self.body)

    def test_app_source_outside_the_tree_is_taken_not_dropped(self):
        self.assertIn("is outside this page's tree but", self.body)
        self.assertIn("is app source — taking it", self.body)

    def test_a_taken_edit_is_still_reverted_if_it_breaks(self):
        self.assertIn("if it does not parse or build", self.body)

    def test_the_old_backwards_rule_is_gone(self):
        self.assertNotIn("new file under components/", self.body)

    def test_something_that_is_not_app_source_is_still_refused(self):
        self.assertIn("changes app/, components/ or lib/ source", self.body)


class TheMessageStopsBlamingTheModelTests(unittest.TestCase):
    def setUp(self):
        self.body = PENCIL.read_text(encoding="utf-8")

    def test_refused_writes_are_remembered(self):
        self.assertIn("got, raw, refused = {}, [], []", self.body)
        self.assertIn("refused.append(key)", self.body)

    def test_a_refused_write_is_not_reported_as_no_write(self):
        self.assertIn("every write was outside what a page edit", self.body)
        self.assertIn("which a page edit cannot touch", self.body)


class TheFeatureFlowWasNeverGatedTests(unittest.TestCase):
    def test_a_feature_write_outside_the_plan_only_gets_a_note(self):
        body = FEATURES.read_text(encoding="utf-8")
        self.assertIn("impact expanded to", body)
        self.assertNotIn("ignored a write to", body)

    def test_an_unsafe_path_is_still_refused_there(self):
        self.assertIn("ignored unsafe source path",
                      FEATURES.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
