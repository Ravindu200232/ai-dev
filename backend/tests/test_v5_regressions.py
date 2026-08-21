import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from agents.analyzer_code import AnalyzerCodeMixin
from agents.analyzer_common import AnalyzerReport, Finding
from agents.analyzer_repair_apply import AnalyzerRepairApplyMixin
from agents.commands import validate
from qa_agent.runner import VitestRunner, TARGET_COMMAND_CHARS


class DynamicRepairScopeTests(unittest.TestCase):
    def test_direct_new_import_dependency_is_written_with_primary_repair(self):
        class Arch:
            def __init__(self, root):
                self.project_dir = Path(root)
                self.plan = {}
                self.files = {
                    "app/shopping-cart/page.jsx":
                        "export default function Page(){return <main/>}",
                }
                self.written = {}
            def _builder_sys(self): return "builder"
            def _stream(self, convo, sink, temperature=0.4):
                # Intentionally send the dependency BEFORE the importing page;
                # streaming allowlists used to reject this exact ordering.
                sink('<write_file path="components/CartItem.jsx">'
                     "export default function CartItem(){return <div/>}"
                     '</write_file>')
                sink('<write_file path="app/shopping-cart/page.jsx">'
                     "import CartItem from '@/components/CartItem'; "
                     "export default function Page(){return <CartItem/>}"
                     '</write_file>')
            def write_file(self, path, content):
                self.written[path] = content
                self.files[path] = content
                return True
            def _capability_ledger(self, _): return ""
            def _data_ledger(self): return ""
            def _contract_ledger(self, _): return ""

        class Repair(AnalyzerRepairApplyMixin):
            def __init__(self, arch):
                self.arch = arch
                self.project_dir = arch.project_dir
                self._files_cache = None
            def source_files(self): return dict(self.arch.files)
            def enumerate_routes(self):
                return {"/shopping-cart": {"file": "app/shopping-cart/page.jsx", "kind": "page"}}
            def plan_text(self): return ""
            def inventory(self): return "app/shopping-cart/page.jsx"
            def _log(self, *_): pass
            def _fire(self, *_): pass

        with TemporaryDirectory() as td:
            arch = Arch(td)
            repair = Repair(arch)
            report = AnalyzerReport(findings=[Finding(
                "major", "LINT", "repair cart boundary",
                path="app/shopping-cart/page.jsx")])
            self.assertEqual(repair.repair(report), 2)
            self.assertIn("app/shopping-cart/page.jsx", arch.written)
            self.assertIn("components/CartItem.jsx", arch.written)

    def test_missing_local_import_is_a_prebuild_blocker(self):
        class Detector(AnalyzerCodeMixin):
            def code_files(self):
                return {
                    "app/shopping-cart/page.jsx":
                        "import CartItem from '@/components/CartItem'; export default function Page(){return <CartItem/>}",
                }
        findings = Detector().missing_local_imports()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "MISSING_LOCAL_IMPORT")
        self.assertEqual(findings[0].severity, "blocker")
        self.assertIn("components/CartItem.jsx", findings[0].extra)


class VitestScalingTests(unittest.TestCase):
    @staticmethod
    def _report(paths):
        suites = []
        for path in paths:
            suites.append({
                "name": str(path), "status": "passed",
                "assertionResults": [{"fullName": f"{path} works", "status": "passed"}],
            })
        return {"testResults": suites, "success": True}

    def test_large_targeted_rerun_is_batched_not_refused(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            calls = []
            class Cmd:
                def run(self, command, timeout=None):
                    calls.append(command)
                    rel = re.search(r"--outputFile=([^ ]+)", command).group(1)
                    middle = command.split("npx vitest run", 1)[1].split("--reporter=json", 1)[0].strip()
                    paths = middle.split() if middle else []
                    out = root / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(VitestScalingTests._report(paths)), encoding="utf-8")
                    return SimpleNamespace(output="", code=0)
            paths = [f"tests/unit/components/VeryLongFeatureComponent{i:03d}Name.test.jsx" for i in range(90)]
            runner = VitestRunner(root, cmd=Cmd())
            passed, failures, ok = runner.run(paths=paths)
            self.assertTrue(ok)
            self.assertEqual(passed, len(paths))
            self.assertEqual(failures, [])
            self.assertGreater(len(calls), 1)
            self.assertTrue(all(len(c) <= TARGET_COMMAND_CHARS for c in calls))

    def test_targeted_no_report_falls_back_to_full_suite(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            calls = []
            class Cmd:
                def run(self, command, timeout=None):
                    calls.append(command)
                    if len(calls) == 1:
                        return SimpleNamespace(output="simulated missing targeted report", code=1)
                    rel = re.search(r"--outputFile=([^ ]+)", command).group(1)
                    out = root / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(VitestScalingTests._report(["tests/unit/all.test.jsx"])), encoding="utf-8")
                    return SimpleNamespace(output="", code=0)
            runner = VitestRunner(root, cmd=Cmd())
            passed, failures, ok = runner.run(paths=["tests/unit/a.test.jsx"])
            self.assertTrue(ok)
            self.assertEqual(passed, 1)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("tests/unit/a.test.jsx", calls[1])

    def test_command_validator_no_longer_rejects_normal_large_test_command(self):
        cmd = "npx vitest run " + " ".join(f"tests/unit/t{i}.test.jsx" for i in range(20))
        argv, reason = validate(cmd)
        self.assertIsNone(reason)
        self.assertIsNotNone(argv)


class PretestConvergenceTests(unittest.TestCase):
    def test_flow_convergence_rechecks_until_evidence_closes(self):
        import server
        calls = {"scan": 0, "repair": 0}

        class Analyzer:
            def scan(self):
                calls["scan"] += 1
                report = AnalyzerReport()
                if calls["scan"] <= 2:
                    report.findings = [Finding(
                        "major", "UNBUILT_PROMISE",
                        f"still disconnected {calls['scan']}", path="app/page.jsx")]
                return report
            def unbuilt_promises(self, max_reads=8): return []

        def fake_repair(*args, **kwargs):
            calls["repair"] += 1
            return 1

        arch = SimpleNamespace(files={})
        with TemporaryDirectory() as td, \
             patch.object(server, "repair_findings", side_effect=fake_repair), \
             patch.object(server, "ephase", lambda *a, **k: None), \
             patch.object(server, "eprog", lambda *a, **k: None), \
             patch.object(server, "elog", lambda *a, **k: None), \
             patch.object(server, "emit", lambda *a, **k: None):
            report, clean, conclusive, written = server.pretest_flow_convergence(
                arch, Path(td), Analyzer(), build_ok=True)
        self.assertTrue(clean)
        self.assertTrue(conclusive)
        self.assertEqual(written, 2)
        self.assertEqual(calls["repair"], 2)


if __name__ == "__main__":
    unittest.main()
