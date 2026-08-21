"""v27 — work that is repeated with nothing changed in between.

Three measured wastes from one 27-minute build: the API stage re-attacked the
finding the runtime stage had just given up on (67s, a second model rewrite, a
fifth production build, a sixth dev boot); an E2E repair bought a second round
for a hypothesis one word apart from the one the browser had already refuted
(21s); and `warm()` paid every route's first-compile one after another when
`next dev` compiles them in parallel.
"""
import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace

from qa_agent.debugger_common import DebugNotebook


def finding(code="INERT_CONTROL", path="components/BorrowButton.jsx",
            message="renders permanently disabled 'Currently Borrowed'"):
    return SimpleNamespace(code=code, path=path, message=message,
                           severity="major")


class ExhaustedFindingTests(unittest.TestCase):
    """A stage that gave up hands that fact to the next one."""

    def setUp(self):
        self.server = importlib.import_module("server")
        self.server._EXHAUSTED_FINDINGS.clear()
        self._elog = self.server.elog
        self.logged = []
        self.server.elog = lambda lvl, txt: self.logged.append(txt)

    def tearDown(self):
        self.server.elog = self._elog
        self.server._EXHAUSTED_FINDINGS.clear()

    def test_nothing_is_dropped_before_anything_gives_up(self):
        got = self.server._drop_exhausted([finding()], "API verification")
        self.assertEqual(1, len(got))

    def test_a_parked_finding_is_not_repaired_again(self):
        self.server._park_exhausted([finding()])
        self.assertEqual([], self.server._drop_exhausted([finding()], "API"))
        self.assertTrue(any("already exhausted" in t for t in self.logged))

    def test_a_different_finding_is_still_repaired(self):
        self.server._park_exhausted([finding()])
        other = finding(code="DEAD_LINK", path="app/page.jsx",
                        message="links to /privacy but no page serves it")
        self.assertEqual([other],
                         self.server._drop_exhausted([other], "API"))

    def test_the_same_finding_with_a_different_record_id_still_matches(self):
        self.server._park_exhausted(
            [finding(message="GET /api/books/6a87b746a8de6a70a454fa67 failed")])
        again = finding(message="GET /api/books/6a87a044a8de6a70a454f9f5 failed")
        self.assertEqual([], self.server._drop_exhausted([again], "API"))


class DecisionShapeTests(unittest.TestCase):
    """A repair round is not bought twice for the same decision."""

    A = {"verdict": "TEST_FIX", "files": ["app/checkout/page.jsx"],
         "test_step": "CLICK :: role=button name=/pay/i",
         "hypothesis": "the journey is functionally correct, but the "
                       "contract's mutation requirement is a misconfiguration"}
    B = dict(A, hypothesis="the journey is functionally correct and the "
                           "contract's mutation requirement is a misconfiguration")

    def test_two_wordings_of_one_decision_have_one_shape(self):
        self.assertEqual(DebugNotebook.decision_shape(self.A),
                         DebugNotebook.decision_shape(self.B))

    def test_a_rejected_decision_is_remembered_by_shape(self):
        nb = DebugNotebook(goal="x")
        nb.record_outcome(self.A, progressed=False)
        self.assertIn(DebugNotebook.decision_shape(self.B), nb.rejected_shapes)

    def test_a_decision_that_moved_the_browser_is_not_rejected(self):
        nb = DebugNotebook(goal="x")
        nb.record_outcome(self.A, progressed=True)
        self.assertEqual([], nb.rejected_shapes)

    def test_a_different_verdict_on_the_same_file_is_a_new_idea(self):
        nb = DebugNotebook(goal="x")
        nb.record_outcome(self.A, progressed=False)
        other = dict(self.A, verdict="APP_FIX")
        self.assertNotIn(DebugNotebook.decision_shape(other), nb.rejected_shapes)

    def test_an_empty_decision_is_not_remembered(self):
        nb = DebugNotebook(goal="x")
        nb.record_outcome({"verdict": "", "files": [], "test_step": ""},
                          progressed=False)
        self.assertEqual([], nb.rejected_shapes)

    def test_the_guard_consults_the_shape(self):
        body = (Path("qa_agent/debugger_investigate.py")
                .read_text(encoding="utf-8"))
        self.assertIn("rejected_shapes", body)


