import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.features import FeaturesAgent
from agents.features_common import FeatureSpec


class RepairEvidenceAnchorTests(unittest.TestCase):
    def planner(self):
        agent = object.__new__(FeaturesAgent)
        agent.arch = SimpleNamespace(
            files={
                "app/admin-room-management/page.jsx": "import RoomForm from '@/components/RoomForm'",
                "components/RoomForm.jsx": "export default function RoomForm(){return null}",
            },
            PKG_NAME_RE=re.compile(r".*"), NODE_BUILTINS=set(),
        )
        return agent

    def test_runtime_focus_is_not_required_root_cause_evidence(self):
        agent = self.planner()
        spec = FeatureSpec(
            files=[{"path":"components/RoomForm.jsx", "action":"edit", "kind":"client", "why":"state proof owner"}],
            context={
                "current":"mutation succeeds but the UI stays stale",
                "gap":"updated value must appear",
                "cause":"RoomForm keeps stale local state after the mutation",
                "evidence":[{"path":"components/RoomForm.jsx", "fact":"component owns the post-mutation state"}],
                "verify":"updated value is rendered after save",
                "confidence":"high",
            },
        )
        self.assertEqual(agent._analysis_issues(spec, "repair"), [])


    def test_empty_incomplete_analysis_reports_protocol_gaps(self):
        agent = self.planner()
        spec = FeatureSpec(context={"current":"mutation succeeded"})
        issues = agent._analysis_issues(spec, "repair")
        self.assertTrue(any("missing GAP" in x for x in issues))
        self.assertTrue(any("no source EVIDENCE" in x for x in issues))
        self.assertTrue(any("missing VERIFY" in x for x in issues))

    def test_visual_selected_owner_still_requires_evidence(self):
        agent = self.planner()
        spec = FeatureSpec(
            files=[{"path":"components/RoomForm.jsx", "action":"edit", "kind":"client", "why":"child change"}],
            context={"current":"old", "gap":"change", "cause":"child owns it",
                     "evidence":[{"path":"components/RoomForm.jsx", "fact":"child markup"}],
                     "verify":"visible change", "confidence":"high"},
        )
        issues = agent._analysis_issues(
            spec, "visual", required_evidence_paths=["app/admin-room-management/page.jsx"])
        self.assertTrue(any("selected/failing owner" in x for x in issues))


class RepairPlanningCallTests(unittest.TestCase):
    def test_plan_repair_treats_focus_as_investigation_seed(self):
        class A(FeaturesAgent):
            def _plan(self, *args, **kwargs):
                self.kwargs = kwargs
                return FeatureSpec()
            def _focused_source(self, focus, budget): return "source"
            def _budget_chars(self): return 100000
        arch = SimpleNamespace(
            files={"app/page.jsx":"export default function Page(){}"},
            plan_text=lambda: "plan",
            enumerate_routes=lambda: {"/":{"file":"app/page.jsx"}},
            route_table=lambda routes: "/ -> app/page.jsx",
            inventory=lambda: "app/page.jsx",
            _route_matches=lambda *a: False,
        )
        agent = object.__new__(A)
        agent.arch = arch
        agent.az = arch
        agent.model = "model"
        agent.plan_repair("failed", focus_paths=["app/page.jsx"])
        self.assertEqual(agent.kwargs.get("investigation_paths"), ["app/page.jsx"])
        self.assertIsNone(agent.kwargs.get("required_evidence_paths"))


if __name__ == "__main__":
    unittest.main()
