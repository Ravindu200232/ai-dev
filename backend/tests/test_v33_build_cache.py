"""One build per set of bytes, not one build per stage."""
import tempfile
import time
import unittest
from pathlib import Path

from agents.build_cache import (already_green, clear, fingerprint, mark_green)


def project(**files):
    root = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


BASE = {"app/page.jsx": "export default () => null",
        "components/Row.jsx": "export default () => null",
        "package.json": '{"name":"x"}'}


class FingerprintTests(unittest.TestCase):
    def test_the_same_bytes_give_the_same_answer(self):
        root = project(**BASE)
        self.assertEqual(fingerprint(root), fingerprint(root))

    def test_an_empty_folder_has_no_fingerprint(self):
        self.assertEqual(fingerprint(project()), "")

    def test_what_the_compiler_never_reads_is_left_out(self):
        root = project(**BASE)
        before = fingerprint(root)
        for rel in ("node_modules/next/x.js", ".next/cache/y.js",
                    "tests/e2e/a.spec.js", "components/Row.test.jsx",
                    ".agentforge/plan.json", "playwright-report/index.html"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("noise", encoding="utf-8")
        self.assertEqual(fingerprint(root), before)

    def test_config_counts_because_the_compiler_reads_it(self):
        root = project(**BASE)
        before = fingerprint(root)
        (root / "next.config.mjs").write_text("export default {}", encoding="utf-8")
        self.assertNotEqual(fingerprint(root), before)


class GreenStateTests(unittest.TestCase):
    def setUp(self):
        self.root = project(**BASE)

    def test_nothing_is_green_before_the_first_build(self):
        self.assertFalse(already_green(self.root))

    def test_a_green_build_lets_the_next_stage_skip(self):
        mark_green(self.root, "round 1")
        self.assertTrue(already_green(self.root))

    def test_writing_only_tests_does_not_force_a_rebuild(self):
        mark_green(self.root)
        path = self.root / "tests/e2e/checkout.spec.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test('x', () => {})", encoding="utf-8")
        self.assertTrue(already_green(self.root))

    def test_touching_app_code_forces_a_rebuild(self):
        mark_green(self.root)
        time.sleep(0.01)
        (self.root / "app/page.jsx").write_text("export default () => <div/>",
                                                encoding="utf-8")
        self.assertFalse(already_green(self.root))

    def test_a_new_component_forces_a_rebuild(self):
        mark_green(self.root)
        (self.root / "components/New.jsx").write_text("export default () => null",
                                                      encoding="utf-8")
        self.assertFalse(already_green(self.root))

    def test_clearing_the_state_forces_a_rebuild(self):
        mark_green(self.root)
        clear(self.root)
        self.assertFalse(already_green(self.root))

    def test_a_missing_project_never_claims_to_be_green(self):
        self.assertFalse(already_green(Path(tempfile.mkdtemp()) / "gone"))


class WiringTests(unittest.TestCase):
    def test_the_build_loop_checks_and_records_the_state(self):
        text = Path("server_modules/agent/build_repair.py").read_text(encoding="utf-8")
        self.assertIn("already_green(proj_dir)", text)
        self.assertIn("mark_green(proj_dir", text)
        self.assertIn("build skipped", text)

    def test_a_caller_can_still_insist_on_a_real_build(self):
        text = Path("server_modules/agent/build_repair.py").read_text(encoding="utf-8")
        self.assertIn("force: bool = False", text)
        self.assertIn("if not force:", text)


if __name__ == "__main__":
    unittest.main()
