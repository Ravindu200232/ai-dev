"""Regression tests for Architect handoff integrity."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agents.architect import ArchitectAgent


class TokenAccountingTests(unittest.TestCase):
    def test_new_agent_starts_with_token_counters(self):
        with (tempfile.TemporaryDirectory() as tmp,
              patch("agents.architect_runtime.max_context", return_value=4096),
              patch("agents.architect_runtime.is_cloud_model", return_value=False)):
            agent = ArchitectAgent(object(), "test-model", Path(tmp))

        self.assertEqual(agent.tokens_in, 0)
        self.assertEqual(agent.tokens_out, 0)

    def test_stream_completion_updates_token_counters(self):
        class Client:
            def chat_stream(self, *args, **kwargs):
                yield {
                    "message": {"content": "done"},
                    "done": True,
                    "prompt_eval_count": 13,
                    "eval_count": 8,
                }

        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.client = Client()
        agent.model = "test-model"
        agent.num_ctx = 4096
        agent._ctx_cache = {"test-model": 4096}
        agent.tokens_in = 0
        agent.tokens_out = 0
        agent._log = lambda *_: None
        output = []

        agent._stream_once([], output.append, tools=None, temperature=0.5,
                           model=None, timeout=None, think=False)

        self.assertEqual(output, ["done"])
        self.assertEqual(agent.tokens_in, 13)
        self.assertEqual(agent.tokens_out, 8)


class PreHandoffRepairTests(unittest.TestCase):
    def test_a_remaining_problem_gets_the_second_bounded_pass(self):
        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.stack = "next"
        problem = "components/Form.jsx:4: invalid JavaScript"
        agent.lint_generated = Mock(side_effect=[[problem], [problem]])
        agent.lint_plan_contracts = Mock(return_value=[])
        agent._run_write_loop = Mock(side_effect=[1, 1])
        agent._fix_boundary_props = Mock(return_value=0)
        agent._log = Mock()
        agent._fire = Mock()

        written = agent.repair_lint()

        self.assertEqual(written, 2)
        self.assertEqual(agent._run_write_loop.call_count, 2)

    def test_invalid_javascript_cannot_pass_final_handoff(self):
        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.stack = "next"
        agent.project_dir = Path(tempfile.gettempdir())
        agent.files = {
            "app/page.jsx": "export default function Page() { return null }",
            "components/A.jsx": "export default function A() { return null }",
            "components/B.jsx": "export default function B() { return null }",
            "components/C.jsx": "export default function C() { return null }",
        }
        agent._log = Mock()
        broken = [{"path": "components/A.jsx", "line": 3,
                   "message": 'Expected ")" but found "{"'}]

        with patch("agents.exports.check_syntax", return_value=(broken, "")):
            self.assertFalse(agent._verify_output())

        messages = " ".join(str(call) for call in agent._log.call_args_list)
        self.assertIn("invalid JavaScript", messages)


if __name__ == "__main__":
    unittest.main()
