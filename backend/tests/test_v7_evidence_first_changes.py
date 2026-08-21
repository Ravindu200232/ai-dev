import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.features import FeaturesAgent
from agents.features_common import FeatureSpec
from qa_agent.e2e_progress import extend_round_budget


class EvidenceFirstPlanningTests(unittest.TestCase):
    def planner(self, count=1):
        agent = object.__new__(FeaturesAgent)
        files = {f"components/C{i}.jsx": f"export default function C{i}(){{return null}}"
                 for i in range(count)}
        agent.arch = SimpleNamespace(
            files=files,
            PKG_NAME_RE=re.compile(r"^(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+$", re.I),
            NODE_BUILTINS=set(),
        )
        return agent

    def test_edit_is_rejected_as_speculative_without_source_evidence(self):
        agent = self.planner()
        spec = FeatureSpec(
            files=[{"path":"components/C0.jsx", "action":"edit", "kind":"client", "why":"guess"}],
            context={"current":"old", "gap":"wrong behavior", "cause":"looks related",
                     "evidence":[], "verify":"new behavior visible", "confidence":"high"},
        )
        issues = agent._analysis_issues(spec, "feature")
        self.assertTrue(any("not yet evidenced" in x or "no source EVIDENCE" in x for x in issues))

    def test_large_evidence_backed_change_is_not_blocked(self):
        agent = self.planner(24)
        evidence = [{"path":f"components/C{i}.jsx", "fact":f"C{i} owns required branch"}
                    for i in range(24)]
        spec = FeatureSpec(
            files=[{"path":f"components/C{i}.jsx", "action":"edit", "kind":"client", "why":"proven"}
                   for i in range(24)],
            context={"current":"old flow", "gap":"cross-cutting change", "cause":"shared behavior spans owners",
                     "evidence":evidence, "verify":"all requested branches are present", "confidence":"high"},
        )
        self.assertEqual(agent._analysis_issues(spec, "feature"), [])

    def test_parser_keeps_root_cause_evidence_and_proof(self):
        agent = self.planner()
        spec = agent._parse("\n".join([
            "CURRENT :: button calls old handler",
            "GAP :: request needs new behavior",
            "CAUSE :: C0 owns the click handler",
            "EVIDENCE :: components/C0.jsx :: source contains the handler",
            "SUMMARY :: update handler",
            "FILE :: edit :: components/C0.jsx :: client :: change proven handler",
            "VERIFY :: clicking it reaches the new state",
            "CONFIDENCE :: high",
            "DONE",
        ]))
        self.assertEqual(spec.context["cause"], "C0 owns the click handler")
        self.assertEqual(spec.context["evidence"][0]["path"], "components/C0.jsx")
        self.assertEqual(agent._analysis_issues(spec, "feature"), [])


class FocusedWriterTransactionTests(unittest.TestCase):
    class Arch:
        EDIT_TIMEOUT = 5
        def __init__(self, replies):
            self.files = {
                "app/page.jsx": "export default function Page(){return <main/>}",
                "components/Child.jsx": "export default function Child(){return <div/>}",
            }
            self.project_dir = Path(".")
            self._workspace_tool_cache = {}
            self.replies = list(replies)
            self.calls = 0
        def _stream(self, convo, sink, **kwargs):
            self.calls += 1
            reply = self.replies.pop(0)
            sink(reply)
        def _budget_chars(self): return 100000
        def run_requested_commands(self, _raw): return []

    def test_selection_never_writes_dependency_body_into_selected_page(self):
        import server
        arch = self.Arch([
            '<write_file path="components/Child.jsx">export default function Child(){return <section/>}</write_file>'
        ])
        ok, result = server._element_write_round(
            arch, "app/page.jsx", arch.files["app/page.jsx"], "change child card",
            {"route":"/", "tag":"main", "text":""}, "", False, attempts=1)
        self.assertFalse(ok)
        self.assertIn("__AGENTFORGE_ESCALATE__", result)
        self.assertIn("components/Child.jsx", result)

    def test_pencil_can_inspect_workspace_before_writing(self):
        import server
        arch = self.Arch([
            '<read_file path="components/Child.jsx"/>',
            '<write_file path="app/page.jsx">export default function Page(){return <main>changed</main>}</write_file>',
        ])
        ok, body = server._pencil_write_round(
            arch, "app/page.jsx", arch.files["app/page.jsx"], "redesign this region",
            {"route":"/", "tag":"main", "text":""}, None, "vision-model",
            {"route":"/", "viewport":{}}, attempts=1)
        self.assertTrue(ok)
        self.assertIn("changed", body)
        self.assertEqual(arch.calls, 2)

    def test_pencil_escalates_multi_file_output_instead_of_misapplying_it(self):
        import server
        arch = self.Arch([
            '<write_file path="components/Child.jsx">export default function Child(){return <section/>}</write_file>'
        ])
        ok, result = server._pencil_write_round(
            arch, "app/page.jsx", arch.files["app/page.jsx"], "redesign child region",
            {"route":"/", "tag":"main", "text":""}, None, "vision-model",
            {"route":"/", "viewport":{}}, attempts=1)
        self.assertFalse(ok)
        self.assertIn("components/Child.jsx", result)


class AdaptiveE2EBudgetTests(unittest.TestCase):
    def test_round_four_progress_earns_more_rounds(self):
        self.assertEqual(extend_round_budget(4, 4, 10, 2, True), 6)
        self.assertEqual(extend_round_budget(6, 6, 10, 2, True), 8)

    def test_no_progress_does_not_buy_more_rounds(self):
        self.assertEqual(extend_round_budget(4, 4, 10, 2, False), 4)
        self.assertEqual(extend_round_budget(3, 4, 10, 2, True), 4)

    def test_progress_never_exceeds_hard_cap(self):
        self.assertEqual(extend_round_budget(10, 10, 10, 2, True), 10)
        self.assertEqual(extend_round_budget(9, 9, 10, 4, True), 10)


