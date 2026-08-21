"""A busy daemon is not a verdict on the app."""
import unittest
from pathlib import Path

from agents.ollama_client import (RETRY_ATTEMPTS, is_transient, retry_delay,
                                  with_retry)

BUSY = ("Ollama 503 @ http://localhost:11434: {\"error\":\"model "
        "'gemma4:31b' is temporarily overloaded, please retry shortly\"}")


class TransientDetectionTests(unittest.TestCase):
    def test_the_overload_this_run_hit_is_recognised(self):
        self.assertTrue(is_transient(BUSY))

    def test_a_real_rejection_is_not_retried(self):
        self.assertFalse(is_transient("Ollama 400 @ x: bad payload"))
        self.assertFalse(is_transient("Ollama 404 @ x: model not found"))
        self.assertFalse(is_transient(KeyError("message")))

    def test_a_stalled_stream_counts_as_busy(self):
        self.assertTrue(is_transient("gemma sent nothing for 300s"))
        self.assertTrue(is_transient("connection reset by peer"))

    def test_the_wait_grows_and_stays_bounded(self):
        waits = [retry_delay(n) for n in range(1, 6)]
        self.assertLess(waits[0], waits[2])
        self.assertTrue(all(w <= 45 for w in waits))


class RetryLoopTests(unittest.TestCase):
    def test_it_waits_the_busy_daemon_out(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError(BUSY)
            return "written"

        import agents.ollama_client as oc
        original = oc.time.sleep
        oc.time.sleep = lambda _s: None
        try:
            self.assertEqual(with_retry(flaky, what="the QA model"), "written")
        finally:
            oc.time.sleep = original
        self.assertEqual(calls["n"], 3)

    def test_a_real_error_is_raised_at_once(self):
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise ValueError("the prompt template is wrong")

        with self.assertRaises(ValueError):
            with_retry(broken)
        self.assertEqual(calls["n"], 1)

    def test_it_gives_up_rather_than_hanging_forever(self):
        calls = {"n": 0}

        def always_busy():
            calls["n"] += 1
            raise RuntimeError(BUSY)

        import agents.ollama_client as oc
        original = oc.time.sleep
        oc.time.sleep = lambda _s: None
        try:
            with self.assertRaises(RuntimeError):
                with_retry(always_busy)
        finally:
            oc.time.sleep = original
        self.assertEqual(calls["n"], RETRY_ATTEMPTS)


class BlockedIsNotFailedTests(unittest.TestCase):
    def test_authoring_marks_the_scenario_blocked_not_empty(self):
        text = Path("qa_agent/e2e_journeys.py").read_text(encoding="utf-8")
        self.assertIn('setattr(empty, "blocked", blocked)', text)
        self.assertIn("with_retry", text)

    def test_the_stage_does_not_spend_a_rewrite_on_a_busy_model(self):
        text = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        self.assertIn('if getattr(sc, "blocked", False):', text)
        self.assertIn("not spending a ", text)

    def test_a_blocked_journey_is_counted_apart_from_a_failure(self):
        text = Path("server_modules/qa/e2e_stage.py").read_text(encoding="utf-8")
        self.assertIn('out["blocked"] = 1', text)
        self.assertIn("blocked_model", text)
        self.assertIn("nothing here says the app is broken", text)


if __name__ == "__main__":
    unittest.main()
