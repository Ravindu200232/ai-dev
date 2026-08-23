"""HTML design previews stay bounded on large cloud reasoning models."""
import unittest
from pathlib import Path

from agents.picture import themes


ROOT = Path(__file__).resolve().parents[1]
STAGE = (ROOT / "server_modules/agent/design/stage.py").read_text(encoding="utf-8")
HANDLER = (ROOT / "server_modules/ui/http_handler.py").read_text(encoding="utf-8")
STUDIO = ROOT.parent / "studio"


class BoundedHTMLGenerationTests(unittest.TestCase):
    def test_html_generation_streams_with_reasoning_off(self):
        self.assertIn("ollama.chat_stream(", STAGE)
        self.assertIn("think=False", STAGE)
        self.assertNotIn("ollama.chat(\n", STAGE)

    def test_each_direction_has_a_real_time_and_output_budget(self):
        self.assertIn("THEME_TIMEOUT_S = 180", STAGE)
        self.assertIn("THEME_STALL_S = 120", STAGE)
        self.assertIn("THEME_OUTPUT_TOKENS = 6_000", STAGE)
        self.assertIn("attempts=THEME_RETRY_ATTEMPTS", STAGE)
        self.assertIn("under 24,000 characters", themes.SYSTEM)

    def test_only_slow_reasoning_families_use_three_choices(self):
        self.assertEqual(themes.SLOW_MODEL_COUNT, 3)
        self.assertEqual(themes.COUNT, 5)
        self.assertEqual(themes.design_count("minimax-m3:cloud"), 3)
        self.assertEqual(themes.design_count("qwen3.5:397b-cloud"), 3)
        self.assertEqual(themes.design_count("gemma4:31b-cloud"), 5)
        self.assertIn("themekit.design_count(model)", HANDLER)

    def test_picker_does_not_promise_a_hard_coded_count(self):
        picker = (STUDIO / "components/ThemePicker.jsx").read_text(encoding="utf-8")
        self.assertIn("Creating studio-grade visual directions", picker)
        self.assertNotIn("Creating five complete directions", picker)


if __name__ == "__main__":
    unittest.main()