if __name__ == "__main__":
    unittest.main()

class VisualPreflightEvidenceTests(unittest.TestCase):
    def _spec(self, *, proven):
        ctx = {"current":"old", "gap":"change", "cause":"page owns selected region",
               "evidence":([{"path":"app/page.jsx", "fact":"selected markup here"}] if proven else []),
               "verify":"selected region reflects request", "confidence":"high"}
        return FeatureSpec(files=[{"path":"app/page.jsx", "action":"edit", "kind":"server", "why":"owner"}],
                           context=ctx)

    def test_unproven_single_file_visual_plan_escalates_instead_of_blind_edit(self):
        import server
        from unittest.mock import patch
        fake = SimpleNamespace(plan_change=lambda *a, **k: self._spec(proven=False),
                               cover_whole_request=lambda req, spec: spec)
        with patch.object(server, "FeaturesAgent", return_value=fake):
            broader, request, _ = server._visual_change_preflight(
                SimpleNamespace(), SimpleNamespace(), Path("."), "change it",
                {"tag":"div", "route":"/"}, "app/page.jsx", "/", "model")
        self.assertTrue(broader)
        self.assertIn("Re-analyze", request)

    def test_proven_single_file_visual_plan_keeps_fast_path(self):
        import server
        from unittest.mock import patch
        fake = SimpleNamespace(plan_change=lambda *a, **k: self._spec(proven=True),
                               cover_whole_request=lambda req, spec: spec)
        with patch.object(server, "FeaturesAgent", return_value=fake):
            broader, _, spec = server._visual_change_preflight(
                SimpleNamespace(), SimpleNamespace(), Path("."), "change it",
                {"tag":"div", "route":"/"}, "app/page.jsx", "/", "model")
        self.assertFalse(broader)
        self.assertEqual(spec.context["confidence"], "high")


class RuntimeRepairEvidenceTests(unittest.TestCase):
    def test_strict_runtime_repair_does_not_call_fixer_when_planner_cannot_prove_change(self):
        import server
        from unittest.mock import patch

        class Planner:
            def repair_focus_paths(self, focus, evidence): return list(focus or [])
            def plan_repair(self, *args, **kwargs): return FeatureSpec()
        class Fixer:
            def fix_runtime(self, *args, **kwargs):
                raise AssertionError("fixer must not run without an evidence-backed plan")
        arch = SimpleNamespace(files={"app/page.jsx":"export default function Page(){}"},
                               cmd=SimpleNamespace(run=lambda cmd: "ok"))
        with patch.object(server, "FeaturesAgent", return_value=Planner()), \
             patch.object(server, "BugFixerAgent", return_value=Fixer()):
            got = server._repair_runtime(
                arch, Path("."), None, SimpleNamespace(), "runtime failed", "trace",
                1, "model", focus_paths=["app/page.jsx"], strict_scope=True)
        self.assertEqual(got, [])


class AdaptivePlanningConvergenceTests(unittest.TestCase):
    def test_evidence_progress_can_continue_past_old_three_turn_limit(self):
        paths = [f"components/C{i}.jsx" for i in range(4)]
        files = {p: f"export default function C{i}(){{return null}}"
                 for i, p in enumerate(paths)}

        def reply(n):
            lines = [
                "CURRENT :: old behavior",
                "GAP :: requested behavior missing",
                "CAUSE :: these four existing owners participate in the behavior",
            ]
            for p in paths[:n]:
                lines.append(f"EVIDENCE :: {p} :: inspected owner {p}")
            lines.append("SUMMARY :: coordinated update")
            for p in paths:
                lines.append(f"FILE :: edit :: {p} :: client :: update proven owner")
            lines += ["VERIFY :: all four owners expose the requested behavior",
                      "CONFIDENCE :: high", "DONE"]
            return "\n".join(lines)

        class Arch:
            PKG_NAME_RE = re.compile(r".*")
            NODE_BUILTINS = set()
            def __init__(self):
                self.files = dict(files)
                self.convo = []
                self._workspace_tool_cache = {}
                self.replies = [reply(1), reply(2), reply(3), reply(4)]
                self.calls = 0
                self.project_dir = Path(".")
            def _stream(self, convo, sink, **kwargs):
                self.calls += 1
                sink(self.replies.pop(0))

        analyzer = SimpleNamespace(_budget_chars=lambda: 200000)
        arch = Arch()
        agent = FeaturesAgent(arch, analyzer=analyzer)
        spec = agent._plan("sys", "request", None, "Feature planning", mode="feature")
        self.assertEqual(arch.calls, 4)
        self.assertEqual(spec.paths(), set(paths))
        self.assertEqual(len(spec.context["evidence"]), 4)

class StreamProtocolSanitizerTests(unittest.TestCase):
    def test_unterminated_file_path_close_is_not_persisted_as_jsx(self):
        from agents.architect_core import FileStreamParser
        ended = []
        parser = FileStreamParser(lambda _x: None, lambda _p: None, lambda _x: None,
                                  lambda p, c: ended.append((p, c)))
        parser.feed('<write_file path="components/CheckoutClientForm.jsx">\n'
                    'export default function CheckoutClientForm(){return <form/>}\n'
                    '</components/CheckoutClientForm.jsx>')
        parser.close()
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0][0], 'components/CheckoutClientForm.jsx')
        self.assertNotIn('</components/CheckoutClientForm.jsx>', ended[0][1])
        self.assertTrue(ended[0][1].rstrip().endswith('}'))
