"""Planner, design and builder models stay separate across the build."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from agents.builder.orchestration.agent import ArchitectAgent


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT.parent / "studio"


class RoleModelRuntimeTests(unittest.TestCase):
    def test_runtime_keeps_three_models_and_their_contexts(self):
        contexts = {"builder-x": 32_000, "planner-x": 131_072,
                    "design-x": 65_536}
        with TemporaryDirectory() as tmp, \
            patch("agents.builder.orchestration.runtime.max_context",
                   side_effect=lambda model: contexts[model]), \
            patch("agents.builder.orchestration.runtime.is_cloud_model",
                   side_effect=lambda model: model != "builder-x"):
            agent = ArchitectAgent(
                object(), "builder-x", Path(tmp), think=True,
                planner_model="planner-x", design_model="design-x")

        self.assertEqual(agent.model, "builder-x")
        self.assertEqual(agent.planner_model, "planner-x")
        self.assertEqual(agent.design_model, "design-x")
        self.assertEqual(agent.planner_num_ctx, 131_072)
        self.assertEqual(agent.design_num_ctx, 65_536)
        self.assertTrue(agent.think)

    def test_builder_thinking_is_opt_in_and_other_roles_can_force_it_off(self):
        with TemporaryDirectory() as tmp, \
            patch("agents.builder.orchestration.runtime.max_context",
                  return_value=32_000), \
            patch("agents.builder.orchestration.runtime.is_cloud_model",
                  return_value=False):
            default_agent = ArchitectAgent(object(), "builder-x", Path(tmp))

        self.assertFalse(default_agent.think)

        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.think = True
        agent._log = Mock()
        agent._stream_once = Mock(return_value=([], False))

        agent._stream([], lambda _text: None, model="builder-x")
        self.assertTrue(agent._stream_once.call_args.kwargs["think"])

        agent._stream_once.reset_mock()
        agent._stream([], lambda _text: None, model="planner-x",
                      reasoning=False)
        self.assertFalse(agent._stream_once.call_args.kwargs["think"])

    def test_approved_design_interpretation_uses_design_model_without_thinking(self):
        client = Mock()
        client.chat.return_value = {
            "message": {"content": "A precise design contract. " * 20}}
        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.client = client
        agent.model = "builder-x"
        agent.design_model = "design-x"
        agent.theme_html = "<html><style>:root{--accent:#123456}</style></html>"
        agent.theme_page = "Home"
        agent.plan = {}
        agent.plan_md = ""
        agent._log = Mock()

        agent._design_md()

        self.assertEqual(client.chat.call_args.args[0], "design-x")
        self.assertFalse(client.chat.call_args.kwargs["think"])


class RoleModelWiringTests(unittest.TestCase):
    def test_all_planner_turns_use_planner_model_with_thinking_off(self):
        source = (ROOT / "agents/planner/execution.py").read_text("utf-8")
        self.assertEqual(source.count("self._stream(messages,"), 5)
        self.assertEqual(source.count("model=self.planner_model"), 5)
        self.assertGreaterEqual(source.count("reasoning=False"), 5)

    def test_ui_sends_all_three_roles_and_persists_each_selection(self):
        home = (STUDIO / "components/Home.jsx").read_text("utf-8")
        sidebar = (STUDIO / "components/Sidebar.jsx").read_text("utf-8")
        store = (STUDIO / "lib/store.js").read_text("utf-8")
        for role in ("planner", "design", "builder"):
            self.assertIn(f"{role}_model", home)
            self.assertIn(f"label=\"{role.title()}\"", sidebar)
            self.assertIn(f"{role}:", store)

    def test_thinking_defaults_off_and_is_shared_only_by_builder_and_qa(self):
        store = (STUDIO / "lib/store.js").read_text("utf-8")
        sidebar = (STUDIO / "components/Sidebar.jsx").read_text("utf-8")
        pipeline = (ROOT / "server_modules/agent/builder/pipeline.py").read_text("utf-8")
        picture = (ROOT / "server_modules/agent/picture/images.py").read_text("utf-8")

        self.assertIn("think: false", store)
        self.assertIn("DEFAULTS.think ? '1' : '0'", store)
        self.assertIn("Builder and QA work", sidebar)
        self.assertIn("think: bool = False", pipeline)
        self.assertIn('return bool(msg.get("think", False))', picture)

    def test_server_routes_roles_into_architect(self):
        pipeline = (ROOT / "server_modules/agent/builder/pipeline.py").read_text("utf-8")
        socket = (ROOT / "server_modules/agent/builder/project_ops.py").read_text("utf-8")
        for role in ("planner_model", "design_model"):
            self.assertIn(f"{role}={role}", pipeline)
            self.assertIn(f'"{role}"', socket)
        self.assertIn("model = model or default_builder_model()", pipeline)

    def test_qa_model_calls_follow_the_shared_thinking_switch(self):
        qa_sources = "\n".join(
            path.read_text("utf-8") for path in (ROOT / "qa_agent").glob("*.py"))
        self.assertEqual(qa_sources.count("self.arch._stream("), 5)
        self.assertGreaterEqual(
            qa_sources.count("reasoning=QASession.reasoning_for"), 5)

        runtime_repair = (ROOT / "server_modules/qa/runtime_repair.py").read_text("utf-8")
        unit_stage = (ROOT / "server_modules/qa/unit_stage.py").read_text("utf-8")
        self.assertIn("reasoning = QASession.reasoning_for(qa) if qa is not None else None",
                      runtime_repair)
        self.assertEqual(runtime_repair.count("reasoning=reasoning"), 2)
        self.assertIn("reasoning=QASession.reasoning_for(qa)", unit_stage)

    def test_user_bug_fixer_uses_builder_model_and_thinking_switch(self):
        page = (STUDIO / "app/page.jsx").read_text("utf-8")
        bugfix = (ROOT / "server_modules/agent/repair/chat.py").read_text("utf-8")
        self.assertIn("model: st.models.builder", page)
        self.assertIn("_open_for_edit(proj_name, model, think)", bugfix)
        self.assertIn("Bug Fixer — Builder {model}", bugfix)

    def test_srs_and_deployment_payloads_force_thinking_off(self):
        srs = (ROOT / "srs-agent/srs_agent/app/llm/ollama_adapter.py").read_text("utf-8")
        deploy = (ROOT / "deployment-agent/deploy_agent/dfagents/planner.py").read_text("utf-8")
        self.assertIn('"think": False', srs)
        self.assertIn('"think": False', deploy)
        self.assertNotIn('"think": OLLAMA_THINK', deploy)


if __name__ == "__main__":
    unittest.main()
