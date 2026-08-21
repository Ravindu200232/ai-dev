import unittest

from agents.analyzer import AnalyzerAgent
from agents.architect import ArchitectAgent, FileStreamParser
from agents.builder import BuilderAgent
from agents.bugfixer import BugFixerAgent
from agents.features import FeaturesAgent
from agents.mongo import MongoManager
from agents.tester import TesterAgent as AgentTester
from agents.exports import parse_exports, parse_imports, check_syntax
from qa_agent.author import UnitTestAuthor
from qa_agent.debugger import AgenticE2EDebugger, DebugNotebook
from qa_agent.e2e import E2EAgent
from qa_agent.harness import TestHarness as Harness
from qa_agent.session import QASession


class RefactorFacadeTests(unittest.TestCase):
    def test_public_agent_methods_survive_split(self):
        expectations = {
            AnalyzerAgent: ("scan",),
            ArchitectAgent: ("run", "_stream"),
            BuilderAgent: ("build",),
            BugFixerAgent: ("fix_runtime",),
            UnitTestAuthor: ("write_for",),
            E2EAgent: ("run",),
            Harness: ("materialise",),
            AgenticE2EDebugger: ("investigate",),
            FeaturesAgent: ("plan_feature", "apply", "run"),
            AgentTester: ("test", "_run_browser_tests"),
            MongoManager: ("ensure_running", "reset_project_db"),
            QASession: ("bind", "drain", "write_test_file"),
        }
        for cls, names in expectations.items():
            for name in names:
                self.assertTrue(hasattr(cls, name), f"{cls.__name__}.{name} missing")

    def test_exports_helpers_still_execute_after_split(self):
        exports = parse_exports("export const x = 1; export default function A() {}")
        self.assertIn("x", exports.named)
        self.assertTrue(exports.has_default)
        self.assertEqual(len(parse_imports("import A, { x } from './a';")), 1)

    def test_public_support_types_survive_split(self):
        self.assertTrue(callable(FileStreamParser))
        self.assertTrue(callable(DebugNotebook))
        self.assertTrue(callable(parse_exports))
        self.assertTrue(callable(parse_imports))
        self.assertTrue(callable(check_syntax))


if __name__ == "__main__":
    unittest.main()


