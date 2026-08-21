"""What the three edit flows do when something goes wrong."""
import json
import shutil
import tempfile
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

PENCIL = Path("server_modules/agent/pencil_page.py")
ACTIONS = Path("server_modules/agent/feature_actions.py")
APPLY = Path("agents/features_apply.py")


def server():
    import importlib
    return importlib.import_module("server")


class UndoRemovesWhatTheEditAddedTests(unittest.TestCase):
    """A created file has no `before`, so undoing it means deleting it."""

    def setUp(self):
        self.s = server()
        self.tmp = Path(tempfile.mkdtemp())
        self.old = (self.s.PROD_DIR, self.s.UNDO_DIR)
        self.s.PROD_DIR = self.tmp / "prod"
        self.s.UNDO_DIR = self.tmp / "undo"
        self.proj = self.s.PROD_DIR / "demo"
        (self.proj / "components").mkdir(parents=True)
        (self.proj / "components" / "Navbar.jsx").write_text("OLD", encoding="utf-8")

    def tearDown(self):
        self.s.PROD_DIR, self.s.UNDO_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _edit(self):
        stamp = self.s._snapshot(
            "demo", ["components/Navbar.jsx", "components/Card.jsx"],
            {"components/Navbar.jsx": "OLD"})
        (self.proj / "components" / "Navbar.jsx").write_text("NEW", encoding="utf-8")
        (self.proj / "components" / "Card.jsx").write_text("added", encoding="utf-8")
        return stamp

    def test_a_rewritten_file_comes_back(self):
        res = self.s.restore_snapshot("demo", self._edit())
        self.assertEqual((self.proj / "components" / "Navbar.jsx").read_text(), "OLD")
        self.assertIn("components/Navbar.jsx", res["restored"])

    def test_a_created_file_is_taken_away(self):
        res = self.s.restore_snapshot("demo", self._edit())
        self.assertFalse((self.proj / "components" / "Card.jsx").exists())
        self.assertIn("components/Card.jsx", res["removed"])

    def test_an_edit_that_only_creates_still_gets_an_undo_point(self):
        stamp = self.s._snapshot("demo", ["components/New.jsx"], {})
        self.assertTrue(stamp)

    def test_the_manifest_is_not_restored_as_a_project_file(self):
        res = self.s.restore_snapshot("demo", self._edit())
        self.assertNotIn(self.s.CREATED_MANIFEST, res["restored"])

    def test_a_path_that_climbs_out_is_refused(self):
        base = self.s.UNDO_DIR / "demo" / "evil"
        base.mkdir(parents=True)
        (base / self.s.CREATED_MANIFEST).write_text(
            json.dumps(["../../escape.txt"]), encoding="utf-8")
        outside = self.s.PROD_DIR.parent / "escape.txt"
        outside.write_text("still here", encoding="utf-8")
        res = self.s.restore_snapshot("demo", "evil")
        self.assertTrue(outside.exists())
        self.assertEqual(res["removed"], [])

    def test_an_old_snapshot_without_a_manifest_still_restores(self):
        stamp = self.s._snapshot("demo", ["components/Navbar.jsx"],
                                 {"components/Navbar.jsx": "OLD"})
        (self.proj / "components" / "Navbar.jsx").write_text("NEW", encoding="utf-8")
        res = self.s.restore_snapshot("demo", stamp)
        self.assertTrue(res["ok"])
        self.assertEqual(res["removed"], [])


class EveryFlowGuardsItsWriteTests(unittest.TestCase):
    def test_the_pencil_edit_is_guarded_like_the_page_edit(self):
        body = PENCIL.read_text(encoding="utf-8")
        self.assertEqual(body.count("guard_scope("), 2)
        self.assertIn("why = guard_scope(before, written, designing=True)", body)

    def test_a_rejected_pencil_edit_says_why(self):
        body = PENCIL.read_text(encoding="utf-8")
        self.assertIn("That edit was rejected —", body)

    def test_the_feature_flow_still_refuses_only_unsafe_paths(self):
        body = APPLY.read_text(encoding="utf-8")
        self.assertIn("ignored unsafe source path", body)
        self.assertIn("impact expanded to", body)


class OneReturnShapeTests(unittest.TestCase):
    def test_the_page_update_never_returns_a_tuple(self):
        body = PENCIL.read_text(encoding="utf-8")
        start = body.index("def run_page_update")
        end = body.index("def run_agent_update", start)
        self.assertNotIn('return False, ""', body[start:end])

    def test_the_pencil_write_round_still_returns_its_pair(self):
        body = PENCIL.read_text(encoding="utf-8")
        start = body.index("def _pencil_write_round")
        end = body.index("def run_pencil_edit", start)
        self.assertIn('return False, ""', body[start:end])
        self.assertIn("return True, body", body[start:end])


if __name__ == "__main__":
    unittest.main()
