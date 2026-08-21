"""Gate the result of a repair, never the attempt."""
import unittest
from pathlib import Path

from agents.features_common import FeatureSpec
from agents.features_planning import FeaturesAgentPlanningMixin

PLANNING = Path("agents/features_planning.py")
RUNTIME = Path("server_modules/qa/runtime_repair.py")
STAGE = Path("server_modules/qa/e2e_stage.py")

FILES = {"app/layout.jsx": "export default function L(){return null}",
         "components/Navbar.jsx": "export default function N(){return null}"}


def decide(paths, cause, files=None, action="edit"):
    """Run the real decision against one spec."""
    agent = FeaturesAgentPlanningMixin.__new__(FeaturesAgentPlanningMixin)
    agent.arch = type("A", (), {"files": files if files is not None else FILES})()
    said = []
    agent._log = lambda lvl, txt: said.append(txt)
    spec = FeatureSpec(files=[{"path": p, "action": action} for p in paths])
    spec.context = {"cause": cause}
    out = agent._unproven_but_actionable(
        spec, ["no source EVIDENCE was cited"], "E2E repair")
    return (out if out.files else None), " ".join(said)


class AKnownCauseMayBeActedOnTests(unittest.TestCase):
    def test_a_named_cause_and_named_files_are_let_through(self):
        out, _ = decide(["app/layout.jsx"],
                        "the navbar calls useSession in an app with no auth",
                        FILES)
        self.assertIsNotNone(out)
        self.assertEqual([f["path"] for f in out.files], ["app/layout.jsx"])

    def test_it_says_plainly_that_it_is_letting_it_run(self):
        _, said = decide(["app/layout.jsx"], "a real root cause sentence", FILES)
        self.assertIn("letting", said)
        self.assertIn("reverted if it does not parse", said)

    def test_a_file_that_does_not_exist_is_dropped(self):
        out, _ = decide(["app/layout.jsx", "app/nope.jsx"], "a real cause", FILES)
        self.assertEqual([f["path"] for f in out.files], ["app/layout.jsx"])


class NothingToActOnIsStillRefusedTests(unittest.TestCase):
    def test_no_cause_means_no_attempt(self):
        out, _ = decide(["app/layout.jsx"], "", FILES)
        self.assertIsNone(out)

    def test_a_cause_too_short_to_be_one_is_refused(self):
        out, _ = decide(["app/layout.jsx"], "broken", FILES)
        self.assertIsNone(out)

    def test_no_file_means_no_attempt(self):
        out, _ = decide([], "a cause with nowhere to apply it", FILES)
        self.assertIsNone(out)


class TheResultIsStillGatedTests(unittest.TestCase):
    """Loosening the attempt is only safe because the outcome is checked."""

    def test_a_repair_that_does_not_parse_is_reverted(self):
        body = STAGE.read_text(encoding="utf-8")
        self.assertIn("problems, _ = check_syntax(", body)
        self.assertIn("snap.restore(fixed)", body)

    def test_a_repair_that_does_not_build_is_rolled_back_whole(self):
        body = STAGE.read_text(encoding="utf-8")
        self.assertIn("run_build_fix_loop(arch, proj_dir, True, max_rounds=1)", body)
        self.assertIn("stage_baseline.restore()", body)

    def test_the_scope_of_a_repair_did_not_widen(self):
        body = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("if strict_scope:", body)
        self.assertIn("nothing this fixer is allowed to edit", body)

    def test_the_old_refusal_is_gone(self):
        self.assertNotIn("no speculative fallback",
                         RUNTIME.read_text(encoding="utf-8"))
        self.assertNotIn("could not establish a source-backed impact map",
                         PLANNING.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