class RefactorDecoratorCompatibilityTests(unittest.TestCase):
    def test_mongo_path_helpers_remain_properties(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from agents.mongo import MongoManager

        with TemporaryDirectory() as td:
            mongo = MongoManager(home=Path(td))
            self.assertIsInstance(mongo.bin_dir, Path)
            self.assertIsInstance(mongo.data_dir, Path)
            self.assertIsInstance(mongo.pid_file, Path)
            self.assertEqual(mongo.bin_dir, Path(td) / "bin")
            # Regression for /settings -> MONGO.status(): this must not attempt
            # Path / bound-method after the monolith is split into mixins.
            status = mongo.status()
            self.assertIn("downloaded", status)
            self.assertIn("running", status)

    def test_static_and_property_descriptors_survive_split(self):
        from agents.architect import ArchitectAgent
        from agents.analyzer import AnalyzerAgent
        from agents.tester import TesterAgent as AgentTester
        from qa_agent.author import UnitTestAuthor
        from qa_agent.debugger import AgenticE2EDebugger
        from qa_agent.e2e import E2EAgent
        from qa_agent.harness import TestHarness as Harness
        from qa_agent.session import QASession

        static_methods = [
            (ArchitectAgent, "_source_requirements"),
            (AnalyzerAgent, "_post_json"),
            (AnalyzerAgent, "_norm"),
            (AgentTester, "_dynamic_pattern_re"),
            (UnitTestAuthor, "_failure_signature"),
            (AgenticE2EDebugger, "_clean_path"),
            (E2EAgent, "_render"),
            (E2EAgent, "_locator"),
            (Harness, "missing_packages"),
            (QASession, "model_for"),
        ]
        for cls, name in static_methods:
            raw = next(base.__dict__[name] for base in cls.__mro__ if name in base.__dict__)
            self.assertIsInstance(raw, staticmethod, f"{cls.__name__}.{name} lost @staticmethod")

        raw = next(base.__dict__["mcp_report"] for base in AgentTester.__mro__
                   if "mcp_report" in base.__dict__)
        self.assertIsInstance(raw, property, "TesterAgent.mcp_report lost @property")

class RefactorClassStateCompatibilityTests(unittest.TestCase):
    def test_e2e_class_level_contracts_survive_split(self):
        import re
        from qa_agent.e2e import E2EAgent

        expected = (
            "_ARROW", "_BULLET", "_TESTID_RE", "_SIGNED_OUT",
            "_SITE_RES", "SWEEP_CAP",
        )
        for name in expected:
            self.assertTrue(hasattr(E2EAgent, name), f"E2EAgent.{name} missing")
        self.assertIsInstance(E2EAgent._TESTID_RE, re.Pattern)
        self.assertEqual(E2EAgent.SWEEP_CAP, 25)
        self.assertIn("anonymous", E2EAgent._SIGNED_OUT)

    def test_e2e_testid_discovery_executes_after_split(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from qa_agent.e2e import E2EAgent

        with TemporaryDirectory() as td:
            arch = SimpleNamespace(
                project_dir=Path(td),
                files={
                    "app/page.jsx": '<main data-testid="shop-root" />',
                    "components/BuyButton.jsx":
                        "export default () => <button data-testid='buy-now'>Buy</button>",
                },
                plan={},
            )
            agent = E2EAgent(arch, Path(td))
            self.assertEqual(agent.testids_in_app(), ["shop-root", "buy-now"])

    def test_analyzer_semantic_parser_survives_split(self):
        import re
        from agents.analyzer import AnalyzerAgent

        self.assertTrue(hasattr(AnalyzerAgent, "PROMISE_RE"))
        self.assertIsInstance(AnalyzerAgent.PROMISE_RE, re.Pattern)
        sample = "PROMISE: customer can buy\nFILE: app/cart/page.jsx\nMISSING: submit path"
        m = AnalyzerAgent.PROMISE_RE.search(sample)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "app/cart/page.jsx")

class AnalyzerLintRepairRegressionTests(unittest.TestCase):
    def test_major_lint_finding_is_writable_and_boundary_prompt_is_specific(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from agents.analyzer_common import AnalyzerReport, Finding
        from agents.analyzer_repair_apply import AnalyzerRepairApplyMixin

        class FakeArch:
            def __init__(self, root):
                self.project_dir = Path(root)
                self.plan = {}
                self.files = {"app/shopping-cart/page.jsx": "export default function Page(){ return <main/> }"}
                self.written = {}
                self.prompt = ""

            def _builder_sys(self):
                return "builder"

            def _stream(self, convo, sink, temperature=0.4):
                self.prompt = convo[-1]["content"]
                sink('<write_file path="app/shopping-cart/page.jsx">export default function Page(){ return <main/> }</write_file>')

            def write_file(self, path, content):
                self.written[path] = content
                self.files[path] = content
                return True

            def _capability_ledger(self, _paths):
                return ""

            def _data_ledger(self):
                return ""

            def _contract_ledger(self, _paths):
                return ""

        class FakeRepair(AnalyzerRepairApplyMixin):
            def __init__(self, arch):
                self.arch = arch
                self.project_dir = arch.project_dir
                self._files_cache = None

            def source_files(self):
                return dict(self.arch.files)

            def enumerate_routes(self):
                return {"/shopping-cart": {"file": "app/shopping-cart/page.jsx", "kind": "page"}}

            def plan_text(self):
                return ""

            def inventory(self):
                return "app/shopping-cart/page.jsx"

            def _log(self, *_args):
                pass

            def _fire(self, *_args):
                pass

        with TemporaryDirectory() as td:
            arch = FakeArch(td)
            agent = FakeRepair(arch)
            report = AnalyzerReport(findings=[Finding(
                "major", "LINT",
                "app/shopping-cart/page.jsx: has onRemove but is a Server Component",
                path="app/shopping-cart/page.jsx")])
            self.assertEqual(agent.repair(report), 1)
            self.assertIn("app/shopping-cart/page.jsx", arch.written)
            self.assertIn("SERVER/CLIENT BOUNDARY RULE", arch.prompt)
            self.assertIn("ObjectId values to strings", arch.prompt)
