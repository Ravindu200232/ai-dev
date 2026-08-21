import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.exports import BrokenImport
from agents.features import FeaturesAgent
from agents.features_common import FeatureSpec


class BrokenImportCompatibilityTests(unittest.TestCase):
    def test_broken_import_is_constructible_after_module_split(self):
        item = BrokenImport(importer="app/page.jsx", line=3, name="x",
                            module="lib/a.js", spec="@/lib/a", available=["y"])
        self.assertEqual(item.importer, "app/page.jsx")
        self.assertIn("imports { x }", item.message())


class FeatureWriterIsolationTests(unittest.TestCase):
    def test_feature_apply_does_not_run_global_next_cleanup(self):
        class Analyzer:
            def __init__(self, arch):
                self.arch = arch
                self.cmd = SimpleNamespace(run=lambda _cmd: "ok")
            def source_files(self): return dict(self.arch.files)
            def _budget_chars(self): return 100000

        class Arch:
            PKG_NAME_RE = re.compile(r".*")
            NODE_BUILTINS = set()
            def __init__(self):
                self.project_dir = Path(".")
                self.files = {"app/page.jsx": "export default function Page(){return <main/>}"}
                self.convo = []
                self._workspace_tool_cache = {}
                self.synced = 0
            def _builder_sys(self): return "builder"
            def _stream(self, _convo, sink, **_kwargs):
                sink('<write_file path="app/page.jsx">export default function Page(){return <main>new</main>}</write_file>')
            def write_file(self, path, content):
                self.files[path] = content
                return True
            def run_requested_commands(self, _raw): return []
            def repair_missing_imports(self):
                raise AssertionError("feature must not run global import generation")
            def apply_next_fixes(self):
                raise AssertionError("feature must not rewrite unrelated Next files")
            def sync_dependencies(self): self.synced += 1

        arch = Arch()
        agent = FeaturesAgent(arch, analyzer=Analyzer(arch))
        spec = FeatureSpec(files=[{"path":"app/page.jsx", "action":"edit",
                                  "kind":"server", "why":"proven owner"}])
        self.assertEqual(agent.apply("change page", spec), 1)
        self.assertEqual(arch.synced, 1)


class FeatureTransactionTests(unittest.TestCase):
    def test_rollback_restores_modified_files_and_deletes_new_files(self):
        import server
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app").mkdir()
            (root / "app/page.jsx").write_text("OLD", encoding="utf-8")
            (root / "package.json").write_text('{"dependencies":{}}', encoding="utf-8")
            arch = SimpleNamespace(files={"app/page.jsx":"OLD", "package.json":'{"dependencies":{}}'},
                                   plan_md="old plan", convo=[{"role":"user","content":"old"}])
            tx = server._capture_feature_transaction(arch, root)

            (root / "app/page.jsx").write_text("NEW", encoding="utf-8")
            (root / "app/new").mkdir()
            (root / "app/new/page.jsx").write_text("NEW ROUTE", encoding="utf-8")
            arch.files["app/page.jsx"] = "NEW"
            arch.files["app/new/page.jsx"] = "NEW ROUTE"
            arch.plan_md = "new plan"
            arch.convo.append({"role":"assistant","content":"feature"})

            restored = server._restore_feature_transaction(arch, root, tx)
            self.assertEqual((root / "app/page.jsx").read_text(), "OLD")
            self.assertFalse((root / "app/new/page.jsx").exists())
            self.assertIn("app/new/page.jsx", restored)
            self.assertEqual(arch.plan_md, "old plan")
            self.assertEqual(len(arch.convo), 1)

    def test_only_new_or_feature_related_serious_findings_are_stabilized(self):
        import server
        old = server.Finding("major", "INERT_CONTROL", "old issue", path="app/old.jsx")
        new = server.Finding("blocker", "DEAD_ENDPOINT", "new /api/cart has no handler",
                             path="app/shopping-cart/page.jsx")
        unrelated_runtime = server.Finding("blocker", "RUNTIME_EXCEPTION", "old admin blew up",
                                           path="app/admin/page.jsx")
        baseline = server.AnalyzerReport(); baseline.findings = [old]
        report = server.AnalyzerReport(); report.findings = [old, new, unrelated_runtime]
        got = server._feature_related_findings(
            report, server._feature_baseline_keys(baseline),
            {"app/shopping-cart/page.jsx"})
        self.assertEqual([f.code for f in got], ["DEAD_ENDPOINT"])


class FeatureStabilizationLoopTests(unittest.TestCase):
    def test_live_regression_is_repaired_then_reobserved(self):
        import server
        bad = server.AnalyzerReport()
        bad.findings = [server.Finding("blocker", "DEAD_ENDPOINT",
                                      "new endpoint missing", path="app/page.jsx")]
        clean = server.AnalyzerReport()
        arch = SimpleNamespace(files={"app/page.jsx":"new"})
        with patch.object(server, "_observe_feature_upgrade",
                          side_effect=[(bad, "trace"), (clean, "")]), \
             patch.object(server, "repair_findings", return_value=1):
            ok, writes, _ = server._stabilize_feature_upgrade(
                arch, Path("."), SimpleNamespace(), baseline_keys=set(),
                before_files={"app/page.jsx":"old"}, db_ok=True)
        self.assertTrue(ok)
        self.assertEqual(writes, 1)

    def test_unrepairable_live_regression_is_not_declared_success(self):
        import server
        bad = server.AnalyzerReport()
        bad.findings = [server.Finding("blocker", "DEAD_ENDPOINT",
                                      "new endpoint missing", path="app/page.jsx")]
        arch = SimpleNamespace(files={"app/page.jsx":"new"})
        with patch.object(server, "_observe_feature_upgrade", return_value=(bad, "trace")), \
             patch.object(server, "repair_findings", return_value=0):
            ok, writes, _ = server._stabilize_feature_upgrade(
                arch, Path("."), SimpleNamespace(), baseline_keys=set(),
                before_files={"app/page.jsx":"old"}, db_ok=True)
        self.assertFalse(ok)
        self.assertEqual(writes, 0)


if __name__ == "__main__":
    unittest.main()
