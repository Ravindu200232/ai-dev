"""Large cloud plans converge without lossy ledgers or full rewrite loops."""
import unittest
from pathlib import Path
from unittest.mock import Mock

from agents.builder.orchestration.agent import ArchitectAgent


class WorkflowCoverageCapacityTests(unittest.TestCase):
    def _agent(self):
        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent._known_fr = set()
        agent._app_noun_cache = set()
        agent._log = Mock()
        return agent

    def test_normalizer_does_not_drop_cap_21_through_cap_40(self):
        ids = [f"CAP-{i:03d}" for i in range(1, 24)]
        raw = {
            "tasks": [{
                "id": 1,
                "title": "application",
                "files": [{"path": "app/page.jsx", "kind": "server"}],
            }],
            "capabilities": [{
                "id": cid,
                "who": "member",
                "requirement": f"member completes operation {i}",
                "proof": f"operation {i} is visible",
                "files": ["app/page.jsx"],
                "e2e": True,
            } for i, cid in enumerate(ids, 1)],
            "workflows": [{
                "name": "member journey",
                "who": "member",
                "covers": ids,
                "steps": ["/ — complete the operations — results are visible"],
            }],
        }

        plan = self._agent()._normalise_plan(raw)

        self.assertEqual(plan["workflows"][0]["covers"], ids)
        self.assertIn("CAP-023", plan["workflows"][0]["covers"])

    def test_unwalked_capability_is_attached_with_a_real_step(self):
        agent = self._agent()
        plan = {
            "phases": [{
                "id": 1,
                "title": "orders",
                "files": [
                    {"path": "app/orders/page.jsx"},
                    {"path": "components/OrderActions.jsx"},
                ],
            }],
            "capabilities": [
                {"id": "CAP-001", "who": "member",
                 "requirement": "member creates an order",
                 "proof": "the created order appears",
                 "files": ["app/orders/page.jsx", "components/OrderActions.jsx"],
                 "e2e": True},
                {"id": "CAP-023", "who": "member",
                 "requirement": "member cancels an order",
                 "proof": "the order status becomes cancelled",
                 "files": ["app/orders/page.jsx", "components/OrderActions.jsx"],
                 "e2e": True},
            ],
            "workflows": [{
                "name": "member orders",
                "who": "member",
                "covers": ["CAP-001"],
                "steps": ["/orders — create an order — the order appears"],
            }],
        }

        repaired = agent._repair_capability_map(plan)

        self.assertEqual(repaired, 1)
        self.assertIn("CAP-023", plan["workflows"][0]["covers"])
        self.assertTrue(any("cancels an order" in step
                            for step in plan["workflows"][0]["steps"]))
        self.assertEqual(agent._capability_gaps(plan, "")[1], [])


class RequirementLedgerRepairTests(unittest.TestCase):
    def test_existing_capability_files_receive_the_omitted_fr_id(self):
        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent._log = Mock()
        agent._fr_text = {"FR-028": "Managers export monthly reports"}
        plan = {
            "phases": [{
                "id": 4,
                "title": "reports",
                "covers": [],
                "files": [{"path": "app/manager/reports/page.jsx"}],
            }],
            "capabilities": [{
                "id": "CAP-023",
                "requirement": "manager can export the monthly report",
                "proof": "a report file downloads",
                "files": ["app/manager/reports/page.jsx"],
                "e2e": True,
            }],
        }

        repaired = agent._repair_requirement_coverage(plan, ["FR-028"])

        self.assertEqual(repaired, 1)
        self.assertEqual(plan["phases"][0]["covers"], ["FR-028"])

    def test_make_plan_repairs_clerical_ledgers_without_a_second_model_call(self):
        raw = """```json
{
  "tasks": [{
    "id": 1,
    "title": "reports",
    "covers": [],
    "files": [{"path": "app/page.jsx", "kind": "server"}]
  }],
  "capabilities": [{
    "id": "CAP-023",
    "who": "manager",
    "requirement": "manager exports the monthly report",
    "proof": "a report file downloads",
    "files": ["app/page.jsx"],
    "e2e": true
  }],
  "workflows": [{
    "name": "manager reports",
    "who": "manager",
    "covers": ["CAP-023"],
    "steps": ["/ — export the monthly report — a report file downloads"]
  }]
}
```"""
        agent = ArchitectAgent.__new__(ArchitectAgent)
        calls = []

        def stream(_messages, sink, **_kwargs):
            calls.append(1)
            sink(raw)

        agent._stream = stream
        agent._planner_sys = lambda: "planner"
        agent._design_md = lambda: "design"
        agent.write_file = Mock()
        agent.write_own = Mock()
        agent._save_plan_json = Mock()
        agent.start_conversation = Mock()
        agent.save_convo = Mock()
        agent._fire = Mock()
        agent._log = Mock()
        agent.planner_model = "planner-cloud"
        agent.planner_is_cloud = True

        brief = "- FR-028: Managers export monthly reports"
        ok = agent.make_plan(brief, requirement_source=brief)

        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        self.assertEqual(agent.plan["phases"][0]["covers"], ["FR-028"])


class PlannerBudgetTests(unittest.TestCase):
    def test_only_cloud_planning_sets_the_large_output_ceiling(self):
        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.planner_is_cloud = True
        self.assertEqual(agent._planning_output_limit(), 16_000)
        agent.planner_is_cloud = False
        self.assertIsNone(agent._planning_output_limit())

    def test_output_token_budget_reaches_the_cloud_request(self):
        class Client:
            def __init__(self):
                self.options = None

            def chat_stream(self, *args, **kwargs):
                self.options = kwargs["options"]
                yield {"message": {"content": "done"}, "done": True}

        agent = ArchitectAgent.__new__(ArchitectAgent)
        agent.client = Client()
        agent.model = "cloud-model"
        agent.num_ctx = 131072
        agent._ctx_cache = {"cloud-model": 131072}
        agent.tokens_in = agent.tokens_out = 0
        agent._log = Mock()

        agent._stream_once([], lambda _delta: None, tools=None,
                           temperature=0.5, model=None, timeout=30,
                           think=True, max_output_tokens=16_000)

        self.assertEqual(agent.client.options["num_predict"], 16_000)

    def test_prompt_tells_verbose_models_to_finish_inside_the_budget(self):
        prompt = Path("agents/planner/prompt_a.py").read_text(
            encoding="utf-8")
        self.assertIn("under 45,000 characters", prompt)
        self.assertIn("reserve enough space to close", prompt)

    def test_repair_turns_drop_discarded_drafts_from_context(self):
        source = Path("agents/planner/execution.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("messages = messages[:2] + ["), 4)
        self.assertNotIn("messages += [", source)


if __name__ == "__main__":
    unittest.main()
