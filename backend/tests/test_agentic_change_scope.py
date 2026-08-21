import re
import unittest
from types import SimpleNamespace

from agents.features import FeaturesAgent
from agents.bugfixer import BugFixerAgent, RUNTIME_MAX_FILES


class AgenticChangeScopeTests(unittest.TestCase):
    def _planner(self):
        agent = object.__new__(FeaturesAgent)
        agent.arch = SimpleNamespace(
            PKG_NAME_RE=re.compile(r"^(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+$", re.I),
            NODE_BUILTINS=set(),
        )
        return agent

    def test_large_feature_plan_is_not_truncated_by_file_or_package_count(self):
        files = [
            f"FILE :: edit :: app/feature-{i}/page.jsx :: server :: required {i}"
            for i in range(14)
        ]
        extras = [
            "FILE :: edit :: app/globals.css :: server :: theme support",
            "FILE :: edit :: middleware.js :: server :: route policy",
        ]
        packages = [f"PACKAGE :: pkg-{i}" for i in range(5)]
        reply = "\n".join([
            "SUMMARY :: large cross-cutting feature", *packages, *files, *extras, "DONE"
        ])
        spec = self._planner()._parse(reply)
        self.assertEqual(len(spec.files), 16)
        self.assertEqual(len(spec.packages), 5)
        self.assertIn("app/globals.css", spec.paths())
        self.assertIn("middleware.js", spec.paths())

    def test_feature_plan_keeps_path_safety_without_complexity_gate(self):
        reply = "\n".join([
            "SUMMARY :: safe paths",
            "FILE :: edit :: ../server.py :: server :: escape",
            "FILE :: edit :: node_modules/x/index.js :: server :: dependency",
            "FILE :: edit :: styles/theme.css :: server :: valid style",
            "FILE :: edit :: components/Card.tsx :: client :: valid component",
            "DONE",
        ])
        spec = self._planner()._parse(reply)
        self.assertEqual(spec.paths(), {"styles/theme.css", "components/Card.tsx"})


    def test_large_feature_apply_writes_every_planned_file(self):
        class Cmd:
            def run(self, _cmd):
                return "ok"

        class Analyzer:
            def __init__(self, arch):
                self.arch = arch
                self.cmd = Cmd()
            def source_files(self):
                return dict(self.arch.files)
            def _budget_chars(self):
                return 200_000

        class Arch:
            PKG_NAME_RE = re.compile(r".*")
            NODE_BUILTINS = set()
            def __init__(self):
                self.project_dir = "."
                self.files = {f"app/f{i}/page.jsx": f"export default function P{i}(){{return <main>{i}</main>}}" for i in range(12)}
                self.convo = []
                self.written = []
                self._workspace_tool_cache = {}
            def _builder_sys(self):
                return "builder"
            def _stream(self, _convo, sink, temperature=0.35, model=None):
                for path in list(self.files):
                    sink(f'<write_file path="{path}">{self.files[path]}\n// changed</write_file>')
            def write_file(self, path, content):
                self.files[path] = content
                if path not in self.written:
                    self.written.append(path)
                return True
            def run_requested_commands(self, _raw):
                return []
            def repair_missing_imports(self):
                pass
            def apply_next_fixes(self):
                pass
            def sync_dependencies(self):
                pass

        from agents.features_common import FeatureSpec
        arch = Arch()
        agent = FeaturesAgent(arch, analyzer=Analyzer(arch))
        spec = FeatureSpec(files=[
            {"path": p, "action": "edit", "kind": "server", "why": "large change"}
            for p in list(arch.files)
        ])
        count = agent.apply("change all feature pages coherently", spec)
        self.assertEqual(count, 12)
        self.assertEqual(len(spec.written), 12)

    def test_runtime_repair_has_no_file_count_ceiling(self):
        self.assertIsNone(RUNTIME_MAX_FILES)
        fixer = object.__new__(BugFixerAgent)
        self.assertTrue(fixer.runtime_editable("app/cart/page.jsx"))
        self.assertTrue(fixer.runtime_editable("components/CartClient.tsx"))
        self.assertTrue(fixer.runtime_editable("styles/cart.css"))
        self.assertTrue(fixer.runtime_editable("lib/auth.js"))
        self.assertFalse(fixer.runtime_editable("../server.py"))
        self.assertFalse(fixer.runtime_editable("node_modules/x/index.js"))


if __name__ == "__main__":
    unittest.main()