class WarmConcurrencyTests(unittest.TestCase):
    def test_warm_fetches_cold_routes_together(self):
        body = Path("qa_agent/e2e_steps.py").read_text(encoding="utf-8")
        block = body.split("def warm(")[1].split("\n    def ")[0]
        self.assertIn("ThreadPoolExecutor", block)
        self.assertIn("_warmed_routes", block)

    def test_a_route_already_warm_is_not_fetched_again(self):
        body = Path("qa_agent/e2e_steps.py").read_text(encoding="utf-8")
        block = body.split("def warm(")[1].split("\n    def ")[0]
        self.assertIn("if p not in self._warmed_routes", block)


if __name__ == "__main__":
    unittest.main()


class OverlappedWorkTests(unittest.TestCase):
    """Work that waits on nothing does not wait its turn."""

    PIPE = Path("server_modules/agent/agent_pipeline.py")

    def test_performance_starts_before_the_confirmations_not_after(self):
        text = self.PIPE.read_text(encoding="utf-8")
        started = text.index("perf_thread = threading.Thread")
        # The post-E2E confirmation, not the one before the journeys.
        confirmed = text.rindex("run_runtime_verification_stage(")
        collected = text.index("thread.join(timeout=600)")
        self.assertLess(started, confirmed)
        self.assertLess(confirmed, collected)

    def test_the_measurement_is_still_collected_before_the_report(self):
        text = self.PIPE.read_text(encoding="utf-8")
        self.assertLess(text.index("thread.join(timeout=600)"),
                        text.index("write_qa_report("))

    def test_a_failed_measurement_is_still_reported(self):
        text = self.PIPE.read_text(encoding="utf-8")
        block = text.split("thread.join(timeout=600)")[1][:400]
        self.assertIn('raise perf_box["error"]', block)

    def test_the_journeys_start_with_their_routes_compiling(self):
        stage = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        self.assertLess(stage.index("_warm_routes_async(agent)"),
                        stage.index("journeys = agent.journeys()"))

    def test_warming_never_blocks_the_stage(self):
        prep = Path("server_modules/qa/e2e_prepare.py").read_text(encoding="utf-8")
        block = prep.split("def _warm_routes_async")[1]
        self.assertIn("daemon=True", block)
        self.assertNotIn(".join(", block)

    def test_an_app_with_no_pages_warms_nothing(self):
        server = importlib.import_module("server")
        agent = SimpleNamespace(route_map=lambda: {}, _warmed_routes=set())
        self.assertIsNone(server._warm_routes_async(agent))


class UnchangedSuiteTests(unittest.TestCase):
    """A suite nothing touched is not measured twice."""

    STAGE = Path("server_modules/qa/unit_stage.py")

    def test_the_sweep_is_skipped_only_when_no_app_code_moved(self):
        block = self.STAGE.read_text(encoding="utf-8")
        guard = block.split("swept_everything = watch is not None")[1][:400]
        self.assertIn("not unit_code_touched", guard)
        self.assertIn("not failures", guard)

    def test_a_code_fix_still_forces_the_whole_suite(self):
        block = self.STAGE.read_text(encoding="utf-8")
        self.assertIn("if swept_everything:\n        passed, failures, ok = runner.run()",
                      block)

    def test_skipping_still_counts_as_having_seen_everything(self):
        block = self.STAGE.read_text(encoding="utf-8")
        guard = block.split("not running it again")[1][:200]
        self.assertIn("saw_everything = True", guard)
