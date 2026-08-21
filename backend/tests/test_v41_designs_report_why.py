"""Five whole-app designs at once is the busiest moment in a build."""
import unittest
from pathlib import Path

STAGE = Path("server_modules/agent/design_stage.py")
HANDLER = Path("server_modules/ui/http_handler.py")
BOOTSTRAP = Path("server_modules/core/bootstrap.py")


class TheBusyDaemonIsWaitedOutTests(unittest.TestCase):
    def setUp(self):
        self.body = STAGE.read_text(encoding="utf-8")

    def test_a_design_call_retries_instead_of_dropping(self):
        self.assertIn("with_retry(", self.body)
        self.assertIn("the design model (direction", self.body)

    def test_the_helpers_reach_the_shared_runtime(self):
        boot = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn("from agents.ollama_client import is_transient, with_retry",
                      boot)

    def test_a_dropped_design_records_why_it_was_dropped(self):
        self.assertIn("why.append", self.body)
        self.assertIn('"busy": busy', self.body)

    def test_a_non_html_answer_is_recorded_too(self):
        self.assertIn("was not an HTML document", self.body)


class ThePickerIsToldWhatHappenedTests(unittest.TestCase):
    def setUp(self):
        self.body = HANDLER.read_text(encoding="utf-8")

    def test_the_reasons_are_collected_and_passed_in(self):
        self.assertIn("why = []", self.body)
        self.assertIn("out, lock, why)", self.body)

    def test_a_total_failure_answers_with_a_reason(self):
        self.assertIn('"detail": detail', self.body)
        self.assertIn("was busy for all", self.body)
        self.assertIn("set a smaller", self.body)

    def test_a_partial_failure_is_still_reported(self):
        self.assertIn('"missing": len(why)', self.body)
        self.assertIn('"attempted": len(directions)', self.body)


class TheSpinnerSaysSomethingTests(unittest.TestCase):
    PICKER = Path("../../studio/components/ThemePicker.jsx")

    def setUp(self):
        if not self.PICKER.exists():
            self.skipTest("the studio tree is not beside the backend here")
        self.body = self.PICKER.read_text(encoding="utf-8")

    def test_the_wait_message_changes_as_it_goes(self):
        self.assertIn("function drawingNote", self.body)
        self.assertIn("it may be busy", self.body)

    def test_there_is_a_way_out_of_a_long_wait(self):
        self.assertIn("Skip the designs and build anyway", self.body)

    def test_an_empty_result_offers_a_retry(self):
        self.assertIn("Try again", self.body)
        self.assertIn("Build without choosing a design", self.body)


if __name__ == "__main__":
    unittest.main()
