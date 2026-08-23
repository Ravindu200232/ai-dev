"""Regression coverage for repeated analyzer/pre-test rewrites."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import tempfile
import unittest

from agents.builder.orchestration.writes import ArchitectWriteMixin
from agents.gates.analyzer_common import AnalyzerReport, Finding
from agents.gates.analyzer_repair_run import AnalyzerRepairRunMixin


def inert_finding():
    return Finding(
        "major", "INERT_CONTROL",
        "renders 'Add Item' as a button with no click handler",
        path="app/admin/catalogue/page.jsx",
    )


class _Writer(ArchitectWriteMixin):
    stack = "vite"

    def __init__(self, root):
        self.project_dir = Path(root)
        self.files = {}
        self.events = []
        self.write_seq = 0

    def _safe_path(self, rel):
        return self.project_dir / rel

    def _fire(self, *event):
        self.events.append(event)

    def _log(self, *_args):
        pass

    def _drop_tests_for(self, *_args):
        raise AssertionError("an identical file must not invalidate tests")


class IdenticalWriteTests(unittest.TestCase):
    def test_identical_model_output_is_not_counted_or_announced_as_a_write(self):
        with tempfile.TemporaryDirectory() as td:
            writer = _Writer(td)
            path = Path(td) / "src" / "App.jsx"
            path.parent.mkdir(parents=True)
            path.write_text("export default function App() {}\n", encoding="utf-8")
            writer.files["src/App.jsx"] = path.read_text(encoding="utf-8")

            self.assertFalse(writer.write_file(
                "src/App.jsx", "export default function App() {}"))
            self.assertEqual(0, writer.write_seq)
            self.assertEqual([], writer.events)


class _AnalyzerRun(AnalyzerRepairRunMixin):
    def __init__(self, root):
        self.project_dir = Path(root)
        self.arch = SimpleNamespace()
        self._exhausted_findings = []
        self.repairs = 0

    def _fire(self, *_args):
        pass

    def _log(self, *_args):
        pass

    def scan(self):
        report = AnalyzerReport()
        report.findings = [inert_finding()]
        return report

    def unresolved_packages(self):
        return []

    def repair(self, _report):
        self.repairs += 1
        return 1


class CrossStageRepairTests(unittest.TestCase):
    def test_a_finding_that_survives_its_repair_is_carried_as_exhausted(self):
        with tempfile.TemporaryDirectory() as td:
            analyzer = _AnalyzerRun(td)
            analyzer.run(semantic=False)
            self.assertEqual(1, analyzer.repairs)
            self.assertEqual(["INERT_CONTROL"],
                             [f.code for f in analyzer._exhausted_findings])

    def test_pretest_reports_an_exhausted_finding_without_rewriting_it(self):
        server = importlib.import_module("server")
        old = {name: getattr(server, name) for name in (
            "elog", "ephase", "eprog", "repair_findings")}
        calls = []
        try:
            server._EXHAUSTED_FINDINGS.clear()
            server._park_exhausted([inert_finding()])
            server.elog = lambda *_args: None
            server.ephase = lambda *_args: None
            server.eprog = lambda *_args: None
            server.repair_findings = lambda *_args, **_kwargs: calls.append(1) or 1

            analyzer = SimpleNamespace(
                scan=lambda: AnalyzerReport(findings=[inert_finding()]),
                unbuilt_promises=lambda **_kwargs: [],
            )
            arch = SimpleNamespace(files={})
            with tempfile.TemporaryDirectory() as td:
                report, clean, conclusive, written = (
                    server.pretest_flow_convergence(
                        arch, Path(td), analyzer, build_ok=True))

            self.assertEqual([], calls)
            self.assertFalse(clean)
            self.assertTrue(conclusive)
            self.assertEqual(0, written)
            self.assertEqual(["INERT_CONTROL"],
                             [f.code for f in report.findings])
        finally:
            for name, value in old.items():
                setattr(server, name, value)
            server._EXHAUSTED_FINDINGS.clear()

    def test_builder_pipeline_resets_then_carries_exhaustion_per_run(self):
        source = Path("server_modules/agent/builder/pipeline.py").read_text(
            encoding="utf-8")
        self.assertIn("_EXHAUSTED_FINDINGS.clear()", source)
        self.assertIn("_park_exhausted(getattr(analyzer,", source)


if __name__ == "__main__":
    unittest.main()
